"""Real bounded-process regressions; fixed local fixtures, no app/database."""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lean_worker

RUN_RECORDS = []


class LeanProcessTest(unittest.TestCase):
    def run_child(self, code, **kwargs):
        result = lean_worker.bounded_process(
            [sys.executable, '-I', '-B', '-c', code],
            cwd=Path(lean_worker.__file__).parent,
            env=lean_worker.clean_environment(), **kwargs)
        RUN_RECORDS.append({'test': self.id(), 'result': lean_worker.process_evidence(result)})
        return result

    def assert_descendant_exited(self, result):
        # Observe the actual child PID printed by Python, not the Windows
        # venv launcher PID. A successful TerminateJobObject call alone does
        # not prove that the descendant has exited.
        pid = int(result['stdout'].decode('ascii').split('descendant=')[1].split()[0])
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            error = ctypes.get_last_error()
            self.assertEqual(error, 87, 'Cannot verify descendant: OpenProcess failed')
            state = 'pid_absent'
        else:
            try:
                self.assertEqual(api.WaitForSingleObject(handle, 0), 0,
                                 'Descendant is still alive after bounded_process returned')
                state = 'process_handle_signaled'
            finally:
                api.CloseHandle(handle)
        RUN_RECORDS[-1]['descendant_observation'] = {'pid': pid, 'state': state}

    def descendant_parent(self, ending):
        child = "import os,time; print(os.getpid(),flush=True); time.sleep(20)"
        return ("import subprocess,sys,time,os; sys.stdin.buffer.read(); "
                "child=subprocess.Popen([sys.executable,'-I','-B','-c'," + repr(child) +
                "],stdout=subprocess.PIPE); "
                "print('descendant='+child.stdout.readline().decode().strip(),flush=True); " + ending)

    def test_partial_chunk_overflow_is_detected_before_wall_timeout(self):
        # One byte over the cap, then no EOF. Buffered read(4096) hides the
        # final byte until the child is killed, misclassifying this as timeout.
        result = self.run_child(
            "import sys,time; sys.stdout.buffer.write(b'x'*4097); sys.stdout.buffer.flush(); time.sleep(5)",
            timeout_seconds=2, max_bytes=4096)
        self.assertTrue(result['output_limited'])
        self.assertFalse(result['timed_out'])
        self.assertEqual(len(result['stdout']),4096)
        self.assertLess(result['duration_ms'],1800)

    def test_exited_parent_with_pipe_holding_child_is_drained_after_job_close(self):
        # Gate descendant creation on stdin, ensuring Job assignment first.
        result = self.run_child(
            "import subprocess,sys; sys.stdin.buffer.read(); "
            "subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(10)']); "
            "print('parent completed',flush=True)", timeout_seconds=3)
        self.assertEqual(result['exit_code'],0)
        self.assertIn(b'parent completed',result['stdout'])
        self.assertLess(result['duration_ms'],1800)

    def test_abnormal_parent_exit_kills_observed_descendant(self):
        result = self.run_child(self.descendant_parent('os._exit(23)'), timeout_seconds=3)
        self.assertEqual(result['exit_code'], 23)
        self.assertFalse(result['timed_out'])
        self.assertTrue(result['reader_complete'])
        self.assertIsNone(result['start_error'])
        self.assert_descendant_exited(result)

    def test_wall_timeout_kills_observed_descendant(self):
        result = self.run_child(self.descendant_parent('time.sleep(20)'), timeout_seconds=2)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['output_limited'])
        self.assertTrue(result['reader_complete'])
        self.assertIsNone(result['start_error'])
        self.assert_descendant_exited(result)


if __name__ == '__main__':
    unittest.main()
