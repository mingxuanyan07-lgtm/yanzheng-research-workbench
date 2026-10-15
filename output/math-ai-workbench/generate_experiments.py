"""Run a small, offline, paired randomized-SVD experiment.

Re-run with the bundled Python interpreter; NumPy is the only required package.
Errors are reproducible in the recorded environment. Wall-clock timings vary.
"""

import os

# Set these BEFORE importing NumPy so the BLAS runtime observes them.
THREAD_ENV = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OMP_THREAD_LIMIT": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
}
os.environ.update(THREAD_ENV)

import argparse
import contextlib
import ctypes
import datetime as dt
import hashlib
import io
import json
import math
import platform
from pathlib import Path
import sys
import time
import warnings

import numpy as np


DATA_SEED = 20261013
SAMPLING_SEEDS = list(range(20))
ROWS, COLS, RANK = 120, 80, 8
P_VALUES, Q_VALUES = [4, 8, 16], [0, 1, 2]
MAX_WIDTH = RANK + max(P_VALUES)
WARMUPS = 1


def summary(values):
    values = np.asarray(values, dtype=np.float64)
    q1, median, q3 = np.percentile(values, [25, 50, 75], method="linear")
    return {
        "median": float(median),
        "min": float(values.min()),
        "max": float(values.max()),
        "q1": float(q1),
        "q3": float(q3),
        "iqr": float(q3 - q1),
    }


def exact_truncated_svd(matrix):
    u, singular_values, vt = np.linalg.svd(matrix, full_matrices=False)
    return (u[:, :RANK] * singular_values[:RANK]) @ vt[:RANK, :]


def randomized_svd(matrix, p, q, seed):
    # Common random numbers: all p/q settings use a prefix of the SAME
    # 80x24 draw for a given seed. RNG initialization and sampling are timed.
    omega = np.random.default_rng(seed).standard_normal((COLS, MAX_WIDTH))
    omega = omega[:, : RANK + p]
    basis, _ = np.linalg.qr(matrix @ omega, mode="reduced")
    # Re-orthogonalize on BOTH sides of every power step for numerical stability.
    for _ in range(q):
        dual_basis, _ = np.linalg.qr(matrix.T @ basis, mode="reduced")
        basis, _ = np.linalg.qr(matrix @ dual_basis, mode="reduced")
    small_matrix = basis.T @ matrix
    u_small, singular_values, vt = np.linalg.svd(small_matrix, full_matrices=False)
    return ((basis @ u_small[:, :RANK]) * singular_values[:RANK]) @ vt[:RANK, :]


def measured_call(function, *args):
    started = time.perf_counter_ns()
    approximation = function(*args)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return approximation, elapsed_ms


