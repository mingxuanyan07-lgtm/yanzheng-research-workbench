"""Real-browser direction checks with an explicit, guarded temporary application.

Requires existing Python dependencies, Node, Playwright and Chrome; installs nothing.
Never starts the normal application service or uses its configuration/database.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_environment():
    env = dict(os.environ)
    for name in list(env):
        if name.startswith(("YANZHENG_", "QWEN_")) or any(
            part in name.upper() for part in ("API_KEY", "ACCESS_TOKEN", "SECRET")
        ):
            env.pop(name)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def source_manifest(repo):
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=repo).decode("utf-8").split("\0")
    paths += ["scripts/direction_browser_acceptance.py", "scripts/direction_browser_checks.mjs"]
    result = {}
    for name in sorted(set(paths)):
        if not name or not name.startswith(("webapp/", "skills/", "scripts/")):
            continue
        path = Path(name)
        if any(part in (".venv", "data", ".git", "node_modules") for part in path.parts):
            continue
        if path.name.startswith(".env") or path.suffix not in (".py", ".js", ".cjs", ".mjs", ".json", ".css", ".html", ".md"):
            continue
        target = repo / path
        if not target.resolve().is_relative_to(repo):
            raise RuntimeError("Source path escaped the checkout")
        result[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    return result


def serve(repo, out):
    # Must precede app imports: app.py creates a default Store at import time.
    if not out.is_relative_to((repo / "output").resolve()) or not out.is_dir():
        raise RuntimeError("Server output must be an existing acceptance round under this checkout/output")
    db = (out / "browser.db").resolve()
    os.environ.clear()
    os.environ.update(clean_environment_from_parent)
    os.environ["YANZHENG_DB_PATH"] = str(db)
    os.environ["YANZHENG_MODEL_PROVIDER"] = "demo"
    sys.dont_write_bytecode = True
    audit = {"database_connections": [], "denied": [], "config_reads": 0, "process_launches": 0}

    def guard(event, args):
        if event == "open" and not isinstance(args[0], int):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.name.startswith(".env") or (
                path.suffix.lower() in (".db", ".sqlite", ".sqlite3") and path != db
            ):
                audit["denied"].append({"event": event, "kind": "config-or-other-database"})
                raise PermissionError("Configuration and other databases are prohibited")
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
            writing = any(x in mode for x in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not path.is_relative_to(out):
                audit["denied"].append({"event": event, "kind": "write-outside-round"})
                raise PermissionError("Acceptance writes must stay in this new round")
        elif event == "sqlite3.connect":
            path = Path(args[0]).resolve()
            if path != db:
                audit["denied"].append({"event": event})
                raise PermissionError("Only the exact acceptance database is allowed")
            audit["database_connections"].append(str(path))
        elif event in ("subprocess.Popen", "os.system"):
            audit["process_launches"] += 1
            audit["denied"].append({"event": event})
            raise PermissionError("Worker subprocesses, including Lean, are prohibited")
        elif event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
                audit["denied"].append({"event": event})
                raise PermissionError("External network is prohibited")
        elif event == "socket.bind":
            address = args[1]
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
                raise PermissionError("Acceptance listeners must stay on loopback")

    sys.addaudithook(guard)
    sys.path[:0] = [str(repo / "webapp"), str(repo / "webapp/tests")]
    import model_service

    class OfflineModel:
        calls = 0

        def status(self):
            return {"provider": "demo", "configured": False, "model": "offline-fixture",
                    "state": "demo", "last_call": None}

        def complete(self, messages):
            from test_direction_proposal import ready
            import engine
            self.calls += 1
            return ready(), {
                "id": "browser-fixture-" + uuid.uuid4().hex, "provider": "fixture", "model": "offline-fixture",
                "requested_model": "offline-fixture", "status": "success", "attempts": 1,
                "started_at": engine.now(), "finished_at": engine.now(), "duration_ms": 0,
                "request_id": "synthetic", "usage": {}, "prompt_version": "fixture",
                "error_code": None, "summary": "Deterministic browser fixture, no model network.",
            }

        def record_result(self, record):
            pass

        def complete_for_theory(self, messages):
            raise RuntimeError("No theory model execution in browser acceptance")

    # Import-time create_app and later /api/status cannot read a real .env.
    model_service.ModelService = OfflineModel
    import lean_service
    lean_service.environment = lambda: {"available": False, "reason": "browser acceptance disables Lean"}
    lean_service.run = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Lean is disabled"))
    import app as product
    from fastapi.responses import JSONResponse
    from test_direction_contract import bound_project

    application = product.app
    assert application.state.store.path == db
    model = application.state.model_service
    assert isinstance(model, OfflineModel)
    store = application.state.store
    routes = {route.name: route.endpoint for route in application.routes if hasattr(route, "endpoint")}
    projects = {}
    for case in ("accept", "reject", "stale", "legacy"):
        value = routes["create_project"](product.Intake(
            initial_idea="浏览器合成验收 " + case + "：固定条件下比较随机低秩方法。", planning_mode="skill"))
        projects[case] = value["id"]
        if case == "reject":
            routes["accept_direction_proposal"](value["id"], value["workflow"]["pending_proposal"]["id"], product.TheoryRequest())
            routes["advance_project"](value["id"], product.Advance(material="合成修订，检查放弃后保留非空地图"))
        elif case == "stale":
            store.update(value["id"], lambda current: current["direction_map"]["intake"]["known"].update(goal="合成上下文已变化"))
        elif case == "legacy":
            record, _ = bound_project()
            for topic in record["topics"]:
                for item in topic["evidence"]:
                    item.pop("note_id", None)
                    item.pop("note_snapshot", None)
            def install_legacy(current):
                current["direction_map"] = record
                current["stage"] = record["stage"]
                current["workflow"]["pending_proposal"] = None
                current["workflow"]["state"] = record["stage"]
            store.update(value["id"], install_legacy)

    run_id = out.name

    @application.middleware("http")
    async def isolated_requests(request, call_next):
        if request.method == "POST" and any(part in request.url.path for part in ("/lean/", "/literature-search", "/theory/", "/experiments")):
            return JSONResponse({"detail": "Outside isolated browser acceptance"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Acceptance-Run"] = run_id
        return response

    @application.get("/__acceptance__/identity")
    def identity():
        return {"run_id": run_id, "pid": os.getpid(), "parent_pid": os.getppid(),
                "database": str(db), "projects": projects,
                "model": "offline-fixture", "model_network_calls": 0, "lean_enabled": False, "audit": audit}

    # An OS-assigned port cannot replace the user's 8097/8104 services.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    write_json(out / "SERVER.json", {"pid": os.getpid(), "parent_pid": os.getppid(), "base": f"http://127.0.0.1:{port}",
                                    "projects": projects, "database": str(db), "run_id": run_id})
    import uvicorn
    try:
        uvicorn.Server(uvicorn.Config(application, log_level="info", access_log=True)).run(sockets=[listener])
    finally:
        write_json(out / "ISOLATION-FINAL.json", identity())
        listener.close()


def run(args):
    repo = Path(__file__).resolve().parents[1]
    out = Path(args.output).resolve()
    if not out.is_relative_to((repo / "output").resolve()) or out == (repo / "output").resolve():
        raise RuntimeError("Use a new round under this checkout/output")
    if out.exists():
        raise RuntimeError("Use a new output directory; existing evidence is never overwritten")
    out.mkdir(parents=True, exist_ok=False)
    before = source_manifest(repo)
    write_json(out / "SOURCE.json", {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "files": before,
    })
    env = clean_environment()
    process = None
    status = {"status": "incomplete", "output": str(out), "scope": "isolated real-browser fixture"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with (out / "server.log").open("x", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()),
                                        "--serve", "--output", str(out)],
                                       cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            write_json(out / "LAUNCH.json", {"launcher_pid": process.pid})
            deadline = time.monotonic() + 30
            last_error = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Isolated server exited: {process.returncode}; see server.log")
                try:
                    info = json.loads((out / "SERVER.json").read_text(encoding="utf-8"))
                    with opener.open(info["base"] + "/__acceptance__/identity", timeout=1) as response:
                        identity = json.load(response)
                    write_json(out / "STARTUP-IDENTITY.json", identity)
                    # Windows venv python.exe may be a forwarding launcher.
                    owned = identity["pid"] == process.pid or identity["parent_pid"] == process.pid
                    if (identity["run_id"] != out.name or identity["pid"] != info["pid"]
                            or identity["database"] != str(out / "browser.db") or not owned):
                        raise RuntimeError("Wrong acceptance server")
                    break
                except (OSError, ValueError) as error:
                    last_error = str(error)
                    time.sleep(0.2)
            else:
                raise RuntimeError("Isolated server not reachable: " + str(last_error))
            with (out / "browser.log").open("x", encoding="utf-8") as browser_log:
                browser_process = subprocess.Popen(
                    [args.node, str(repo / "scripts/direction_browser_checks.mjs"), str(out),
                     args.playwright_module, args.chrome],
                    cwd=repo, env=env, stdout=browser_log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    browser_code = browser_process.wait(timeout=150)
                except subprocess.TimeoutExpired:
                    # Only this still-running child tree is ours. Never search/kill
                    # Chrome by name or touch an existing user's browser.
                    if browser_process.poll() is None:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(browser_process.pid), "/T", "/F"],
                                           stdout=browser_log, stderr=subprocess.STDOUT, timeout=10,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            browser_process.kill()
                        browser_process.wait(timeout=10)
                    raise RuntimeError("Browser deadline exceeded; owned child tree was stopped")
            with opener.open(info["base"] + "/__acceptance__/identity", timeout=3) as response:
                isolation = json.load(response)
            write_json(out / "ISOLATION.json", isolation)
            if browser_code:
                raise RuntimeError(f"Browser checks failed, exit {browser_code}")
            if isolation["audit"]["denied"] or isolation["audit"]["process_launches"]:
                raise RuntimeError("Isolation boundary violation was attempted")
            if source_manifest(repo) != before:
                raise RuntimeError("Source changed during acceptance")
            status["status"] = "passed"
    except Exception as error:
        status.update(status="failed", error=str(error))
        raise
    finally:
        if process and process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        write_json(out / "RESULT.json", status)
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()
                  if p.is_file() and p.suffix in (".json", ".log", ".png")}
        write_json(out / "SHA256SUMS.json", hashes)
    print(json.dumps(status))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--node")
    parser.add_argument("--playwright-module")
    parser.add_argument("--chrome")
    options = parser.parse_args()
    if options.serve:
        clean_environment_from_parent = clean_environment()
        serve(Path(__file__).resolve().parents[1], Path(options.output).resolve())
    else:
        if not all((options.node, options.playwright_module, options.chrome)):
            parser.error("--node, --playwright-module and --chrome are required")
        run(options)
