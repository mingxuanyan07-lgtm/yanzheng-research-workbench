"""Fixed Lean execution and bounded process supervision; never accepts source code.

This is a resource boundary for reviewed local programs, not a sandbox for
hostile code. No application, model configuration, or database module is loaded.
"""
from __future__ import annotations

import ctypes
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "lean" / "short-lemmas"
TOOLCHAIN = "leanprover/lean4:v4.33.1"
LEAN_COMMIT = "819816b2e0a3bf405af45ae5c7af2491d8f5bee6"
MAX_OUTPUT_BYTES = 262_144
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
PROCESS_MEMORY_BYTES = 1536 * 1024 * 1024
APPROVED_AXIOMS = ("Classical.choice", "Quot.sound", "propext")
POLICY_ID = "lean-core-standard-axioms-v1"
REVIEW_LOCK_SHA256 = "89b3b006c2a3e370dc48ac57817fe5b7c26cc0a6a82b878cbc205baf2c768360"
AUDIT_PREFIX = "YANZHENG_LEAN_AUDIT "


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def clean_environment(lean_binary=None):
    """An allowlist: no model credentials, Python/Lean paths or elan overrides."""
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    paths = [str(Path(sys.executable).parent)]
    if lean_binary:
        paths.insert(0, str(Path(lean_binary).parent))
    if os.name == "nt":
        paths.append(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"))
    else:
        paths.extend(("/usr/bin", "/bin"))
    env.update(PATH=os.pathsep.join(paths), PYTHONIOENCODING="utf-8", LEAN_NUM_THREADS="1")
    return env


class _WindowsJob:
    """Kill-on-close job covering the worker and all descendants on Windows."""
    def __init__(self):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Limits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.api.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
        self.api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.api.OpenProcess.restype = w.HANDLE
        self.api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
        self.api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.members = {}
        self.cleanup = {"member_pids": [], "errors": [], "exit_wait_completed": False}
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        # JOB_TIME | ACTIVE_PROCESS | PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE
        limits.BasicLimitInformation.LimitFlags = 0x4 | 0x8 | 0x100 | 0x200 | 0x2000
        limits.BasicLimitInformation.PerJobUserTimeLimit = 40 * 10_000_000
        limits.BasicLimitInformation.ActiveProcessLimit = 4
        limits.ProcessMemoryLimit = PROCESS_MEMORY_BYTES
        limits.JobMemoryLimit = MEMORY_BYTES
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, proc):
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def kill(self):
        # TerminateJobObject/CloseHandle request termination asynchronously.
        # Retain handles to verified members before requesting it: PID lookup
        # after termination can miss an exiting process or find a reused PID.
        from ctypes import wintypes as w
        class ProcessIds(ctypes.Structure):
            _fields_ = [("assigned", w.DWORD), ("count", w.DWORD), ("ids", ctypes.c_size_t * 64)]
        ids = ProcessIds()
        if self.api.QueryInformationJobObject(self.handle, 3, ctypes.byref(ids), ctypes.sizeof(ids), None):
            for pid in ids.ids[:ids.count]:
                if pid in self.members:
                    continue
                handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    if error != 87:  # Already exited before OpenProcess.
                        self.cleanup["errors"].append({"operation": "OpenProcess", "winerror": error})
                    continue
                belongs = w.BOOL()
                checked = self.api.IsProcessInJob(handle, self.handle, ctypes.byref(belongs))
                if checked and belongs.value:
                    self.members[pid] = handle
                else:
                    error = ctypes.get_last_error()
                    self.api.CloseHandle(handle)
                    if not checked:
                        self.cleanup["errors"].append({"operation": "IsProcessInJob", "winerror": error})
        else:
            self.cleanup["errors"].append({"operation": "QueryProcessIds", "winerror": ctypes.get_last_error()})
        self.cleanup["member_pids"] = sorted(self.members)
        return bool(self.api.TerminateJobObject(self.handle, 1))

    def wait_for_exit(self, deadline):
        from ctypes import wintypes as w
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ("user", "kernel", "period_user", "period_kernel")] + [
                        (name, w.DWORD) for name in ("faults", "total", "active", "terminated")]
        accounting = Accounting()
        while True:
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                self.cleanup["errors"].append({"operation": "QueryAccounting", "winerror": ctypes.get_last_error()})
                return False
            self.cleanup["active_processes"] = accounting.active
            if accounting.active == 0:
                break
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))
        for handle in self.members.values():
            remaining_ms = max(0, int((deadline - time.perf_counter()) * 1000))
            if self.api.WaitForSingleObject(handle, remaining_ms) != 0:
                return False
        self.cleanup["exit_wait_completed"] = not self.cleanup["errors"]
        return self.cleanup["exit_wait_completed"]

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
        for handle in self.members.values():
            self.api.CloseHandle(handle)
        self.members.clear()