def make_datasets():
    rng = np.random.default_rng(DATA_SEED)
    datasets = []
    definitions = [
        ("fast_decay", "快速衰减", "exp(-j / 6), j = 0,...,79"),
        ("slow_decay", "缓慢衰减", "(j + 1)^(-0.6), j = 0,...,79"),
        ("noisy_low_rank", "带噪低秩", "rank-8 signal + 0.08 / sqrt(120) * Gaussian noise"),
    ]
    for dataset_id, name, formula in definitions:
        # Each family gets fresh orthonormal factors from the same, sequential RNG.
        left, _ = np.linalg.qr(rng.standard_normal((ROWS, COLS)), mode="reduced")
        right, _ = np.linalg.qr(rng.standard_normal((COLS, COLS)), mode="reduced")
        if dataset_id == "fast_decay":
            spectrum = np.exp(-np.arange(COLS, dtype=np.float64) / 6.0)
            matrix = (left * spectrum) @ right.T
            construction = {
                "formula": "A = U @ diag(s) @ V.T",
                "spectrum_formula": formula,
                "singular_values": spectrum.tolist(),
            }
        elif dataset_id == "slow_decay":
            spectrum = (np.arange(COLS, dtype=np.float64) + 1.0) ** -0.6
            matrix = (left * spectrum) @ right.T
            construction = {
                "formula": "A = U @ diag(s) @ V.T",
                "spectrum_formula": formula,
                "singular_values": spectrum.tolist(),
            }
        else:
            signal_spectrum = np.geomspace(6.0, 1.0, RANK)
            clean = (left[:, :RANK] * signal_spectrum) @ right[:, :RANK].T
            noise_scale = 0.08 / np.sqrt(ROWS)
            noise = noise_scale * rng.standard_normal((ROWS, COLS))
            matrix = clean + noise
            construction = {
                "formula": "A = U[:, :8] @ diag(s_signal) @ V[:, :8].T + noise",
                "signal_singular_values": signal_spectrum.tolist(),
                "noise_entry_distribution": "independent N(0, (0.08/sqrt(120))^2)",
                "noise_entry_std": float(noise_scale),
                "measured_signal_frobenius_norm": float(np.linalg.norm(clean, "fro")),
                "measured_noise_frobenius_norm": float(np.linalg.norm(noise, "fro")),
            }
        matrix = np.ascontiguousarray(matrix, dtype=np.float64)
        construction.update({
            "orthonormal_factors": "reduced QR of independent standard-normal matrices; sequential dataset RNG",
            "data_seed": DATA_SEED,
        })
        datasets.append((matrix, {
            "id": dataset_id,
            "name": name,
            "shape": [ROWS, COLS],
            "target_rank": RANK,
            "dtype": str(matrix.dtype),
            "matrix_sha256": hashlib.sha256(matrix.tobytes(order="C")).hexdigest(),
            "frobenius_norm": float(np.linalg.norm(matrix, "fro")),
            "construction": construction,
        }))
    return datasets


def get_environment():
    numpy_build = io.StringIO()
    with warnings.catch_warnings(), contextlib.redirect_stdout(numpy_build):
        warnings.filterwarnings("ignore", message="Install `pyyaml` for better output", category=UserWarning)
        np.show_config()
    try:
        from threadpoolctl import threadpool_info
        pools = threadpool_info()
        blas_pools = [pool for pool in pools if pool.get("user_api") == "blas"]
        if any(pool.get("num_threads") != 1 for pool in blas_pools):
            raise RuntimeError("BLAS did not honor the requested single-thread setting.")
        thread_status = "verified" if blas_pools else "environment_requested; no BLAS pool reported"
    except ImportError:
        pools = []
        # NumPy's bundled Windows OpenBLAS exposes a read-only thread-count
        # API. Use it when the optional threadpoolctl package is unavailable.
        numpy_libs = Path(np.__file__).parent.parent / "numpy.libs"
        getter_names = [
            "scipy_openblas_get_num_threads64_",
            "scipy_openblas_get_num_threads",
            "openblas_get_num_threads64_",
            "openblas_get_num_threads",
        ]
        for library_path in numpy_libs.glob("*openblas*"):
            library = ctypes.CDLL(str(library_path))
            for getter_name in getter_names:
                if hasattr(library, getter_name):
                    getter = getattr(library, getter_name)
                    getter.argtypes = []
                    getter.restype = ctypes.c_int
                    count = getter()
                    if count != 1:
                        raise RuntimeError(f"OpenBLAS reports {count} threads instead of 1.")
                    pools.append({
                        "user_api": "blas",
                        "internal_api": "openblas",
                        "filepath": str(library_path),
                        "num_threads": count,
                        "verification_api": getter_name,
                    })
                    break
        thread_status = "verified via OpenBLAS API" if pools else "environment_requested; thread-count API unavailable"
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "numpy_version": np.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "thread_environment": THREAD_ENV,
        "threadpool_info": pools,
        "blas_single_thread_status": thread_status,
        "numpy_build_configuration": numpy_build.getvalue(),
        "timing_clock": "time.perf_counter_ns",
        "timing_clock_resolution_seconds": time.get_clock_info("perf_counter").resolution,
    }


