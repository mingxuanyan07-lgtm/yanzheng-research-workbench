"""Local-only integration tests; model and literature inputs are offline fixtures."""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import create_app
from fastapi.testclient import TestClient
from model_service import ModelService, ModelServiceError
from test_model_api import FakeModel, payload as direction_payload
from test_theory import card, proposal, evidence, model_call
import engine
import theory
import literature_service


class TheoryApiTest(unittest.TestCase):
    def setUp(self):
        self.root = BASE / "tests/.tmp"
        self.root.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(dir=self.root))
        self.database = self.directory / "theory.db"
        self.model = FakeModel()
        self.model.result = proposal()
        self.application = create_app(self.database, model_client=self.model)
        self.client = TestClient(self.application)

    def tearDown(self):
        self.client.close()
        assert self.directory.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.directory)

    def create(self, selected=False):
        response = self.client.post("/api/projects", json={"initial_idea": "研究函数列的收敛条件与连续性", "goal": "theory"})
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()
        if selected:
            self.application.state.store.update(item["id"], engine.generate_directions)
            response = self.client.post(f'/api/projects/{item["id"]}/select', json={"topic_id": "conditions", "reason": "由研究者选择条件分析。"})
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()
        return item

    def save_card(self, item, value=None, version=0):
        response = self.client.post(f'/api/projects/{item["id"]}/theory/card', json={"card": value or card(), "expected_version": version})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def propose(self, item):
        return self.client.post(f'/api/projects/{item["id"]}/theory/propose', json={})

    def saved(self, item):
        response = self.client.get(f'/api/projects/{item["id"]}')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_explicit_proposal_review_and_accept_never_selects_or_executes(self):
        item = self.save_card(self.create(selected=True))
        original_map = copy.deepcopy(item["direction_map"])
        original_card = copy.deepcopy(item["theory"]["card"])
        self.assertEqual(self.model.calls, 0)
        self.client.get(f'/api/projects/{item["id"]}/theory')
        self.client.get("/api/status")
        self.assertEqual(self.model.calls, 0)
        response = self.propose(item)
        self.assertEqual(response.status_code, 200, response.text)
        proposed = response.json()
        self.assertEqual(proposed["direction_map"], original_map)
        self.assertEqual(proposed["theory"]["card"], original_card)
        self.assertEqual(proposed["theory"]["card_version"], 1)
        self.assertEqual(self.model.calls, 1)
        self.assertFalse(proposed.get("experiments"))
        pending_id = proposed["theory"]["pending_proposal"]["id"]
        response = self.client.post(f'/api/projects/{item["id"]}/theory/proposals/{pending_id}/accept', json={})
        self.assertEqual(response.status_code, 200, response.text)
        accepted = response.json()
        self.assertEqual(accepted["direction_map"], original_map)
        self.assertEqual(accepted["theory"]["card_version"], 2)
        self.assertEqual(accepted["theory"]["card"], proposal()["revised_card"])
        self.assertEqual(self.model.calls, 1)
        self.assertFalse(accepted.get("experiments"))
        self.assertIsNone(accepted["theory"]["pending_proposal"])

    def test_api_validation_unknown_project_and_stale_card_make_no_model_call(self):
        item = self.save_card(self.create())
        path = f'/api/projects/{item["id"]}/theory/card'
        for body in ({"card": card(), "expected_version": 1, "proven": True},
                     {"card": card(), "expected_version": True}, {"card": card()},
                     {"card": card(statement="x" * 100001), "expected_version": 1},
                     {"card": card(accepted=True), "expected_version": 1}):
            with self.subTest(body=str(body)[:100]):
                response = self.client.post(path, json=body)
                self.assertIn(response.status_code, (400, 422), response.text)
        response = self.client.post(path, json={"card": card(title="过期写入"), "expected_version": 0})
        self.assertIn(response.status_code, (400, 409), response.text)
        response = self.client.post(f'/api/projects/{item["id"]}/theory/propose', json={"formally_verified": True})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.post('/api/projects/missing/theory/propose', json={}).status_code, 404)
        self.assertEqual(self.saved(item)["theory"]["card"], card())
        self.assertEqual(self.model.calls, 0)

    def test_timeout_and_invalid_payload_preserve_card_and_existing_pending_then_allow_retry(self):
        item = self.save_card(self.create())
        response = self.propose(item)
        self.assertEqual(response.status_code, 200, response.text)
        original = response.json()
        old_pending = copy.deepcopy(original["theory"]["pending_proposal"])
        timeout = model_call("timeout-theory", "failed", error_code="timeout", usage=None,
                             summary="离线模拟超时；当前命题已保留。")
        for code, status in (("timeout", 504), ("invalid_json", 502)):
            failed = timeout | {"id": "failed-theory-" + code, "error_code": code}
            with self.subTest(error=code):
                with patch.object(self.model, "complete", side_effect=ModelServiceError(failed["summary"], status, failed)):
                    response = self.propose(item)
                self.assertEqual(response.status_code, status, response.text)
                saved = self.saved(item)
                self.assertEqual(saved["theory"]["card"], card())
                self.assertEqual(saved["theory"]["pending_proposal"], old_pending)
        self.model.result = {"revised_card": card(), "untrusted_provider_field": True}
        response = self.propose(item)
        self.assertEqual(response.status_code, 502, response.text)
        saved = self.saved(item)
        self.assertEqual(saved["theory"]["card"], card())
        self.assertEqual(saved["theory"]["pending_proposal"], old_pending)
        self.assertEqual(saved["model_calls"][-1]["error_code"], "invalid_theory")
        self.model.result = proposal()
        response = self.propose(item)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["theory"]["card"], card())
        self.assertNotEqual(response.json()["theory"]["pending_proposal"]["id"], old_pending["id"])

    def test_concurrent_card_edit_during_model_call_is_not_overwritten(self):
        item = self.save_card(self.create())
        updated = card(title="并发编辑后的命题", assumptions=["已由研究者修改假设。"])
        self.model.callback = lambda: self.application.state.store.update(item["id"], lambda current: theory.save_card(current, updated, 1))
        response = self.propose(item)
        self.assertEqual(response.status_code, 409, response.text)
        saved = self.saved(item)
        self.assertEqual(saved["theory"]["card"], updated)
        self.assertEqual(saved["theory"]["card_version"], 2)
        self.assertIsNone(saved["theory"]["pending_proposal"])
        self.assertEqual(saved["model_calls"][-1]["application_status"], "discarded_conflict")

    def test_zero_request_busy_theory_does_not_discard_real_direction_generation(self):
        item = self.create()
        self.application.state.store.update(item["id"], engine.generate_directions)
        item = self.save_card(item)
        self.model.result = direction_payload()
        busy = model_call("busy-theory", "failed", attempts=0, error_code="busy",
                          summary="已有模型调用正在进行，本次未发请求。")

        def simultaneous_theory_request():
            with patch.object(self.model, "complete", side_effect=ModelServiceError(busy["summary"], 409, busy)):
                self.assertEqual(self.propose(item).status_code, 409)

        self.model.callback = simultaneous_theory_request
        response = self.client.post(f'/api/projects/{item["id"]}/generate', json={})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["direction_map"]["provenance"]["generator"], "qwen")
        self.assertEqual(saved["theory"]["card"], card())
        self.assertIsNone(saved["theory"]["pending_proposal"])
        self.assertEqual(len(saved["model_calls"]), 1)

    def test_rejected_proposal_and_evidence_snapshots_survive_export_and_restart(self):
        item = self.save_card(self.create())
        self.application.state.store.update(item["id"], evidence)
        self.model.result = proposal("note-theory-1")
        response = self.propose(item)
        self.assertEqual(response.status_code, 200, response.text)
        pending_id = response.json()["theory"]["pending_proposal"]["id"]
        reject_path = f'/api/projects/{item["id"]}/theory/proposals/{pending_id}/reject'
        pending = copy.deepcopy(response.json()["theory"]["pending_proposal"])
        for invalid in ({"reason": "x" * 2001}, {"reason": {"command": "approve"}}, {"reason": "拒绝", "formally_verified": True}):
            self.assertEqual(self.client.post(reject_path, json=invalid).status_code, 422)
            self.assertEqual(self.saved(item)["theory"]["pending_proposal"], pending)
        reason = "该提议尚未充分交代定义域及量词，需要重新审查。"
        response = self.client.post(reject_path, json={"reason": reason})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        workspace = self.client.get(f'/api/projects/{item["id"]}/theory').json()
        self.assertEqual(workspace["card"], card())
        self.assertEqual(workspace["proposals"][-1]["id"], pending_id)
        self.assertEqual(workspace["proposals"][-1]["review_reason"], reason)
        self.assertEqual(workspace["proof_status"], "not_formally_verified")
        exported = self.client.get(f'/api/projects/{item["id"]}/theory/export?format=json')
        self.assertEqual(exported.json(), workspace)
        self.assertIn("attachment", exported.headers["content-disposition"])
        markdown = self.client.get(f'/api/projects/{item["id"]}/theory/export?format=md')
        self.assertEqual(markdown.status_code, 200, markdown.text)
        for value in ("Theorem fixture, p. 2", "v2 / 2026", "Offline theorem fixture"):
            self.assertIn(value, markdown.text)
        self.assertIn(reason, markdown.text)
        with TestClient(create_app(self.database, model_client=self.model)) as fresh:
            self.assertEqual(fresh.get(f'/api/projects/{item["id"]}').json(), saved)
            self.assertEqual(fresh.get(f'/api/projects/{item["id"]}/theory/export?format=json').json(), workspace)
        self.assertEqual(self.model.calls, 1)

    def test_without_key_is_honest_empty_state_and_retains_card_for_later_retry(self):
        item = self.save_card(self.create())
        absent = ModelService(env_file=self.directory / "absent.env", environ={})
        with TestClient(create_app(self.database, model_client=absent)) as client:
            workspace = client.get(f'/api/projects/{item["id"]}/theory').json()
            self.assertIsNone(workspace["pending_proposal"])
            response = client.post(f'/api/projects/{item["id"]}/theory/propose', json={})
            self.assertEqual(response.status_code, 409, response.text)
            saved = client.get(f'/api/projects/{item["id"]}').json()
            self.assertEqual(saved["theory"]["card"], card())
            self.assertIsNone(saved["theory"]["pending_proposal"])
            self.assertFalse(client.get("/api/status").json()["model_connected"])
            self.assertEqual(saved["model_calls"][-1]["attempts"], 0)

    def test_curated_example_creates_new_project_without_selection_reading_or_model_claim(self):
        existing = self.create()
        response = self.client.post("/api/theory/examples", json={"name": "uniform-continuity"})
        self.assertEqual(response.status_code, 201, response.text)
        sample = response.json()
        self.assertNotEqual(sample["id"], existing["id"])
        self.assertEqual(self.saved(existing), existing)
        self.assertIsNone(sample["direction_map"]["selected_topic_id"])
        self.assertFalse(sample["direction_map"]["provenance"]["live_search_performed"])
        self.assertFalse(sample["direction_map"]["provenance"]["external_model_api_called"])
        self.assertEqual(self.model.calls, 0)
        self.assertFalse(sample.get("model_calls"))
        for record in sample["literature_searches"]:
            self.assertEqual(literature_service.validate(record), [])
        notes = sample.get("reading_notes", [])
        self.assertTrue(notes)
        self.assertTrue(all(note.get("origin") == "curated_example" for note in notes))
        self.assertFalse(any(note.get("confirmed") is True and note.get("origin") == "user_attested" for note in notes))
        exported = self.client.get(f'/api/projects/{sample["id"]}/theory/export?format=json').text
        self.assertIn("curated_example", exported)
        self.assertEqual(self.client.post("/api/theory/examples", json={"name": "unknown-example"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
