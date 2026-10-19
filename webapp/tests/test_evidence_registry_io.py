"""Deterministic Windows namespace races; every fixture is in the A output root.

Set EVIDENCE_IO_TEST_ROOT to an unused per-run directory under output/lean-reliability.
Timing hooks call real Win32 operations; error-injection tests are explicitly separate.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import evidence_registry as er

DATA = b"same-byte outside decoy is still unauthorized\x00\xff\n"


class EvidenceRegistryIOTests(unittest.TestCase):
    def setUp(self):
        self.scope = Path(os.environ['EVIDENCE_IO_TEST_ROOT']).absolute()
        project = Path(__file__).resolve().parents[2]
        self.assertTrue(self.scope.is_relative_to(project/'output/lean-reliability'))
        self.scope.mkdir(parents=True, exist_ok=True)
        self.base = self.scope/self._testMethodName
        self.base.mkdir(exist_ok=False)
        self.root = self.base/'container/root'
        self.leaf = self.root/'sub/one.bin'
        self.leaf.parent.mkdir(parents=True);self.leaf.write_bytes(DATA)
        self.outside = self.base/'outside';self.outside.mkdir()
        self.events=[];self.links=[]
        self.registry=self.make_registry()
        self.assertEqual(os.name,'nt','This suite requires actual Windows handles')

    def tearDown(self):
        for link,target,is_dir in reversed(self.links):
            self.assertTrue(link.absolute().is_relative_to(self.scope))
            self.assertEqual(link.resolve(),target.resolve())
            if is_dir:os.rmdir(link)
            else:link.unlink()
        (self.base/'events.json').write_text(json.dumps(self.events,ensure_ascii=False,indent=2),encoding='utf-8')

    def make_registry(self, data=DATA, limit=4096):
        h=hashlib.sha256(data).hexdigest()
        m={'schema_version':er.MANIFEST_SCHEMA,'created_at':'2026-10-19T00:00:00+08:00',
           'allowed_root':str(self.root),'read_limit_bytes':limit,'artifacts':[{
           'artifact_id':'fixture:one','expected_byte_sha256':h,'expected_bytes':len(data),
           'hash_scope':er.HASH_SCOPE,'acquired_at':None,'acquired_at_status':'unknown_not_inferred_from_mtime',
           'identity_kind':'test_fixture','locations':[{'path':'sub/one.bin','expected_byte_sha256':h,'expected_bytes':len(data)}]}]}
        p=self.base/'manifest.json';p.write_text(json.dumps(m),encoding='utf-8')
        return er.Registry.from_manifest(p)

    def safe(self,*paths):
        for p in paths:
            self.assertTrue(p.absolute().is_relative_to(self.scope))
            self.assertTrue(p.resolve().is_relative_to(self.scope))

    def create_link(self, link, target, kind, is_dir=True):
        self.safe(link,target)
        if kind=='junction':
            proc=subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(target)],capture_output=True)
            self.events.append({'link_creation':kind,'exit_code':proc.returncode,
                'stdout_hex':proc.stdout.hex(),'stderr_hex':proc.stderr.hex()})
            if proc.returncode:
                detail=proc.stderr.decode('mbcs',errors='replace').strip().splitlines()
                # Capability denial is explicitly unverified, never a synthetic pass:
                # reparse behaviour at this position remains untested in this environment.
                self.skipTest('real junction creation denied (OS error): rc=%s %s; '
                              'reparse coverage at this position remains unverified'
                              % (proc.returncode,detail[-1] if detail else ''))
        else:
            try:os.symlink(target,link,target_is_directory=is_dir)
            except OSError as e:
                self.events.append({'link_creation':kind,'winerror':getattr(e,'winerror',None)})
                self.skipTest('real symlink creation denied (OS error): '+str(e)
                              +'; symlink coverage at this position remains unverified')
        self.links.append((link,target,is_dir))

    # A closed native handle must never be queryable. Windows documents the
    # failure as FALSE + ERROR_INVALID_HANDLE(6); some hardened/sandboxed
    # processes surface the same probe as an NTSTATUS 0xC0000008 OSError.
    # Only those exact invalid-handle signals count as "closed".
    _ERROR_INVALID_HANDLE=6
    _STATUS_INVALID_HANDLE=0xC0000008

    def assert_handle_queryable(self, api, guard, path):
        """Contrast: a currently open handle must be queryable and return True."""
        handle=api.CreateFileW(str(path),0x80,1,None,3,0x02000000|0x00200000,None)
        self.assertNotEqual(handle,guard.c.c_void_p(-1).value,'open-handle contrast could not be created')
        try:self.assertTrue(api.GetFileInformationByHandle(handle,ctypes.byref(guard.Info())),
                            'currently open native handle was not queryable')
        finally:self.assertTrue(api.CloseHandle(handle),'open-handle contrast was not closed')

    def assert_handle_closed(self, api, guard, handle):
        """Prove a native handle is closed without swallowing real OSErrors.

        Returns the exact signal form observed for evidence. Any OSError that is
        not exactly NTSTATUS 0xC0000008 (STATUS_INVALID_HANDLE) is a real failure
        and is re-raised as an assertion error, never treated as success.
        """
        info=guard.Info()
        try:returned=api.GetFileInformationByHandle(handle,ctypes.byref(info))
        except OSError as error:
            winerror=getattr(error,'winerror',None)
            if not isinstance(winerror,int) or (winerror&0xFFFFFFFF)!=self._STATUS_INVALID_HANDLE:
                raise AssertionError('unexpected OSError while probing a closed native handle: %r' % (error,)) from error
            return 'ntstatus_invalid_handle_oserror'
        self.assertFalse(returned,'closed native handle was still queryable')
        self.assertEqual(ctypes.get_last_error(),self._ERROR_INVALID_HANDLE)
        return 'false_with_error_invalid_handle'

    def checked_read(self, hook=None):
        original_open=er._WindowsFileGuard._open;original_read=os.read
        handles=[];fds=[];reads=[];api_holder=[]
        def open_handle(guard,path,**kw):
            h=original_open(guard,path,**kw);handles.append(h);api_holder.append(guard)
            return h
        def read_fd(fd,n):
            if fd not in fds:fds.append(fd)
            if hook:hook(fd)
            # Query actual handle path before the real byte read; content equality
            # cannot distinguish an outside object, handle namespace can.
            import msvcrt
            actual=api_holder[-1]._final_name(msvcrt.get_osfhandle(fd))
            data=original_read(fd,n)
            reads.append({'actual_handle_path':actual,'bytes':len(data),'outside': '\\outside\\' in actual})
            return data
        with patch.object(er._WindowsFileGuard,'_open',new=open_handle),patch.object(er.os,'read',side_effect=read_fd):
            result=self.registry.read_verified('fixture:one')
        self.assertFalse(any(x['outside'] and x['bytes'] for x in reads))
        for fd in fds:
            with self.assertRaises(OSError):os.fstat(fd)
        closed_forms=[]
        if api_holder:
            g=api_holder[-1]
            self.assert_handle_queryable(g.api,g,self.scope)
            closed_forms=[self.assert_handle_closed(g.api,g,h) for h in handles]
        self.events.append({'status':result.status,'reason':result.locations[0]['reason'],
            'reads':reads,'outside_bytes_read':0,'native_handles_opened':len(handles),
            'all_native_handles_closed':True,'closed_handle_signal_forms':sorted(set(closed_forms)),
            'result':result.as_dict()})
        return result

    def swap_before_guard(self, position, kind):
        target={'above_root':self.root.parent,'root':self.root,'ancestor':self.leaf.parent,'leaf':self.leaf}[position]
        is_dir=position!='leaf'
        decoy=self.outside/'target'
        if is_dir:
            decoy.mkdir();relative=self.leaf.relative_to(target)
            (decoy/relative).parent.mkdir(parents=True,exist_ok=True);(decoy/relative).write_bytes(DATA)
        else:decoy.write_bytes(DATA)
        old_open=er._WindowsFileGuard.open
        def injected(guard,*args):
            self.safe(target,decoy);target.rename(target.with_name(target.name+'-original'))
            if kind in ('junction','symlink'):self.create_link(target,decoy,kind,is_dir)
            elif is_dir:
                # New ordinary directory at authorized namespace (not a link).
                target.mkdir();relative=self.leaf.relative_to(target)
                (target/relative).parent.mkdir(parents=True,exist_ok=True);(target/relative).write_bytes(DATA)
            else:target.write_bytes(DATA)
            return old_open(guard,*args)
        with patch.object(er._WindowsFileGuard,'open',new=injected):r=self.checked_read()
        if kind in ('junction','symlink') or position in ('root','above_root'):
            self.assertEqual(r.status,'path_rejected');self.assertIsNone(r.bytes)
            self.assertEqual(self.events[-1]['reads'],[])
        else:self.assertEqual(r.status,'verified')

    def test_normal_utf8_binary_and_json_contract(self):
        for data in ('文本原件\n'.encode(),DATA,b''):
            self.leaf.write_bytes(data);self.registry=self.make_registry(data)
            r=self.checked_read();self.assertEqual(r.bytes,data);self.assertEqual(r.status,'verified')
            self.assertNotIn('bytes',r.as_dict());self.assertTrue(r.as_dict()['bytes_returned'])

    def test_locked_chain_denies_rename_and_write_until_read_finishes(self):
        attempted=[]
        def hook(fd):
            if attempted:return
            for p in (self.root.parent,self.root,self.leaf.parent,self.leaf):
                dst=p.with_name(p.name+'-moved');self.safe(p,dst)
                try:p.rename(dst)
                except OSError as e:attempted.append({'path':str(p),'winerror':getattr(e,'winerror',None)})
                else:self.fail('namespace rename succeeded while pinned: '+str(p))
            with self.assertRaises(OSError):self.leaf.write_bytes(b'outside')
        self.assertEqual(self.checked_read(hook).status,'verified')
        self.assertEqual(len(attempted),4);self.events.append({'real_denied_renames':attempted})
        # Cleanup proof: after return ordinary rename is possible again.
        moved=self.leaf.with_name('closed.bin');self.safe(self.leaf,moved);self.leaf.rename(moved);moved.rename(self.leaf)

    def test_swap_after_leaf_open_before_handle_validation_is_denied(self):
        info=er._WindowsFileGuard._info;attempted=[]
        def injected(g,h):
            if len(g.handles)==len(list(er._path_prefixes(self.leaf))):
                try:self.leaf.rename(self.leaf.with_name('moved.bin'))
                except OSError as e:attempted.append(getattr(e,'winerror',None))
                else:self.fail('leaf moved after handle acquisition')
            return info(g,h)
        with patch.object(er._WindowsFileGuard,'_info',new=injected):self.assertEqual(self.checked_read().status,'verified')
        self.assertTrue(attempted)

    def test_component_swapped_immediately_before_its_open(self):
        old=er._WindowsFileGuard._open;done=[]
        decoy=self.outside/'target';decoy.mkdir();(decoy/'one.bin').write_bytes(DATA)
        def injected(g,p,**kw):
            if Path(p)==self.leaf.parent and not done:
                done.append(True);self.safe(p,decoy);p.rename(p.with_name('old-sub'))
                self.create_link(p,decoy,'junction')
            return old(g,p,**kw)
        # checked_read wraps _open too, preserving this real mutation injection.
        with patch.object(er._WindowsFileGuard,'_open',new=injected):r=self.checked_read()
        self.assertEqual(r.status,'path_rejected');self.assertEqual(self.events[-1]['reads'],[])

    def test_hardlink_same_bytes_outside_rejected_before_read(self):
        decoy=self.outside/'same.bin';decoy.write_bytes(DATA)
        self.safe(self.leaf,decoy);self.leaf.unlink()
        try:os.link(decoy,self.leaf)
        except OSError as error:
            self.events.append({'hardlink_creation_denied':{'winerror':getattr(error,'winerror',None),
                'strerror':error.strerror}})
            # Capability denial is explicitly unverified, never a synthetic pass.
            self.skipTest('real hardlink creation denied (OS error): '+repr(error)
                          +'; outside same-bytes hardlink rejection remains unverified')
        r=self.checked_read();self.assertEqual(r.status,'path_rejected');self.assertEqual(self.events[-1]['reads'],[])

    def test_missing_directory_hash_and_size_limits(self):
        self.leaf.unlink();self.assertEqual(self.checked_read().status,'missing')
        self.leaf.mkdir();self.assertEqual(self.checked_read().status,'not_a_regular_file');self.leaf.rmdir()
        self.leaf.write_bytes(b'x'*len(DATA));self.assertEqual(self.checked_read().status,'hash_mismatch')
        self.leaf.write_bytes(b'x');self.assertEqual(self.checked_read().status,'size_mismatch')
        self.leaf.write_bytes(b'x'*4097);self.assertEqual(self.checked_read().status,'too_large')
        self.registry=self.make_registry(b'x'*4097);self.assertEqual(self.checked_read().status,'too_large')

    def test_io_error_and_programming_error_close_real_handles(self):
        for error in (OSError(5,'synthetic read fault'),TypeError('synthetic programming error')):
            native_open=er._WindowsFileGuard._open;seen=[]
            def record(g,p,**kw):
                h=native_open(g,p,**kw);seen.append((g,h));return h
            with patch.object(er._WindowsFileGuard,'_open',new=record),patch.object(er.os,'read',side_effect=error):
                if isinstance(error,OSError):
                    r=self.registry.read_verified('fixture:one');self.assertEqual(r.status,'unreadable');self.assertIsNone(r.bytes)
                else:
                    with self.assertRaises(TypeError):self.registry.read_verified('fixture:one')
            for g,h in seen:
                self.assert_handle_closed(g.api,g,h)
            self.events.append({'injected_error':type(error).__name__,'native_handles_closed':len(seen)})

    def test_closed_handle_diagnostic_accepts_only_exact_invalid_handle_signal(self):
        """The closed-handle proof must accept only the two exact invalid-handle forms.

        The OSError forms are injected here; real closed handles (whichever of the
        two representations the host process produces) are exercised by every
        checked_read() call above. A currently open handle is the control.
        """
        g=er._WindowsFileGuard()
        class Probe:
            def __init__(self,result):self.result=result
            def GetFileInformationByHandle(self,handle,pointer):
                if isinstance(self.result,OSError):raise self.result
                return self.result
        # Documented Win32 form: FALSE + last error ERROR_INVALID_HANDLE(6).
        ctypes.set_last_error(self._ERROR_INVALID_HANDLE)
        self.assertEqual(self.assert_handle_closed(Probe(0),g,0),'false_with_error_invalid_handle')
        # Hardened-process form: OSError carrying NTSTATUS 0xC0000008. The
        # negative winerror is exactly the shape in the supervisor's raw traceback.
        exact=ctypes.WinError(-1073741816)
        self.assertEqual(exact.winerror,-1073741816)
        self.assertEqual(self.assert_handle_closed(Probe(exact),g,0),'ntstatus_invalid_handle_oserror')
        # Any other OSError is a real failure and must never be swallowed.
        for other in (OSError(5,'synthetic read fault'),PermissionError(13,'denied'),
                      ctypes.WinError(-1073741815)):
            with self.assertRaises(AssertionError):self.assert_handle_closed(Probe(other),g,0)
        # Control: a currently open handle must still return True.
        self.assert_handle_queryable(g.api,g,self.scope)
        self.events.append({'closed_handle_signal_forms_accepted':['false_with_error_invalid_handle',
            'ntstatus_invalid_handle_oserror'],
            'other_oserror_rejected':['WinError5','WinError13','NTSTATUS0xC0000009'],
            'valid_open_handle_contrast':True})

    def test_identity_query_failure_closes_and_never_reads(self):
        with patch.object(er._WindowsFileGuard,'_identity',side_effect=er.RegistryError('SECURE_OPEN_NO_IDENTITY')):
            r=self.checked_read()
        self.assertEqual(r.status,'path_rejected');self.assertEqual(self.events[-1]['reads'],[])

    def test_unsupported_platform_fails_closed(self):
        with patch.object(er.os,'name','unsupported'):
            with self.assertRaisesRegex(er.RegistryError,'UNSUPPORTED_PLATFORM'):er._WindowsFileGuard()
        self.events.append({'substitute_test':'unsupported platform branch; no claim of non-Windows OS validation'})

    def test_leaf_replaced_by_directory_junction(self):
        old=er._WindowsFileGuard.open
        def injected(g,*args):
            self.safe(self.leaf,self.outside)
            self.leaf.rename(self.leaf.with_name('original.bin'))
            self.create_link(self.leaf,self.outside,'junction',True)
            return old(g,*args)
        with patch.object(er._WindowsFileGuard,'open',new=injected):r=self.checked_read()
        self.assertEqual(r.status,'path_rejected');self.assertEqual(self.events[-1]['reads'],[])

    def test_existing_writer_denies_read_without_bytes(self):
        g=er._WindowsFileGuard()
        h=g.api.CreateFileW(str(self.leaf),0x40000000,7,None,3,0,None)
        self.assertNotEqual(h,g.c.c_void_p(-1).value)
        try:
            r=self.checked_read()
            self.assertEqual(r.status,'unreadable');self.assertEqual(self.events[-1]['reads'],[])
        finally:self.assertTrue(g.api.CloseHandle(h))
        self.events.append({'actual_writer_handle_closed':True,'writer_did_not_write':True})

    def test_missing_native_api_fails_closed(self):
        with patch.object(ctypes,'WinDLL',return_value=object()):r=self.checked_read()
        self.assertEqual(r.status,'path_rejected');self.assertEqual(self.events[-1]['reads'],[])
        self.assertIn('UNSUPPORTED_API',self.events[-1]['reason'])
        self.events.append({'substitute_test':'missing native function; actual Windows availability not altered'})

    def test_final_path_query_failure_closes_and_never_reads(self):
        with patch.object(er._WindowsFileGuard,'_final_name',side_effect=er.RegistryError('SECURE_OPEN_NO_FINAL_PATH')):
            r=self.checked_read()
        self.assertEqual(r.status,'path_rejected');self.assertEqual(self.events[-1]['reads'],[])


def _race(position,kind):
    def check(self):self.swap_before_guard(position,kind)
    return check
for _position in ('above_root','root','ancestor','leaf'):
    for _kind in ('ordinary','symlink','junction'):
        if _position=='leaf' and _kind=='junction':continue
        setattr(EvidenceRegistryIOTests,'test_preopen_'+_position+'_'+_kind,_race(_position,_kind))

if __name__=='__main__':unittest.main()