def check_finite(value, path="root"):
    if isinstance(value, dict):
        for key, item in value.items():
            check_finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_finite(item, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Non-finite number at {path}")


def run_experiment():
    environment = get_environment()
    data_entries, groups = [], []
    comparisons_checked = 0
    tiny_below_baseline_count = 0
    worst_below_baseline = 0.0
    for matrix, dataset in make_datasets():
        norm = dataset["frobenius_norm"]
        for _ in range(WARMUPS):
            exact_truncated_svd(matrix)
        baseline_runs = []
        for repetition in range(len(SAMPLING_SEEDS)):
            approximation, elapsed_ms = measured_call(exact_truncated_svd, matrix)
            error = float(np.linalg.norm(matrix - approximation, "fro") / norm)
            baseline_runs.append({"repetition": repetition, "relative_error": error, "time_ms": elapsed_ms})
        baseline_error = float(np.median([run["relative_error"] for run in baseline_runs]))
        if baseline_error <= 1e-12:
            raise ValueError(f"{dataset['id']}: baseline error must be nonzero.")
        dataset["baseline"] = {
            "method": "numpy.linalg.svd(full_matrices=False), truncate to k=8, reconstruct",
            "relative_error": baseline_error,
            "timing_ms": summary([run["time_ms"] for run in baseline_runs]),
            "individual_runs": baseline_runs,
        }
        data_entries.append(dataset)

        # Warm each numerical path before collecting data. Run settings in a
        # deterministic shuffled order for each seed to reduce order bias.
        settings = [(p, q) for p in P_VALUES for q in Q_VALUES]
        per_setting = {setting: [] for setting in settings}
        for p, q in settings:
            for _ in range(WARMUPS):
                randomized_svd(matrix, p, q, SAMPLING_SEEDS[0])
        order_rng = np.random.default_rng(DATA_SEED + 100)
        for seed in SAMPLING_SEEDS:
            for setting_index in order_rng.permutation(len(settings)):
                p, q = settings[int(setting_index)]
                approximation, elapsed_ms = measured_call(randomized_svd, matrix, p, q, seed)
                error = float(np.linalg.norm(matrix - approximation, "fro") / norm)
                tolerance = max(1e-10, baseline_error * 1e-9)
                if error < baseline_error - tolerance:
                    raise ValueError(f"{dataset['id']}, p={p}, q={q}, seed={seed}: below optimal rank-k error.")
                if error < baseline_error:
                    tiny_below_baseline_count += 1
                    worst_below_baseline = max(worst_below_baseline, baseline_error - error)
                comparisons_checked += 1
                per_setting[(p, q)].append({
                    "seed": seed,
                    "relative_error": error,
                    "time_ms": elapsed_ms,
                    "baseline_error_ratio": error / baseline_error,
                    "relative_gap_to_baseline": error / baseline_error - 1.0,
                })
        for p, q in settings:
            runs = per_setting[(p, q)]
            groups.append({
                "id": f"{dataset['id']}_p{p}_q{q}",
                "dataset_id": dataset["id"],
                "method": "randomized_svd",
                "p": p,
                "q": q,
                "sample_width": RANK + p,
                "seeds": SAMPLING_SEEDS,
                "run_count": len(runs),
                "relative_error": summary([run["relative_error"] for run in runs]),
                "timing_ms": summary([run["time_ms"] for run in runs]),
                "baseline_error_ratio": summary([run["baseline_error_ratio"] for run in runs]),
                "relative_gap_to_baseline": summary([run["relative_gap_to_baseline"] for run in runs]),
                "individual_runs": runs,
            })
    result = {
        "metadata": {
            "schema_version": 1,
            "experiment_name": "微型随机 SVD 离线对照实验",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "data_seed": DATA_SEED,
            "sampling_seeds": SAMPLING_SEEDS,
            "shape": [ROWS, COLS],
            "target_rank": RANK,
            "p_values": P_VALUES,
            "q_values": Q_VALUES,
            "runs_per_group": len(SAMPLING_SEEDS),
            "warmup_runs_per_method": WARMUPS,
            "baseline_repetitions_per_dataset": len(SAMPLING_SEEDS),
            "paired_randomness": "For each seed, draw an 80x24 Omega and take its first k+p columns. All q values share Omega.",
            "execution_order": "Within each dataset, baseline measured first; randomized settings shuffled for each seed using RNG seed 20261113.",
            "error_definition": "||A - A_hat||_F / ||A||_F",
            "baseline_error_ratio_definition": "relative_error / exact_truncated_SVD_relative_error; optimum is 1",
            "relative_gap_to_baseline_definition": "baseline_error_ratio - 1; optimum is 0",
            "quantile_method": "NumPy percentile, linear interpolation",
            "timing_scope": "Only algorithm and rank-k reconstruction; randomized method includes RNG initialization, full shared-width Gaussian draw and prefix selection. Excludes dataset construction, warmups, reconstruction-error calculation and JSON serialization.",
            "environment": environment,
            "limitations": [
                "Three synthetic matrices at one small size, one target rank and one data seed; results do not establish general superiority or a new algorithm.",
                "20 sampling seeds measure randomness on these fixed matrices; they are not 20 independent datasets.",
                "Wall-clock timings are actual local measurements and are sensitive to scheduling and overhead; they are not large-scale speedup claims.",
                "The exact baseline is measured before randomized methods; the warmup and shuffled randomized order reduce, but do not eliminate, timing-order effects.",
                "All randomized settings draw 24 columns for paired comparisons; narrower p values include unused-column sampling overhead.",
                "More p or q entails extra work; observed accuracy improvements do not imply an equal-cost comparison.",
                "Computational checks support these finite experiments; they do not replace a general mathematical proof.",
            ],
        },
        "datasets": data_entries,
        "groups": groups,
        "validation": {
            "all_numeric_data_finite": True,
            "nonzero_baseline_errors": True,
            "group_count": len(groups),
            "randomized_run_count": comparisons_checked,
            "baseline_run_count": len(data_entries) * len(SAMPLING_SEEDS),
            "below_baseline_tolerance": "max(1e-10, baseline_relative_error * 1e-9)",
            "significantly_below_optimum_count": 0,
            "floating_point_tiny_below_baseline_count": tiny_below_baseline_count,
            "largest_tiny_below_baseline_absolute_error_difference": worst_below_baseline,
            "all_groups_have_20_runs": all(len(group["individual_runs"]) == 20 for group in groups),
        },
    }
    check_finite(result)
    if result["validation"]["group_count"] != 27 or not result["validation"]["all_groups_have_20_runs"]:
        raise ValueError("Unexpected number of experimental groups or runs.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("experiment-data.json"))
    args = parser.parse_args()
    result = run_experiment()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Saved {args.output.resolve()}")
    print(f"BLAS single-thread status: {result['metadata']['environment']['blas_single_thread_status']}")
    print(f"Validation: {json.dumps(result['validation'], ensure_ascii=False)}")
    for dataset in result["datasets"]:
        candidates = [group for group in result["groups"] if group["dataset_id"] == dataset["id"]]
        print(f"{dataset['id']}: exact error={dataset['baseline']['relative_error']:.8f}; exact median={dataset['baseline']['timing_ms']['median']:.4f} ms")
        for group in candidates:
            print(f"  p={group['p']:2}, q={group['q']}: error={group['relative_error']['median']:.8f}, ratio={group['baseline_error_ratio']['median']:.6f}, time={group['timing_ms']['median']:.4f} ms")


if __name__ == "__main__":
    main()
