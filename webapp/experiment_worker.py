"""Fixed numerical worker. stdin is bounded JSON parameters, never Python code."""

import os

THREAD_ENV = {key: "1" for key in (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")}
os.environ.update(THREAD_ENV)

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import sys
import time

np = None


def _load_numpy():
    global np
    if np is None:
        import numpy
        np = numpy
    return np


def _config(value):
    # Isolated (-I) Python excludes cwd and PYTHONPATH. Import exactly our fixed
    # sibling rather than permitting any arbitrary import or executable target.
    spec = importlib.util.spec_from_file_location("fixed_experiment_config", Path(__file__).with_name("experiments.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_config(value)


def summary(values):
    return {"median": float(np.median(values)), "min": float(min(values)), "max": float(max(values))}


def exact_truncated_svd(matrix, rank):
    _load_numpy()
    left, singular_values, right = np.linalg.svd(matrix, full_matrices=False)
    return (left[:, :rank] * singular_values[:rank]) @ right[:rank, :]


def randomized_svd(matrix, rank, p, q, seed, max_width):
    _load_numpy()
    omega = np.random.default_rng(seed).standard_normal((matrix.shape[1], max_width))[:, :rank + p]
    basis, _ = np.linalg.qr(matrix @ omega, mode="reduced")
    for _ in range(q):
        dual, _ = np.linalg.qr(matrix.T @ basis, mode="reduced")
        basis, _ = np.linalg.qr(matrix @ dual, mode="reduced")
    small = basis.T @ matrix
    left, singular_values, right = np.linalg.svd(small, full_matrices=False)
    return ((basis @ left[:, :rank]) * singular_values[:rank]) @ right[:rank, :]


def make_datasets(config):
    _load_numpy()
    rows, cols, rank = config["rows"], config["cols"], config["rank"]
    dimension = min(rows, cols)
    rng = np.random.default_rng(config["data_seed"])
    for family, name in (("fast_decay", "快速衰减"), ("slow_decay", "缓慢衰减"), ("noisy_low_rank", "带噪低秩")):
        left, _ = np.linalg.qr(rng.standard_normal((rows, dimension)), mode="reduced")
        right, _ = np.linalg.qr(rng.standard_normal((cols, dimension)), mode="reduced")
        construction = {"data_seed": config["data_seed"], "factors": "sequential Gaussian draws, reduced QR"}
        if family == "fast_decay":
            singular_values = np.exp(-np.arange(dimension, dtype=np.float64) / 6.0)
            matrix = (left * singular_values) @ right.T
            construction.update({"formula": "U diag(s) V.T", "spectrum": "s_j=exp(-j/6), j=0..min(m,n)-1"})
        elif family == "slow_decay":
            singular_values = (np.arange(dimension, dtype=np.float64) + 1.0) ** -0.6
            matrix = (left * singular_values) @ right.T
            construction.update({"formula": "U diag(s) V.T", "spectrum": "s_j=(j+1)^(-0.6), j=0..min(m,n)-1"})
        else:
            signal = np.geomspace(6.0, 1.0, rank)
            noise_scale = 0.08 / math.sqrt(rows)
            matrix = (left[:, :rank] * signal) @ right[:, :rank].T + noise_scale * rng.standard_normal((rows, cols))
            construction.update({"formula": "rank-k signal + independent Gaussian noise", "signal_singular_values": signal.tolist(), "noise_entry_std": noise_scale})
        matrix = np.ascontiguousarray(matrix, dtype=np.float64)
        yield matrix, {"id": family, "name": name, "shape": [rows, cols], "target_rank": rank,
                       "matrix_sha256": hashlib.sha256(matrix.astype("<f8", copy=False).tobytes(order="C")).hexdigest(),
                       "matrix_hash_encoding": "little-endian float64, C order", "dtype": "float64",
                       "frobenius_norm": float(np.linalg.norm(matrix, "fro")), "construction": construction}


def _measured(function, *args):
    start = time.perf_counter_ns()
    result = function(*args)
    return result, (time.perf_counter_ns() - start) / 1_000_000.0


def _finite(value):
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


def execute(config):
    result = {"status": "failed", "execution_started": False,
              "metrics": {"matrix_count": 0, "configuration_count": 0, "randomized_run_count": 0, "baseline_run_count": 0},
              "checks": {"passed": False, "all_numeric_data_finite": False, "optimal_rank_error_checks": 0,
                         "significantly_below_optimum_count": 0, "floating_point_tiny_below_baseline_count": 0,
                         "baseline_tail_checks": 0, "below_baseline_tolerance": "max(1e-10, baseline_error*1e-9)"},
              "datasets": [], "rows": [], "groups": [], "environment": {},
              "definitions": {"relative_error": "||A-A_hat||_F / ||A||_F", "baseline_error_ratio": "relative_error / exact_truncated_SVD_relative_error",
                              "matrix_passes": "2*q+2 logical dense matrix multiplication passes; width k+p also affects cost",
                              "sampling_seeds": "0..repeats-1, paired across p/q on each fixed matrix",
                              "paired_sampling": "draw n x (k+max(p)) once per seed and retain first k+p columns",
                              "matrix_count": "3 fixed matrices, one per family, not the sampling repetition count",
                              "timing": "algorithm, sampling and rank-k reconstruction only; one warmup per method/config; baseline first"}}
    try:
        config = _config(config)
    except ValueError:
        result["error"] = {"code": "invalid_config"}
        return result
    try:
        _load_numpy()
    except ImportError:
        result["error"] = {"code": "dependency_missing"}
        return result
    try:
        result["execution_started"] = True
        result["environment"] = {"python_version": platform.python_version(), "numpy_version": np.__version__,
                                 "platform": platform.platform(), "requested_blas_thread_environment": THREAD_ENV,
                                 "blas_thread_status": "single-thread environment requested; runtime thread count not independently inspected",
                                 "timing_clock": "time.perf_counter_ns"}
        rank = config["rank"]
        settings = [(p, q) for p in config["p_values"] for q in config["q_values"]]
        max_width = rank + max(config["p_values"])
        for matrix, dataset in make_datasets(config):
            norm = dataset["frobenius_norm"]
            exact_truncated_svd(matrix, rank)
            baseline_runs = []
            singular_values = np.linalg.svd(matrix, full_matrices=False, compute_uv=False)
            tail_error = float(np.linalg.norm(singular_values[rank:]) / norm)
            for repetition in range(config["repeats"]):
                approximation, milliseconds = _measured(exact_truncated_svd, matrix, rank)
                error = float(np.linalg.norm(matrix - approximation, "fro") / norm)
                baseline_runs.append({"repetition": repetition, "relative_error": error, "time_ms": milliseconds})
                result["metrics"]["baseline_run_count"] += 1
                if abs(error - tail_error) > max(1e-10, tail_error * 1e-9):
                    raise ArithmeticError("baseline tail identity")
                result["checks"]["baseline_tail_checks"] += 1
            baseline_error = float(np.median([row["relative_error"] for row in baseline_runs]))
            if baseline_error <= 1e-12:
                raise ArithmeticError("nonzero baseline required")
            dataset["baseline"] = {"method": "exact_truncated_svd", "relative_error": baseline_error,
                                   "singular_tail_relative_error": tail_error, "timing_ms": summary([row["time_ms"] for row in baseline_runs]),
                                   "individual_runs": baseline_runs}
            result["datasets"].append(dataset)
            result["metrics"]["matrix_count"] += 1
            per_setting = {setting: [] for setting in settings}
            for p, q in settings:
                randomized_svd(matrix, rank, p, q, 0, max_width)
            order_rng = np.random.default_rng(config["data_seed"] + 100)
            for seed in range(config["repeats"]):
                for setting_index in order_rng.permutation(len(settings)):
                    p, q = settings[int(setting_index)]
                    approximation, milliseconds = _measured(randomized_svd, matrix, rank, p, q, seed, max_width)
                    error = float(np.linalg.norm(matrix - approximation, "fro") / norm)
                    tolerance = max(1e-10, baseline_error * 1e-9)
                    if error < baseline_error - tolerance:
                        result["checks"]["significantly_below_optimum_count"] += 1
                        raise ArithmeticError("below optimum")
                    if error < baseline_error:
                        result["checks"]["floating_point_tiny_below_baseline_count"] += 1
                    result["checks"]["optimal_rank_error_checks"] += 1
                    row = {"dataset_id": dataset["id"], "p": p, "q": q, "sampling_seed": seed,
                           "relative_error": error, "baseline_error": baseline_error, "baseline_error_ratio": error / baseline_error,
                           "time_ms": milliseconds, "matrix_passes": 2 * q + 2, "sample_width": rank + p,
                           "matrix_entry_visits": (2 * q + 2) * config["rows"] * config["cols"], "matrix_sha256": dataset["matrix_sha256"]}
                    result["rows"].append(row)
                    per_setting[(p, q)].append(row)
                    result["metrics"]["randomized_run_count"] += 1
            for p, q in settings:
                rows = per_setting[(p, q)]
                result["groups"].append({"id": f"{dataset['id']}_p{p}_q{q}", "dataset_id": dataset["id"], "p": p, "q": q,
                                         "run_count": len(rows), "sample_width": rank + p, "matrix_passes": 2 * q + 2,
                                         "relative_error": summary([row["relative_error"] for row in rows]),
                                         "baseline_error_ratio": summary([row["baseline_error_ratio"] for row in rows]),
                                         "timing_ms": summary([row["time_ms"] for row in rows])})
                result["metrics"]["configuration_count"] += 1
        if not _finite(result):
            raise ArithmeticError("nonfinite result")
        result["checks"]["all_numeric_data_finite"] = True
        expected = 3 * len(settings) * config["repeats"]
        if result["metrics"]["randomized_run_count"] != expected or result["metrics"]["baseline_run_count"] != 3 * config["repeats"]:
            raise ArithmeticError("unexpected counts")
        result["checks"]["passed"] = True
        result["status"] = "success"
    except ArithmeticError:
        result["error"] = {"code": "numerical_check_failed"}
    except Exception:
        # Never return arbitrary tracebacks or exception strings to the web UI.
        result["error"] = {"code": "computation_failed"}
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw = sys.stdin.buffer.read(16_385)
    try:
        if len(raw) > 16_384:
            raise ValueError("input too large")
        config = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        # No computation starts for invalid transport data.
        config = {"invalid_transport": True}
    result = execute(config)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
