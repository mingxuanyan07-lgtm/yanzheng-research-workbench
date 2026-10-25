"""Pure object checks only: no ASGI requests, sockets, model network or subprocess."""
import importlib.util
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("yanzheng_cloud_boundary_target", DEPLOY / "cloud_app.py")
cloud = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cloud
spec.loader.exec_module(cloud)


class Model:
    def __init__(self):
        self.calls = 0
        self.last = None

    def status(self):
        return {"configured": True, "model": "test-model", "state": "configured", "last_call": self.last}

    def complete(self, messages):
        self.calls += 1
        self.last = {"id": uuid.uuid4().hex, "attempts": 1}
        return {"fixture": True}, self.last.copy()

    def complete_for_theory(self, messages):
        return self.complete(messages)

    def record_result(self, record):
        self.last = record.copy()


class CloudBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.base = DEPLOY / "tests/.tmp-boundaries"
        self.base.mkdir(exist_ok=True)
        self.directory = self.base / uuid.uuid4().hex
        self.directory.mkdir()
        self.clock = [1000]
        self.models = []
        self.guard = patch("subprocess.Popen", side_effect=AssertionError("No subprocess permitted"))
        self.guard.start()
        self.app = cloud.build_app(public_origin="https://demo.example.test", runtime_root=self.directory,
            environ={"YANZHENG_DEMO_MODEL_ENABLED": "1"}, model_factory=self.factory,
            clock=lambda: self.clock[0])
        self.a = self.app._new_session()
        self.b = self.app._new_session()

    def tearDown(self):
        self.guard.stop()
        self.assertEqual(self.directory.resolve().parent, self.base.resolve())
        shutil.rmtree(self.directory)

    def factory(self):
        result = Model()
        self.models.append(result)
        return result

    def test_module_is_lazy_and_all_active_stores_are_isolated(self):
        self.assertIsNone(cloud.app.application)
        self.assertTrue(self.app.public_app.state.store.path.is_relative_to(self.directory))
        for item in self.app.sessions.values():
            self.assertTrue(item.application.state.store.path.is_relative_to(self.directory))
        self.assertNotEqual(self.app.sessions[self.a].directory, self.app.sessions[self.b].directory)

    def test_session_token_never_enters_filesystem_path(self):
        self.assertRegex(self.a, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(self.a, self.b)
        self.assertNotIn(self.a, str(self.app.sessions[self.a].directory))
        self.assertIsNone(self.app._token({cloud.SESSION_HEADER: [b"../../research.db"]}))
        self.assertIsNone(self.app._token({cloud.SESSION_HEADER: [self.a.encode(), self.b.encode()]}))

    def test_independent_stores_cannot_read_each_others_project(self):
        source = self.app.core.engine.create_project({"initial_idea": "随机低秩逼近",
            "goal": "comparison", "resources": "laptop", "time_horizon": "week", "context": ""})
        store_a = self.app.sessions[self.a].application.state.store
        store_b = self.app.sessions[self.b].application.state.store
        store_a.create(source)
        self.assertEqual(len(store_a.list()), 1)
        self.assertEqual(store_b.list(), [])
        with self.assertRaises(self.app.core.ProjectNotFound):
            store_b.get(source["id"])

    def test_model_status_is_per_session_and_does_not_call_provider(self):
        one = self.app.sessions[self.a].model
        two = self.app.sessions[self.b].model
        one.complete([{"role": "user", "content": "public fixture"}])
        self.assertIsNotNone(one.status()["last_call"])
        self.assertIsNone(two.status()["last_call"])
        self.assertEqual(self.models[1].calls, 0)

    def test_default_switch_blocks_even_configured_model(self):
        model = cloud.BoundedModel(Model(), self.app.core.ModelServiceError)
        self.assertFalse(model.status()["configured"])
        with self.assertRaises(self.app.core.ModelServiceError) as failure:
            model.complete([{"role": "user", "content": "public"}])
        self.assertEqual(failure.exception.record["error_code"], "cloud_model_not_enabled")
        self.assertEqual(self.app.core.planner._call_facts(failure.exception.record)["requested_model"], "test-model")
        self.assertEqual(model.inner.calls, 0)

    def test_model_requests_exceed_previous_visitor_and_daily_limits(self):
        for token in (self.a, self.b):
            model = self.app.sessions[token].model
            for _ in range(35):
                model.complete([{"role": "user", "content": "public"}])
            self.assertEqual(model.inner.calls, 35)
            self.assertEqual(model.status()["demo_calls_reserved"], 35)
            self.assertIsNone(model.status()["demo_call_limit"])
        self.assertFalse((self.directory / "daily-budget.sqlite").exists())

    def test_input_limit_never_retries_provider_and_failure_can_be_saved(self):
        other = self.app.sessions[self.b].model
        other.input_limit = 8
        with self.assertRaises(self.app.core.ModelServiceError) as failure:
            other.complete([{"role": "user", "content": "too long"}])
        self.assertEqual(failure.exception.record["error_code"], "demo_input_limit")
        self.assertEqual(self.app.core.planner._call_facts(failure.exception.record)["requested_model"], "test-model")
        self.assertEqual(other.inner.calls, 0)

    def test_theory_and_direction_requests_have_no_count_limit(self):
        model = self.app.sessions[self.a].model
        for _ in range(20):
            model.complete_for_theory([{"role": "user", "content": "public"}])
            model.complete([{"role": "user", "content": "public"}])
        self.assertEqual(model.inner.calls, 40)

    def test_old_settings_and_budget_file_cannot_restore_model_quotas(self):
        old_budget = self.directory / "daily-budget.sqlite"
        old_budget.write_bytes(b"existing exhausted budget fixture")
        for index, value in enumerate(("0", "1", "3", "999999", "obsolete")):
            other = cloud.build_app(public_origin=self.app.origin, runtime_root=self.directory,
                environ={"YANZHENG_DEMO_MODEL_ENABLED": "1", "YANZHENG_DEMO_SESSION_CALLS": value,
                         "YANZHENG_DEMO_DAILY_CALLS": value}, model_factory=self.factory)
            token = other._new_session()
            model = other.sessions[token].model
            for _ in range(35):
                model.complete([{"role": "user", "content": "public"}])
            self.assertEqual(model.inner.calls, 35)
        self.assertEqual(old_budget.read_bytes(), b"existing exhausted budget fixture")

    def test_expiration_cannot_delete_active_session(self):
        session = self.app.sessions[self.a]
        session.active = 1
        self.clock[0] += self.app.ttl + 1
        self.app._cleanup()
        self.assertIn(self.a, self.app.sessions)
        self.assertNotIn(self.b, self.app.sessions)
        session.active = 0
        self.app._cleanup()
        self.assertNotIn(self.a, self.app.sessions)

    def test_capacity_fails_closed(self):
        self.app.max_sessions = 2
        self.assertIsNone(self.app._new_session())
        self.assertEqual(len(self.app.sessions), 2)

    def test_fixed_public_origin_ignores_forwarded_headers(self):
        scope = {"type": "http", "scheme": "http", "server": ("internal", 7860), "path": "/api/projects",
                 "headers": [(b"host", b"evil.test"), (b"x-forwarded-host", b"evil.test"),
                             (b"forwarded", b"host=evil.test"), (b"x-forwarded-proto", b"http")]}
        result = self.app._child_scope(scope)
        self.assertEqual(result["scheme"], "https")
        self.assertEqual(result["server"], ("demo.example.test", 443))
        self.assertEqual(result["headers"], [(b"host", b"demo.example.test")])

    def test_invalid_origin_is_rejected(self):
        for origin in ("", "http://demo.test", "https://demo.test/path", "https://user:pass@demo.test"):
            with self.assertRaises(ValueError):
                cloud._origin(origin)

    def test_disabled_evidence_is_not_a_linux_registry_bypass(self):
        self.assertFalse(self.app.evidence.metadata()["available"])
        with self.assertRaises(self.app.core.evidence_access.Unavailable):
            self.app.evidence.read("anything", [])
        with self.assertRaises(RuntimeError):
            cloud._disabled_lean()

    def test_default_model_configuration_never_opens_local_env(self):
        other = cloud.build_app(public_origin="https://demo.example.test",
            runtime_root=self.directory / "default", environ={})
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in (".env", "local.env"):
                raise AssertionError("Local env read")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded):
            token = other._new_session()
            self.assertFalse(other.sessions[token].model.status()["configured"])
            self.assertFalse(other.absent_env.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
