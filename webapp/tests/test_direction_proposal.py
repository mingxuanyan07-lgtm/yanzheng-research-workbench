"""Gap B: direction model results are previews; only explicit accept applies them."""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import create_app
from fastapi.testclient import TestClient
from test_model_api import FakeModel, payload
import engine


def ready():
    return payload() | {"stage": "directions_ready", "questions": [], "assumptions": [],
                        "search_queries": ["randomized low rank approximation"], "revision_note": "依据已给约束形成候选，仍需文献核查。"}


class DirectionProposalTest(unittest.TestCase):
    def setUp(self):
        self.root = BASE / "tests/.tmp"
        self.root.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(dir=self.root))
        self.model = FakeModel()
        self.model.result = ready()
        self.app = create_app(self.directory / "test.db", model_client=self.model)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        assert self.directory.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.directory)

    def create(self):
        response = self.client.post("/api/projects", json={"initial_idea": "随机低秩逼近；比较误差和耗时。", "planning_mode": "skill"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def saved(self, project_id):
        return self.client.get(f"/api/projects/{project_id}").json()

    def pending(self, project_id):
        return self.saved(project_id)["workflow"]["pending_proposal"]

    def accept(self, project_id, proposal_id=None):
        proposal_id = proposal_id or self.pending(project_id)["id"]
        response = self.client.post(f"/api/projects/{project_id}/direction/proposals/{proposal_id}/accept", json={})
        return response

    def reject(self, project_id, proposal_id=None, reason=""):
        proposal_id = proposal_id or self.pending(project_id)["id"]
        return self.client.post(f"/api/projects/{project_id}/direction/proposals/{proposal_id}/reject",
                                json={"reason": reason})

    def test_preview_is_saved_with_context_and_does_not_mutate_map_selection_or_versions(self):
        project = self.create()
        pending = project["workflow"]["pending_proposal"]
        self.assertIsNotNone(pending)
        self.assertEqual(project["stage"], "clarifying")
        self.assertEqual(project["direction_map"]["topics"], [])
        self.assertIsNone(project["direction_map"]["selected_topic_id"])
        self.assertNotIn("direction_versions", project)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["payload"]["stage"], "directions_ready")
        self.assertEqual(len(pending["payload"]["topics"]), 3)
        self.assertEqual(pending["base_map"], project["direction_map"])
        self.assertEqual(len(pending["context_sha256"]), 64)
        self.assertEqual(len(pending["base_map_sha256"]), 64)
        self.assertEqual(len(pending["input_sha256"]), 64)
        self.assertIn("topics", pending["diff"])
        self.assertEqual(pending["diff"]["topics"]["before"], [])
        self.assertEqual(len(pending["diff"]["topics"]["after"]), 3)
        self.assertEqual(project["model_calls"][-1]["application_status"], "pending_review")
        self.assertEqual(project["workflow"]["state"], "proposal_ready")

    def test_accept_applies_once_and_records_version_and_keeps_recommendation_unselected(self):
        project = self.create()
        proposal_id = project["workflow"]["pending_proposal"]["id"]
        response = self.accept(project["id"])
        self.assertEqual(response.status_code, 200, response.text)
        accepted = response.json()
        self.assertEqual(accepted["stage"], "directions_ready")
        self.assertEqual(len(accepted["direction_map"]["topics"]), 3)
        self.assertEqual(accepted["direction_map"]["recommendation"]["topic_id"], "qwen-1")
        self.assertIsNone(accepted["direction_map"]["selected_topic_id"])
        self.assertIsNone(accepted["workflow"]["pending_proposal"])
        self.assertEqual(accepted["direction_versions"][-1]["reason"], "skill_workflow_proposal_accepted")
        self.assertEqual(accepted["direction_versions"][-1]["proposal_id"], proposal_id)
        self.assertEqual(accepted["direction_versions"][-1]["direction_map"]["topics"], [])
        accepted_entry = next(item for item in accepted["workflow"]["proposals"] if item["id"] == proposal_id)
        self.assertEqual(accepted_entry["status"], "accepted")
        self.assertEqual(accepted["model_calls"][-1]["application_status"], "applied")
        self.assertEqual(engine.validate(accepted["direction_map"]), [])
        # Repeating the same accept must not apply twice.
        self.assertEqual(self.accept(project["id"], proposal_id).status_code, 400)

    def test_reject_preserves_old_map_selection_versions_and_allows_a_new_preview(self):
        project = self.create()
        before = copy.deepcopy(self.saved(project["id"]))
        pending_id = before["workflow"]["pending_proposal"]["id"]
        response = self.reject(project["id"], pending_id, "先核对文献再决定。")
        self.assertEqual(response.status_code, 200, response.text)
        rejected = response.json()
        self.assertEqual(rejected["direction_map"], before["direction_map"])
        self.assertEqual(rejected["direction_map"]["selected_topic_id"], before["direction_map"]["selected_topic_id"])
        self.assertNotIn("direction_versions", rejected)
        self.assertIsNone(rejected["workflow"]["pending_proposal"])
        self.assertEqual(rejected["workflow"]["state"], rejected["direction_map"]["stage"])
        entry = next(item for item in rejected["workflow"]["proposals"] if item["id"] == pending_id)
        self.assertEqual(entry["status"], "rejected")
        self.assertEqual(entry["researcher_reason"], "先核对文献再决定。")
        self.assertEqual(rejected["model_calls"][-1]["application_status"], "rejected_by_user")
        # A later explicit request can still create a fresh pending preview.
        response = self.client.post(f'/api/projects/{project["id"]}/advance', json={"material": "先收窄到可复现基线。"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(response.json()["workflow"]["pending_proposal"])

    def test_new_request_supersedes_previous_pending_preview(self):
        project = self.create()
        first = self.pending(project["id"])["id"]
        response = self.client.post(f'/api/projects/{project["id"]}/advance', json={"material": "补充：只用公开合成数据。"})
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()
        second = current["workflow"]["pending_proposal"]["id"]
        self.assertNotEqual(first, second)
        history = {item["id"]: item for item in current["workflow"]["proposals"]}
        self.assertEqual(history[first]["status"], "superseded")
        self.assertEqual(history[second]["status"], "pending")
        self.assertEqual(self.accept(project["id"], first).status_code, 400)
        self.assertEqual(self.accept(project["id"], second).status_code, 200)

    def test_later_local_edit_invalidates_acceptance_without_erasing_pending_preview(self):
        project = self.create()
        pending_id = self.pending(project["id"])["id"]

        def append_note(current):
            current.setdefault("reading_notes", []).append({"id": "late-note", "run_id": "late-run", "source_id": "S001",
                                                            "level": "abstract_checked", "content": "后加笔记。",
                                                            "locator": "Abstract", "checked_at": engine.now(),
                                                            "confirmed": True, "origin": "user_attested"})
        self.app.state.store.update(project["id"], append_note)
        response = self.accept(project["id"], pending_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["status"], "stale_proposal")
        saved = self.saved(project["id"])
        self.assertIsNotNone(saved["workflow"]["pending_proposal"])
        self.assertEqual(saved["workflow"]["pending_proposal"]["id"], pending_id)
        self.assertEqual(saved["direction_map"]["topics"], [])
        # The stale preview can still be explicitly discarded.
        self.assertEqual(self.reject(project["id"], pending_id).status_code, 200)

    def test_topic_selection_after_preview_invalidates_acceptance(self):
        project = self.create()
        self.assertEqual(self.accept(project["id"]).status_code, 200)
        response = self.client.post(f'/api/projects/{project["id"]}/advance', json={"material": "依据公开文献修订。"})
        pending_id = response.json()["workflow"]["pending_proposal"]["id"]
        topic_id = response.json()["direction_map"]["topics"][0]["id"]
        self.assertEqual(self.client.post(f'/api/projects/{project["id"]}/select',
                                          json={"topic_id": topic_id, "reason": "先按当前候选做首轮验证。"}).status_code, 200)
        response = self.accept(project["id"], pending_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["status"], "stale_proposal")
        saved = self.saved(project["id"])
        self.assertEqual(saved["stage"], "topic_selected")
        self.assertEqual(saved["direction_map"]["selected_topic_id"], topic_id)
        self.assertIsNotNone(saved["workflow"]["pending_proposal"])


if __name__ == "__main__":
    unittest.main()