def _posix_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (40, 41))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _process_snapshot(proc):
    """Best-effort OS facts only; no environment values or application config."""
    if os.name != "nt" or type(getattr(proc, "pid", None)) is not int:
        return {}
    from ctypes import wintypes as w
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = w.HANDLE(int(proc._handle))
    times = [ctypes.c_uint64() for _ in range(4)]
    data = {"pid": proc.pid}
    if api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        data.update(kernel_seconds=times[2].value / 10_000_000,
                    user_seconds=times[3].value / 10_000_000)
    in_job = w.BOOL()
    if api.IsProcessInJob(handle, None, ctypes.byref(in_job)):
        data["in_any_job"] = bool(in_job.value)
    class Memory(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    memory = Memory()
    memory.cb = ctypes.sizeof(memory)
    if ctypes.WinDLL("psapi").GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
        data.update(working_set_bytes=memory.WorkingSetSize, peak_working_set_bytes=memory.PeakWorkingSetSize,
                    private_bytes=memory.PrivateUsage, peak_commit_bytes=memory.PeakPagefileUsage)
    return data


def process_evidence(result):
    """JSON-safe bounded raw transport and process diagnostics."""
    value = {key: result[key] for key in ("exit_code", "timed_out", "output_limited", "start_error",
             "duration_ms", "limiter", "limiter_applied", "tree_termination_requested", "tree_termination_confirmed") if key in result}
    for key in ("phase_ms", "process", "command", "cwd", "reader_complete", "read_errors", "output_observed_bytes", "cleanup"):
        if key in result:
            value[key] = result[key]
    for key in ("stdout", "stderr"):
        data = result.get(key, b"")
        value[key] = {"base64": base64.b64encode(data).decode("ascii"), "bytes": len(data), "sha256": sha256(data)}
    return value


def bounded_process(command, *, input_bytes=b"", cwd, env, timeout_seconds, max_bytes=MAX_OUTPUT_BYTES):
    """Drain pipes with live caps, enforce wall time, and terminate descendants.

    All callers supply fixed trusted commands. The Windows worker reads stdin
    before spawning Lean, so it is assigned to its job before receiving input.
    POSIX children inherit limits set by the isolated worker and share its group.
    """
    started = time.perf_counter()
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    job = None
    proc = None
    assigned = False
    readers = []
    phases = {}
    observed = {"stdout": 0, "stderr": 0}
    read_errors = []
    cleanup_deadline = None
    result = {"exit_code": None, "timed_out": False, "output_limited": False,
              "started": False, "limiter": "windows_job" if os.name == "nt" else "posix_process_group",
              "limiter_applied": False, "start_error": None, "tree_termination_confirmed": None}
    def mark(name):
        phases[name] = round((time.perf_counter() - started) * 1000, 3)
    def read_pipe(pipe, key):
        try:
            # read() waits to fill its buffer, hiding a short over-cap tail
            # until EOF. read1() exposes every available chunk immediately.
            read = getattr(pipe, "read1", pipe.read)
            while data := read(4096):
                observed[key] += len(data)
                if key + "_first_byte" not in phases:
                    mark(key + "_first_byte")
                remaining = max_bytes - len(chunks[key])
                chunks[key].extend(data[:max(0, remaining)])
                if len(data) > remaining:
                    overflow.set()
        except (OSError, ValueError) as exc:
            read_errors.append({"stream": key, "error": type(exc).__name__})
        finally:
            mark(key + "_reader_done")
    def stop():
        nonlocal cleanup_deadline
        if cleanup_deadline is None:
            cleanup_deadline = time.perf_counter() + 3
        if job and assigned:
            result["tree_termination_requested"] = bool(job.kill())
        elif proc and os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                result["tree_termination_confirmed"] = True
            except (ProcessLookupError, PermissionError):
                result["tree_termination_confirmed"] = False
        # Assignment can fail before the process belongs to the job. A failed
        # job kill must likewise never leave the direct process unreaped.
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    try:
        if os.name == "nt":
            job = _WindowsJob()
        mark("limiter_created")
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(cwd), env=env, shell=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                start_new_session=os.name != "nt")
        result["started"] = True
        mark("process_created")
        result["process"] = {"parent_pid": os.getpid(), "before_assignment": _process_snapshot(proc)}
        if job:
            job.assign(proc)
            assigned = True
        result["limiter_applied"] = True
        mark("limiter_assigned")
        readers = [threading.Thread(target=read_pipe, args=(getattr(proc, key), key), daemon=True)
                   for key in chunks]
        for reader in readers:
            reader.start()
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        mark("input_closed")
        deadline = started + timeout_seconds
        while proc.poll() is None:
            if overflow.is_set():
                result["output_limited"] = True
                result["process"]["before_stop"] = _process_snapshot(proc)
                mark("output_limit_detected")
                stop()
                break
            if time.perf_counter() >= deadline:
                result["timed_out"] = True
                result["process"]["before_stop"] = _process_snapshot(proc)
                mark("deadline_detected")
                stop()
                break
            time.sleep(0.01)
        if cleanup_deadline is None:
            cleanup_deadline = time.perf_counter() + 3
        proc.wait(timeout=max(0, cleanup_deadline - time.perf_counter()))
        mark("process_reaped")
        result["process"]["after_exit"] = _process_snapshot(proc)
        # Kill pipe-holding descendants before waiting for EOF. Waiting for
        # readers first burns the drain budget and races pipe.close/read locks.
        if job:
            if not result.get("tree_termination_requested"):
                result["tree_termination_requested"] = bool(job.kill())
            result["tree_termination_confirmed"] = job.wait_for_exit(cleanup_deadline)
            result["cleanup"] = job.cleanup
            if not result["tree_termination_confirmed"]:
                result["start_error"] = "ProcessCleanupIncomplete"
            mark("job_members_exited")
            job.close()
            job = None
        mark("job_closed")
        for reader in readers:
            reader.join(timeout=1)
        result["reader_complete"] = all(not reader.is_alive() for reader in readers)
        if not result["reader_complete"] or read_errors:
            result["start_error"] = "OutputReadIncomplete"
        mark("output_drained")
        result["output_limited"] |= overflow.is_set()
        result["exit_code"] = proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        result["start_error"] = type(exc).__name__
        if proc:
            stop()
            try:
                proc.wait(timeout=max(0, cleanup_deadline - time.perf_counter()))
            except subprocess.SubprocessError:
                pass
            if job and assigned:
                result["tree_termination_confirmed"] = job.wait_for_exit(cleanup_deadline)
                result["cleanup"] = job.cleanup
    finally:
        if job:
            job.close()
        if proc:
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                # Never block close() on a reader's buffered lock after its
                # bounded join expired. The daemon owns that handle until EOF.
                busy_reader = any(reader.is_alive() for reader in readers)
                if pipe and not pipe.closed and (pipe is proc.stdin or not busy_reader):
                    pipe.close()
        mark("cleanup_finished")
    result.update(stdout=bytes(chunks["stdout"]), stderr=bytes(chunks["stderr"]),
                  phase_ms=phases, command=list(command), cwd=str(cwd),
                  read_errors=read_errors, output_observed_bytes=observed,
                  duration_ms=round((time.perf_counter() - started) * 1000, 3))
    return result


