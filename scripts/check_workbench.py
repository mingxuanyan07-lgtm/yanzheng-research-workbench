#!/usr/bin/env python3
"""Read-only local setup/build checks. Does not import app, open a DB or call a model."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]


def environment_checks(root=ROOT):
    checks = [{"name": "Python", "ok": sys.version_info >= (3, 12),
               "detail": ".".join(map(str, sys.version_info[:3]))}]
    requirements = root / "webapp/requirements.txt"
    for line in requirements.read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
        if not match:
            continue
        package, expected = match.groups()
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        checks.append({"name": package, "ok": installed == expected,
                       "detail": f"{installed or '未安装'} / 需要 {expected}"})
    required = ("webapp/static/index.html", "webapp/static/app.js", "webapp/static/styles.css",
                "webapp/evidence_registry_manifest.json", "skills/research-direction-guide/SKILL.md",
                "skills/research-direction-guide/scripts/validate_direction_map.py",
                "skills/literature-search/scripts/search_literature.py",
                "output/math-ai-workbench/research-direction-map.json",
                "output/math-ai-workbench/experiment-data.json",
                "output/literature-search-skill/cases/forward-trial/literature-search.json",
                "output/literature-search-skill/cases/forward-trial/direction-handoff.json",
                "output/project-review/cases.json")
    missing = [name for name in required if not (root / name).is_file()]
    checks.append({"name": "运行文件", "ok": not missing,
                   "detail": "完整" if not missing else "缺少：" + "、".join(missing)})
    return checks


def running_service(port, local_build):
    """Only one fixed loopback status URL; no environment proxy or arbitrary URL."""
    url = f"http://127.0.0.1:{port}/api/status"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=2) as response:
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("status too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("storage") != "sqlite" or payload.get("mode") not in ("demo", "qwen"):
            return {"state": "unrecognized", "detail": "该端口未返回可识别的研证服务，请检查原启动窗口。"}
        if payload.get("build", {}).get("id") != local_build["id"]:
            return {"state": "restart_required", "detail": "正在运行旧版源码。请在原启动窗口按 Ctrl+C，再重新启动研证。"}
        return {"state": "current", "detail": "运行版本与本地源码一致。",
                "mode": payload["mode"], "version": payload.get("version")}
    except HTTPError:
        return {"state": "unrecognized", "detail": "该端口未提供可用的研证状态接口。"}
    except (URLError, TimeoutError, OSError):
        return {"state": "not_running", "detail": "尚未启动服务；环境检查通过后可运行 Start-Workbench.ps1。"}
    except (ValueError, TypeError, AttributeError):
        return {"state": "unrecognized", "detail": "该端口返回的状态格式不正确。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8097)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--build-id", action="store_true", help="Print only the public source fingerprint")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1–65535")
    sys.path.insert(0, str(ROOT / "webapp"))
    try:
        from engine import runtime_build_identity
        build = runtime_build_identity()
    except (ImportError, OSError, RuntimeError):
        print("运行文件无法读取，请保留完整项目后重试。", file=sys.stderr)
        return 2
    if args.build_id:
        print(build["id"])
        return 0
    checks = environment_checks()
    service = running_service(args.port, build)
    healthy = all(check["ok"] for check in checks)
    result = {"ready": healthy, "build": build, "checks": checks, "service": service,
              "scope": "read_only_no_database_no_model"}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("研证 · 启动检查（不打开研究库，不调用模型）")
        for check in checks:
            print(f"{'通过' if check['ok'] else '待处理'}  {check['name']}：{check['detail']}")
        print("服务：" + service["detail"])
        if not healthy:
            print("请从项目根目录运行 webapp/Setup-Workbench.ps1 修复环境。")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
