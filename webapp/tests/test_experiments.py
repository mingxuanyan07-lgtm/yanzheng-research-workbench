"""Finite numerical checks and execution boundaries for the fixed SVD runner."""

import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import engine
import experiments
import experiment_worker as worker


def selected_project():
    project = engine.create_project({"initial_idea": "研究随机低秩逼近与随机 SVD 的误差", "goal": "experimental",
                                     "resources": "laptop", "time_horizon": "week", "context": ""})
    engine.select_topic(project, project["direction_map"]["topics"][0]["id"], "先核对有限实验。")
    return project


SMALL = {"rows": 40, "cols": 20, "rank": 2, "p_values": [0, 4], "q_values": [0, 1], "repeats": 2, "data_seed": 17}


class ExperimentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = selected_project()
        cls.result = experiments.run(cls.project, SMALL)

    def test_actual_worker_counts_hashes_invariants_and_csv(self):
        result = self.result
        self.assertEqual(result["status"], "success", result.get("error"))
        self.assertEqual(result["metrics"], {"matrix_count": 3, "configuration_count": 12,
                                             "randomized_run_count": 24, "baseline_run_count": 6})
        self.assertTrue(result["checks"]["passed"])
        self.assertEqual(result["checks"]["optimal_rank_error_checks"], 24)
        self.assertEqual(result["checks"]["baseline_tail_checks"], 6)
        self.assertEqual(result["checks"]["significantly_below_optimum_count"], 0)
        self.assertTrue(result["provenance"]["new_experiments_executed"])
        self.assertFalse(result["provenance"]["external_model_api_called"])
        self.assertEqual(len({item["matrix_sha256"] for item in result["datasets"]}), 3)
        self.assertTrue(all(len(item["matrix_sha256"]) == 64 for item in result["datasets"]))
        for row in result["rows"]:
            self.assertGreaterEqual(row["relative_error"] + 1e-10, row["baseline_error"])
            self.assertEqual(row["matrix_passes"], 2 * row["q"] + 2)
            self.assertEqual(row["sample_width"], SMALL["rank"] + row["p"])
            self.assertGreaterEqual(row["time_ms"], 0)
        exported = list(csv.DictReader(io.StringIO(experiments.export_csv(result))))
        self.assertEqual(len(exported), 24)
        self.assertEqual(exported[0]["experiment_id"], result["id"])
        self.assertEqual(exported[0]["target_rank"], "2")
        json.dumps(result, allow_nan=False)

    def test_exact_known_diagonal_and_full_width_randomized(self):
        np = worker._load_numpy()
        matrix = np.diag([5.0, 3.0, 1.0, 0.5])
        exact = worker.exact_truncated_svd(matrix, 2)
        expected_error = (1.0 + 0.25) ** 0.5
        self.assertAlmostEqual(float(np.linalg.norm(matrix - exact, "fro")), expected_error, places=12)
        random = worker.randomized_svd(matrix, 2, 2, 1, 7, 4)
        self.assertAlmostEqual(float(np.linalg.norm(matrix - random, "fro")), expected_error, places=12)
        self.assertLessEqual(int(np.linalg.matrix_rank(random)), 2)

    def test_seed_reproducibility_excludes_wall_clock(self):
        second = experiments.run(self.project, SMALL)
        self.assertEqual(second["status"], "success", second.get("error"))
        self.assertEqual([item["matrix_sha256"] for item in self.result["datasets"]],
                         [item["matrix_sha256"] for item in second["datasets"]])
        first_values = {(row["dataset_id"], row["p"], row["q"], row["sampling_seed"]): row["relative_error"] for row in self.result["rows"]}
        for row in second["rows"]:
            self.assertAlmostEqual(row["relative_error"], first_values[(row["dataset_id"], row["p"], row["q"], row["sampling_seed"])], places=12)
        changed = copy.deepcopy(SMALL)
        changed["data_seed"] += 1
        generated = list(worker.make_datasets(changed))
        self.assertNotEqual(generated[0][1]["matrix_sha256"], self.result["datasets"][0]["matrix_sha256"])

    def test_bounds_and_no_arbitrary_execution_fields(self):
        for config in ({"rows": 241}, {"cols": 19}, {"rank": True}, {"rank": 20, "cols": 20},
                       {"repeats": 6}, {"p_values": [1, 1]}, {"q_values": [3]},
                       {"p_values": [0, 1, 2, 3]}, {"data_seed": -1}, {"code": "print('x')"},
                       {"path": "elsewhere.py"}, {"url": "https://untrusted.invalid"},
                       {"cols": 20, "rank": 8, "p_values": [16]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                experiments.validate_config(config)
        self.assertEqual(experiments.validate_config({}), experiments.defaults())
        self.assertEqual(experiments.validate_config({"p_values": [8, 0]})["p_values"], [0, 8])

    def test_recommendation_is_not_a_user_selection(self):
        project = selected_project()
        engine.reopen_project(project)
        self.assertIsNotNone(project["direction_map"]["recommendation"]["topic_id"])
        with patch.object(experiments.subprocess, "run") as invoke:
            with self.assertRaises(ValueError):
                experiments.run(project, SMALL)
            invoke.assert_not_called()
        generic = copy.deepcopy(self.project)
        generic["intake_form"]["initial_idea"] = "公交站点研究"
        generic["direction_map"]["initial_idea"] = "公交站点研究"
        generic["direction_map"]["intake"]["known"] = {}
        selected = generic["direction_map"]["selected_topic_id"]
        generic["direction_map"]["topics"] = [{"id": selected, "title": "公交站点调查"}, {"id": "other", "title": "随机 SVD 模型推荐"}]
        self.assertFalse(experiments.supported(generic)["supported"])

    def test_subprocess_target_environment_and_timeout_failure_archive(self):
        original = copy.deepcopy(self.project)
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "fixture-secret", "AWS_SESSION_TOKEN": "fixture-token", "QWEN_BASE_URL": "fixture-url", "OPENBLAS_NUM_THREADS": "88", "PYTHONPATH": "untrusted-import-path"}):
            with patch.object(experiments.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixed", 20)) as invoke:
                result = experiments.run(self.project, SMALL)
            args, kwargs = invoke.call_args
            self.assertEqual(args[0], [sys.executable, "-I", "-B", str(experiments.WORKER)])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["cwd"], str(BASE))
            self.assertEqual(kwargs["timeout"], 20)
            for key in ("DASHSCOPE_API_KEY", "AWS_SESSION_TOKEN", "QWEN_BASE_URL", "PYTHONPATH"):
                self.assertNotIn(key, kwargs["env"])
            self.assertEqual(kwargs["env"]["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "timeout")
        self.assertTrue(result["execution_state_unknown"])
        self.assertFalse(result["provenance"]["new_experiments_executed"])
        self.assertEqual(self.project, original)
        project = copy.deepcopy(self.project)
        experiments.attach_result(project, result)
        self.assertEqual(project["experiments"][0]["id"], result["id"])
        self.assertEqual(project["history"][-1]["action"], "experiment_failed")
        self.assertFalse(project["direction_map"]["provenance"]["new_experiments_executed"])

    def test_invalid_output_and_busy_are_distinct(self):
        bad = subprocess.CompletedProcess(["fixed"], 0, b'{"status":"success"}', b"")
        with patch.object(experiments.subprocess, "run", return_value=bad):
            result = experiments.run(self.project, SMALL)
        self.assertEqual(result["error"]["code"], "invalid_result")
        self.assertTrue(result["execution_state_unknown"])
        experiments._RUN_LOCK.acquire()
        try:
            with patch.object(experiments.subprocess, "run") as invoke:
                busy = experiments.run(self.project, SMALL)
                invoke.assert_not_called()
        finally:
            experiments._RUN_LOCK.release()
        self.assertEqual(busy["error"]["code"], "busy")
        self.assertFalse(busy["execution_state_unknown"])
        self.assertIsNone(busy["started_at"])

    def test_attachment_retains_original_context_after_selection_changes(self):
        project = copy.deepcopy(self.project)
        record = copy.deepcopy(self.result)
        before = project["direction_map"]["selected_topic_id"]
        other = next(topic["id"] for topic in project["direction_map"]["topics"] if topic["id"] != before)
        engine.select_topic(project, other)
        experiments.attach_result(project, record)
        self.assertEqual(project["direction_map"]["selected_topic_id"], other)
        saved = project["experiments"][0]
        self.assertEqual(saved["selected_topic_id"], before)
        self.assertFalse(saved["attachment"]["selected_topic_matches_current"])
        self.assertFalse(project["direction_map"]["provenance"]["new_experiments_executed"])
        self.assertTrue(saved["provenance"]["new_experiments_executed"])
        with self.assertRaises(ValueError):
            experiments.attach_result(project, record)


if __name__ == "__main__":
    unittest.main()