def load_cases():
    data = json.loads((PROJECT / "cases.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    if data.get("schema_version") != "1.0" or not isinstance(cases, list):
        raise ValueError("Invalid reviewed registry")
    return {case["case_id"]: case for case in cases}


def reviewed_sources(case):
    lock_path = PROJECT / "reviewed-sources.json"
    lock_bytes = lock_path.read_bytes()
    if sha256(lock_bytes) != REVIEW_LOCK_SHA256:
        raise ValueError("Reviewed source lock mismatch")
    lock = json.loads(lock_bytes)
    files = lock["files"]
    required = {"Audit.lean", "cases.json", "lean-toolchain", "lakefile.toml", "lake-manifest.json", case["file"]}
    if not required.issubset(files):
        raise ValueError("Incomplete reviewed source lock")
    result = []
    for name, expected in sorted(files.items()):
        path = (PROJECT / name).resolve()
        if not path.is_relative_to(PROJECT.resolve()) or not path.is_file():
            raise ValueError("Invalid reviewed source path")
        digest = sha256(path.read_bytes())
        if digest != expected:
            raise ValueError("Reviewed source content changed")
        result.append({"path": name, "sha256": digest})
    return result


def reviewed_text(name, files):
    expected = next(item["sha256"] for item in files if item["path"] == name)
    data = (PROJECT / name).read_bytes()
    if sha256(data) != expected:
        raise ValueError("Reviewed source changed during snapshot")
    return data.decode("utf-8").replace("\r\n", "\n")


def validate_timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.05 <= value <= 30:
        raise ValueError("timeout_seconds must be a finite number from 0.05 to 30")
    return float(value)


def _lean_candidate():
    configured = os.environ.get("YANZHENG_LEAN_BINARY")
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute() or not candidate.is_file():
            return None
        # Resolve an explicitly supplied elan proxy to an already installed
        # toolchain; never let a proxy select or install a global toolchain.
        if (candidate.parent / ("elan.exe" if os.name == "nt" else "elan")).is_file():
            candidate = candidate.parent.parent / "toolchains" / "leanprover--lean4---v4.33.1" / "bin" / candidate.name
        return candidate.resolve() if candidate.is_file() else None
    suffix = "lean.exe" if os.name == "nt" else "lean"
    roots = [Path("D:/Lean/elan"), Path.home() / ".elan"]
    if os.environ.get("ELAN_HOME"):
        roots.insert(0, Path(os.environ["ELAN_HOME"]))
    for root in roots:
        candidate = root / "toolchains" / "leanprover--lean4---v4.33.1" / "bin" / suffix
        if candidate.is_file():
            return candidate.resolve()
    return None


def probe_environment():
    record = {"available": False, "toolchain": TOOLCHAIN, "expected_lean_commit": LEAN_COMMIT,
              "lean_version": None, "lean_commit": None, "binary_sha256": None,
              "dependencies": [{"name": "Lean core / bundled Std", "version": "4.33.1", "commit": LEAN_COMMIT}],
              "mathlib_used": False, "external_packages": [], "automatic_downloads": False}
    if os.name != "nt":
        record["error_code"] = "unsupported_platform"
        return record
    binary = _lean_candidate()
    if not binary:
        record["error_code"] = "toolchain_missing"
        return record
    # Avoid elan selection and downloads: execute a local concrete binary only.
    result = bounded_process([str(binary), "--version"], cwd=PROJECT, env=clean_environment(binary), timeout_seconds=5)
    text = result["stdout"].decode("utf-8", errors="replace").strip()
    record["probe_process"] = process_evidence(result)
    record["lean_version"] = text
    if result["exit_code"] != 0 or "version 4.33.1," not in text or ("commit " + LEAN_COMMIT) not in text:
        record["error_code"] = "toolchain_mismatch" if result["exit_code"] == 0 else "toolchain_probe_failed"
        return record
    if (PROJECT / "lean-toolchain").read_text(encoding="utf-8").strip() != TOOLCHAIN:
        record["error_code"] = "project_toolchain_mismatch"
        return record
    record.update(available=True, lean_commit=LEAN_COMMIT, binary_sha256=sha256(binary.read_bytes()),
                  limiter=result["limiter"], limiter_applied=result["limiter_applied"])
    return record


def _blank_trust():
    return {"policy_id": POLICY_ID, "approved_axioms": list(APPROVED_AXIOMS),
            "audit_completed": False, "passed": False, "axioms": [], "sorry_axioms": [],
            "unapproved_axioms": [], "dependencies": [], "unsafe_dependencies": [],
            "partial_dependencies": [], "extra_dependencies": [], "missing_dependencies": [],
            "kernel_primitive_dependencies": [], "independent_leanchecker_run": False,
            "imported_modules_rechecked": False,
            "trusted_components": ["pinned Lean kernel and bundled compiled imports", "fixed audit command",
                                   "local compiler/runtime and Python supervisor"]}


def _trust(audit):
    result = _blank_trust()
    result.update(audit_completed=audit["audit_complete"], axioms=sorted(audit["axioms"]),
                  sorry_axioms=[value for value in audit["axioms"] if value == "sorryAx"],
                  unapproved_axioms=sorted(set(audit["axioms"]) - set(APPROVED_AXIOMS) - {"sorryAx"}),
                  dependencies=audit["dependencies"], unsafe_dependencies=audit["unsafe_dependencies"],
                  partial_dependencies=audit["partial_dependencies"],
                  extra_dependencies=audit["extra_trust_dependencies"], missing_dependencies=audit["missing_dependencies"],
                  kernel_primitive_dependencies=audit.get("kernel_primitive_dependencies", []))
    result["passed"] = (result["audit_completed"] and not any(result[key] for key in
                        ("sorry_axioms", "unapproved_axioms", "unsafe_dependencies", "partial_dependencies",
                         "extra_dependencies", "missing_dependencies")))
    return result


def _compile(source, binary, timeout_seconds):
    # Keep scratch inside this independent project. Some managed Windows
    # sessions provide a TEMP directory inaccessible to nested child processes.
    scratch = (PROJECT / ".runtime").resolve()
    if not scratch.is_relative_to(PROJECT.resolve()):
        raise OSError("Invalid fixed Lean scratch directory")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=scratch) as directory:
        path = Path(directory) / "Case.lean"
        path.write_text(source, encoding="utf-8", newline="\n")
        env = clean_environment(binary)
        env.update(TMP=directory, TEMP=directory)
        # Trust the pinned bundled imports. In this Windows installation the
        # trust-0 import recheck blocks; new local declarations remain checked
        # by Lean's kernel and the fixed target's axiom closure is still audited.
        command = [str(binary), "-t", "1", "-M", "512", "-j", "1", "-T", "200000", "--json", str(path)]
        return bounded_process(command, cwd=directory, env=env, timeout_seconds=timeout_seconds)


def execute(case_id, *, timeout_seconds=10.0):
    timeout_seconds = validate_timeout(timeout_seconds)
    case = load_cases()[case_id]
    started = time.perf_counter()
    result = {"worker_protocol": "lean-fixed-v1", "case_id": case_id, "status": "unavailable",
              "formal_statement": {"target": case["target"], "source": case["statement"], "elaborated": None,
                                   "elaborated_sha256": None},
              "source": {"sha256": None, "files": [], "reviewed_sources_match": False},
              "environment": {}, "trust": _blank_trust(), "diagnostics": [], "duration_ms": 0,
              "execution": {"compiler_started": False, "exit_code": None, "timed_out": False,
                            "output_limited": False, "resource_limits": {
                                "wall_seconds": timeout_seconds, "compiler_wall_seconds": timeout_seconds, "lean_memory_mb": 512,
                                "job_memory_bytes": MEMORY_BYTES, "process_memory_bytes": PROCESS_MEMORY_BYTES,
                                "cpu_seconds": 40,
                                "output_bytes_per_stream": MAX_OUTPUT_BYTES, "lean_threads": 1,
                                "allocation_limit_per_task": 200000, "kernel_trust_level": 1,
                                "is_general_security_sandbox": False}}}
    def finish(status, code=None):
        result["status"] = status
        if status != "passed":
            result["trust"]["passed"] = False
        if code:
            result["error"] = {"code": code}
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result
    try:
        result["source"]["files"] = reviewed_sources(case)
        result["source"]["reviewed_sources_match"] = True
    except (OSError, ValueError, KeyError, TypeError):
        return finish("unavailable", "reviewed_sources_changed")
    result["environment"] = probe_environment()
    if not result["environment"]["available"]:
        return finish("unavailable", result["environment"].get("error_code", "toolchain_missing"))
    try:
        audit_code = reviewed_text("Audit.lean", result["source"]["files"])
        case_code = reviewed_text(case["file"], result["source"]["files"])
    except (OSError, ValueError):
        return finish("unavailable", "reviewed_sources_changed")
    source = (audit_code.rstrip() + "\n\n" + case_code.rstrip() + "\n\n#yanzheng_audit " + case["target"] + "\n")
    result["source"]["sha256"] = sha256(source.encode("utf-8"))
    # Generated source is exclusively the locked auditor + reviewed body + fixed target.
    binary = _lean_candidate()
    try:
        execution = _compile(source, binary, timeout_seconds)
    except OSError:
        return finish("unavailable", "scratch_or_process_unavailable")
    for key in ("exit_code", "timed_out", "output_limited", "limiter", "limiter_applied", "tree_termination_confirmed"):
        result["execution"][key] = execution[key]
    result["execution"]["compiler_started"] = execution["started"] and execution["limiter_applied"]
    result["execution"]["compiler_duration_ms"] = execution["duration_ms"]
    result["execution"]["process"] = process_evidence(execution)
    result["source"]["compilation_unit"] = source
    text = execution["stdout"].decode("utf-8", errors="replace")
    audits = []
    for line in text.splitlines():
        if line.startswith(AUDIT_PREFIX):
            try:
                audits.append(json.loads(line[len(AUDIT_PREFIX):]))
            except ValueError:
                result["diagnostics"].append({"severity": "error", "message": "Invalid audit payload"})
            continue
        try:
            message = json.loads(line)
            # With --json, Lean captures command IO.println as an information
            # diagnostic. Accept exactly the same fixed marker in its data.
            if isinstance(message, dict) and isinstance(message.get("data"), str) and message["data"].startswith(AUDIT_PREFIX):
                audits.append(json.loads(message["data"][len(AUDIT_PREFIX):].strip()))
                continue
            # Lean diagnostics contain the scratch path; keep only useful positions.
            result["diagnostics"].append({"severity": message.get("severity", "info"),
                                          "message": message.get("data", line),
                                          "position": message.get("pos"), "end_position": message.get("endPos")})
        except (ValueError, AttributeError):
            if line.strip():
                result["diagnostics"].append({"severity": "info", "message": line})
    if execution["stderr"]:
        result["diagnostics"].append({"severity": "error", "message": execution["stderr"].decode("utf-8", errors="replace")})
    if execution["timed_out"]:
        return finish("timeout", "wall_timeout")
    if execution["start_error"] or not execution["limiter_applied"]:
        return finish("unavailable", "resource_limiter_or_process_start_failed")
    if execution["output_limited"]:
        return finish("error", "output_limit")
    if execution["exit_code"] != 0:
        unclosed = any("unsolved goals" in str(item["message"]) for item in result["diagnostics"])
        return finish("incomplete" if unclosed else "error", "unsolved_goals" if unclosed else "lean_error")
    if len(audits) != 1:
        return finish("error", "audit_missing_or_ambiguous")
    audit = audits[0]
    array_fields = ("axioms", "dependencies", "unsafe_dependencies", "partial_dependencies",
                    "extra_trust_dependencies", "missing_dependencies")
    if (not isinstance(audit, dict) or audit.get("schema_version") != "1.0" or audit.get("target") != case["target"]
            or audit.get("declaration_kind") != "theorem" or not isinstance(audit.get("elaborated_type"), str)
            or not audit["elaborated_type"].strip() or type(audit.get("audit_complete")) is not bool
            or any(not isinstance(audit.get(key), list) or any(not isinstance(v, str) for v in audit[key]) for key in array_fields)):
        return finish("error", "invalid_audit")
    result["formal_statement"].update(elaborated=audit["elaborated_type"],
                                      elaborated_sha256=sha256(audit["elaborated_type"].encode("utf-8")))
    result["trust"] = _trust(audit)
    if result["trust"]["sorry_axioms"]:
        return finish("incomplete", "sorry_dependency")
    if result["trust"]["extra_dependencies"] or result["trust"]["unsafe_dependencies"] or result["trust"]["partial_dependencies"]:
        return finish("error", "trust_dependency")
    if result["trust"]["unapproved_axioms"]:
        return finish("error", "unapproved_axiom")
    if not result["trust"]["passed"]:
        return finish("error", "audit_incomplete")
    if any(item["severity"] == "error" for item in result["diagnostics"]):
        return finish("error", "lean_error")
    return finish("passed")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if os.name != "nt":
        _posix_limits()
    raw = sys.stdin.buffer.read(4097)
    try:
        value = json.loads(raw.decode("utf-8"))
        if len(raw) > 4096 or not isinstance(value, dict) or set(value) != {"case_id", "timeout_seconds"}:
            raise ValueError("Invalid fixed-worker input")
        timeout = validate_timeout(value["timeout_seconds"])
        if not isinstance(value["case_id"], str) or value["case_id"] not in load_cases():
            raise ValueError("Unknown reviewed case")
        result = execute(value["case_id"], timeout_seconds=timeout)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        result = {"worker_protocol": "lean-fixed-v1", "status": "error", "error": {"code": "invalid_request"}}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
