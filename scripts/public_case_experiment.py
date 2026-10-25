"""Executable public-case A protocol and B diagnostic; not a new theorem/benchmark.

Run --suite smoke for implementation checks (6 rows). The 90-row full suite has
not been executed unless its own dated output says so. BLAS threads are bounded.
"""
import os
for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[variable] = "1"
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time
import numpy as np


def randomized_svd(a, rank, oversampling, power, seed):
    rng = np.random.default_rng(seed)
    width = min(rank + oversampling, min(a.shape))
    q = np.linalg.qr(a @ rng.standard_normal((a.shape[1], width)), mode="reduced")[0]
    for _ in range(power):
        qt = np.linalg.qr(a.T @ q, mode="reduced")[0]
        q = np.linalg.qr(a @ qt, mode="reduced")[0]
    small = q.T @ a
    u, s, vt = np.linalg.svd(small, full_matrices=False)
    reconstruction = ((q @ u[:, :rank]) * s[:rank]) @ vt[:rank]
    orthogonality = float(np.linalg.norm(q.T @ q - np.eye(q.shape[1]), ord=2))
    return reconstruction, orthogonality, width


def run(suite, out):
    out.mkdir(parents=True, exist_ok=True)
    m, n = (256, 192) if suite == "smoke" else (512, 384)
    ranks, powers, seeds, repeats = ((10,), (0, 1), (0,), 1) if suite == "smoke" else ((10, 20), (0, 1, 2), range(5), 3)
    rows = []
    # Warm up the numerical library before any recorded timing.
    np.linalg.svd(np.eye(12), full_matrices=False)
    for family in ("exponential", "slow_decay", "plateau_tail"):
        j = np.arange(n)
        singular = np.exp(-j / 8) if family == "exponential" else (1 + j) ** -0.5
        if family == "plateau_tail":
            singular = np.where(j < 25, 1.0, 0.1)
        for seed in seeds:
            rng = np.random.default_rng(seed + 1000)
            left = np.linalg.qr(rng.standard_normal((m, n)), mode="reduced")[0]
            right = np.linalg.qr(rng.standard_normal((n, n)), mode="reduced")[0]
            a = (left * singular) @ right.T
            reference_start = time.perf_counter()
            u, s, vt = np.linalg.svd(a, full_matrices=False)
            reference_ms = (time.perf_counter() - reference_start) * 1000
            matrix_hash = hashlib.sha256(a.tobytes()).hexdigest()
            for rank in ranks:
                reference = (u[:, :rank] * s[:rank]) @ vt[:rank]
                reference_residual = a - reference
                optimal2 = float(np.linalg.norm(reference_residual, 2))
                optimal_f = float(np.linalg.norm(reference_residual, "fro"))
                for power in powers:
                    timings = []
                    for _ in range(repeats):
                        started = time.perf_counter()
                        approx, orthogonality, width = randomized_svd(a, rank, 10, power, seed)
                        timings.append((time.perf_counter() - started) * 1000)
                    residual = a - approx
                    error2 = float(np.linalg.norm(residual, 2))
                    error_f = float(np.linalg.norm(residual, "fro"))
                    if not (orthogonality < 1e-10 and error2 >= optimal2 * (1 - 1e-10) and np.isfinite(error_f)):
                        raise RuntimeError("Implementation sanity checks failed; stop before drawing conclusions")
                    rows.append(dict(family=family, m=m, n=n, k=rank, p=10, q=power, seed=seed,
                        matrix_sha256=matrix_hash, repeats=repeats,
                        algorithm_ms_median=float(np.median(timings)),
                        reference_svd_ms=reference_ms, spectral_ratio=error2 / optimal2,
                        frobenius_ratio=error_f / optimal_f, q_orthogonality=orthogonality,
                        block_matrix_products=2 * power + 2,
                        equivalent_single_vector_products=(2 * power + 2) * width,
                        leading_matmul_flops_estimate=2 * m * n * width * (2 * power + 2)))
    with (out / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    # A small instance inspired by Musco §2.2; it is not a dataset reproduction.
    a = np.diag([np.sqrt(10.), np.sqrt(10.), 1.])
    z = np.array([[0.], [0.], [1.]])
    b = {"kind": "diagnostic_example_inspired_by_paper_not_original_experiment",
         "matrix": "diag(sqrt(10), sqrt(10), 1)", "k": 1, "Z": "e3",
         "residual_spectral_squared": float(np.linalg.norm(a - z @ z.T @ a, 2) ** 2),
         "optimal_rank1_residual_spectral_squared": 10,
         "captured_variance": float((z.T @ a @ a.T @ z)[0, 0]),
         "best_rank1_captured_variance": 10,
         "interpretation": "谱范数残差最优仍可只捕获1而非10的方差；不能只据此验收PCA。这个构造并不证明特定随机SVD算法会输出Z=e3。重复奇异值下最优方向不唯一。"}
    (out / "B-diagnostic.json").write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": suite, "rows": len(rows), "numpy": np.__version__, "python": platform.python_version(),
        "platform": platform.system(), "machine": platform.machine(), "blas_threads": 1,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": "implementation_checks_passed",
        "scope": "仅冒烟与诊断；正式实验未执行，不能据此宣称加速或普遍性质。" if suite == "smoke" else "有限合成矩阵实验；不是普遍定理，也未测试真实数据。",
        "timing_scope": "矩阵生成、参考SVD、残差测量与算法分别计算；算法计时含随机采样、QR、小SVD和重构。访问代价另列，不把不同q称作同预算。"}
    (out / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.suite, Path(args.output).resolve())
