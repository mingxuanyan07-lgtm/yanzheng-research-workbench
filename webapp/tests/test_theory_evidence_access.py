"""Thin integration tests: disposable SQLite and genuine accepted registry on QA files."""
import copy
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
from app import create_app
from evidence_access import EvidenceAccess, MODULE_SHA256, MANIFEST_SHA256
from evidence_registry import Registry, RegistryError
import test_theory_checking as fixture
import theory


class EvidenceIntegrationTest(unittest.TestCase):
    tearDown = fixture.CheckingTest.tearDown
    create = fixture.CheckingTest.create
    view = fixture.CheckingTest.view
    body = fixture.CheckingTest.body
    save = fixture.CheckingTest.save
    opinion = fixture.CheckingTest.opinion

    def setUp(self):
        # Real Win32 reads lock every namespace ancestor. A system TEMP under a
        # protected user profile can deny an ancestor handle in sandboxed runs,
        # even when SQLite and ordinary file I/O there are allowed. Keep these
        # genuine handle fixtures in the explicitly writable checkout instead.
        scratch = Path(__file__).resolve().parent / '.tmp'
        scratch.mkdir(parents=True, exist_ok=True)
        fixture.CheckingTest.setUp(self, temp_dir=scratch)
        self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(scratch.resolve()))
        self.payload = b'<html><script>window.qaExecuted=true</script><p>test_fixture</p></html>'
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.access = EvidenceAccess(self.registry_for_payload(self.payload))
        self.client.close()
        self.app=create_app(self.db,model_client=self.model,lean_client=lambda *_: self.fail('no Lean'),evidence_client=self.access)
        self.client=TestClient(self.app)
        self.r=self.save(self.body(artifact={"artifact_id":"qa-html","original_bytes_sha256":"f"*64,"acquired_at":None},artifact_refs=[{
            "artifact_id":"qa-html","original_bytes_sha256":self.sha,"acquired_at":None,
            "label":"QA HTML","source_version":"v1","locator":"paragraph","support_scope":"no math certification","origin":"test_fixture"}]))
        self.url=self.base+f"/checks/{self.r['id']}/artifacts"

    def registry_for_payload(self, payload):
        root = Path(self.tmp.name)
        self.file = root / 'sample.html'
        self.file.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        entry = {"artifact_id":"qa-html", "expected_byte_sha256":digest, "expected_bytes":len(payload),
            "hash_scope":"exact_saved_file_bytes", "acquired_at":None,
            "acquired_at_status":"unknown_not_inferred_from_mtime", "identity_kind":"test_fixture",
            "provenance":{"source":"test_fixture, not original model input"},
            "locations":[{"path":"sample.html", "expected_byte_sha256":digest, "expected_bytes":len(payload)}]}
        manifest = {"schema_version":"evidence-registry-manifest-v1", "created_at":"2026-10-18T18:00:00+08:00",
            "read_limit_bytes":1048576, "artifacts":[entry]}
        path=root/'registry.json';path.write_text(json.dumps(manifest),encoding='utf-8')
        return Registry.from_manifest(path,allowed_root=str(root))

    def read(self,artifact_id='qa-html'):
        return self.client.post(self.url+'/read',json={"artifact_id":artifact_id})

    def test_describe_never_reads_or_adds_history_and_does_not_expose_execution_paths(self):
        with patch.object(self.access.registry,'read_verified',side_effect=AssertionError('metadata must not read')):
            result=self.client.post(self.url+'/describe',json={"artifact_id":"qa-html"})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['read_status'],'not_checked')
        self.assertNotIn('path',result.json()['locations'][0])
        self.assertEqual(result.json()['locations'][0]['location_index'],0)
        self.assertNotIn('artifact_reads',self.view()['records'][0])

    def test_verified_read_appends_fact_without_replacing_declarations_or_reviews_or_model_scope(self):
        self.client.post(self.base+f"/checks/{self.r['id']}/reviews",json=self.opinion())
        before=copy.deepcopy(self.app.state.store.get(self.p['id']))
        result=self.read();self.assertEqual(result.status_code,200,result.text)
        fact=result.json()['fact'];r=fact['result']
        self.assertEqual(r['status'],'verified');self.assertEqual(r['preview']['text'],self.payload.decode())
        self.assertEqual([v['byte_sha256_comparison'] for v in r['declaration_comparisons']],['mismatch','match'])
        self.assertIsNone(r['acquired_at']);self.assertIsNotNone(r['read_at'])
        after=self.app.state.store.get(self.p['id']);entry=after['theory']['checking_records'][0]
        reads=entry.pop('artifact_reads');self.assertEqual(entry,before['theory']['checking_records'][0])
        self.assertEqual(len(reads),1)
        self.assertEqual(theory.build_messages(before),theory.build_messages(after))
        self.assertEqual(self.view()['records'][0]['stale_reasons'],[])
        self.assertEqual(self.view()['records'][0]['formal_status'],'not_formally_verified')

    def test_cross_project_nonexistent_and_unassociated_rejected_before_registry(self):
        other=self.create()
        with patch.object(self.access,'read',side_effect=AssertionError('association first')):
            for url,status in [(self.url.replace(self.p['id'],other['id']),404),
                               (self.url.replace(self.r['id'],'missing'),404),
                               (self.url.replace(self.p['id'],'missing'),404)]:
                self.assertEqual(self.client.post(url+'/read',json={"artifact_id":"qa-html"}).status_code,status)
            self.assertEqual(self.read('unassociated').status_code,403)
        self.assertNotIn('artifact_reads',self.view()['records'][0])

    def test_client_paths_urls_limits_and_queries_are_not_configuration(self):
        for extra in ({"path":"sample.html"},{"root":"C:/"},{"url":"https://example.com"},{"limit":1}):
            self.assertEqual(self.client.post(self.url+'/read',json={"artifact_id":"qa-html",**extra}).status_code,422)
        for action in ('read','describe'):
            self.assertEqual(self.client.post(self.url+'/'+action+'?path=sample.html',json={"artifact_id":"qa-html"}).status_code,422)
        self.assertEqual(self.client.post(self.url+'/read',json={"artifact_id":1}).status_code,422)

    def test_missing_tampered_and_second_fresh_read_keep_distinct_facts_without_preview(self):
        self.assertEqual(self.read().json()['fact']['result']['status'],'verified')
        self.file.write_bytes(b'X'*len(self.payload))
        second=self.read().json()['fact'];self.assertEqual(second['result']['status'],'hash_mismatch')
        self.assertIsNone(second['result']['preview']['text'])
        self.file.unlink()
        third=self.read().json()['fact'];self.assertEqual(third['result']['status'],'missing')
        self.assertEqual(len(self.view()['records'][0]['artifact_reads']),3)
        self.assertEqual(len({f['id'] for f in self.view()['records'][0]['artifact_reads']}),3)

    def test_associated_unknown_stays_unregistered_and_missing_acquisition_not_inferred(self):
        r=self.save(self.body(request_id='second',artifact={"artifact_id":"qa-unknown","original_bytes_sha256":None,"acquired_at":None}))
        url=self.base+f"/checks/{r['id']}/artifacts"
        self.assertEqual(self.client.post(url+'/describe',json={"artifact_id":"qa-unknown"}).json()['status'],'unregistered')
        result=self.client.post(url+'/read',json={"artifact_id":"qa-unknown"}).json()['fact']['result']
        self.assertEqual(result['status'],'unregistered');self.assertIsNone(result['acquired_at'])
        self.assertIsNone(result['preview']['text'])

    def test_unavailable_is_saved_failure_fact_and_does_not_erase_existing_record(self):
        self.access.registry=None
        self.assertEqual(self.client.post(self.url+'/describe',json={"artifact_id":"qa-html"}).status_code,503)
        result=self.read();self.assertEqual(result.status_code,503)
        self.assertTrue(result.json()['saved']);self.assertEqual(result.json()['fact']['result']['status'],'registry_unavailable')
        self.assertEqual(self.view()['records'][0]['material'],self.r['material'])

    def test_save_failure_returns_unsaved_fact_and_restores_on_next_explicit_read(self):
        with patch.object(self.app.state.store,'update',side_effect=sqlite3.OperationalError('QA write denied')):
            failed=self.read()
        self.assertEqual(failed.status_code,503);self.assertFalse(failed.json()['saved'])
        self.assertEqual(failed.json()['fact']['result']['status'],'verified')
        self.assertNotIn('artifact_reads',self.view()['records'][0])
        self.assertTrue(self.read().json()['saved'])

    def test_association_rechecked_after_io_before_persistence(self):
        real=self.access.read
        def change(*args):
            result=real(*args)
            def mutate(p):
                p['theory']['checking_records'][0]['material']['artifact']=None
                p['theory']['checking_records'][0]['material']['artifact_refs']=[]
            self.app.state.store.update(self.p['id'],mutate)
            return result
        with patch.object(self.access,'read',side_effect=change):
            result=self.read()
        self.assertEqual(result.status_code,503);self.assertFalse(result.json()['saved'])

    def test_restart_single_and_whole_exports_retain_exact_fact_and_old_card_warning(self):
        self.app.state.store.update(self.p['id'],lambda p:p['theory'].update(card_version=99))
        fact=self.read().json()['fact'];self.assertIn('card_changed',fact['stale_reasons_at_read'])
        self.client.close()
        self.client=TestClient(create_app(self.db,model_client=self.model,evidence_client=self.access))
        self.assertEqual(self.view()['records'][0]['artifact_reads'],[fact])
        for path in (self.base+f"/checks/{self.r['id']}/export",self.base+'/export'):
            data=self.client.get(path+'?format=json').json()
            entry=data['checking']['records'][0] if 'checking' in data else data
            self.assertEqual(entry['artifact_reads'],[fact])
            md=self.client.get(path+'?format=md').text
            entries=[json.loads(raw) for raw in re.findall(r'```json\n(.*?)\n```',md,re.S)]
            self.assertTrue(any(isinstance(e,dict) and e.get('artifact_reads')==[fact] for e in entries))
            self.assertNotIn('<script>',md)

    def test_preview_bounds_binary_and_production_pin_gate(self):
        # These are real registered bytes, not a result with a mutated payload
        # whose expected hash still describes a different original file.
        self.access.registry = self.registry_for_payload(('中'*12001).encode())
        result=self.access.read('qa-html',[])['preview']
        self.assertEqual(len(result['text']),12000);self.assertTrue(result['truncated'])
        self.access.registry = self.registry_for_payload(b'\x00binary')
        self.assertEqual(self.access.read('qa-html',[])['preview']['status'],'binary_or_non_utf8')
        self.access.registry = self.registry_for_payload(b'')
        self.assertEqual(self.access.read('qa-html',[])['preview']['text'],'')
        base=Path(__file__).resolve().parents[1]
        self.assertEqual(hashlib.sha256((base/'evidence_registry_manifest.json').read_bytes()).hexdigest(),MANIFEST_SHA256)
        current=hashlib.sha256((base/'evidence_registry.py').read_bytes()).hexdigest()
        production=EvidenceAccess()
        if current != MODULE_SHA256:
            # A may edit its owned module before acceptance. Do not adopt it merely to make this green.
            self.assertFalse(production.metadata()['available'])
            self.assertEqual(production.metadata()['availability_reason'],'accepted_module_hash_mismatch')
        self.assertEqual(production.metadata()['accepted_module_sha256'],MODULE_SHA256)

    def injected_failure(self, status="path_rejected"):
        """Trusted injection, not a real OS path/handle experiment."""
        result = self.access.registry.read_verified('qa-html')
        result.status = status
        result.byte_match = False
        result.locations = [{"path": "C:/qa-private-root/outside.bin", "status": status,
            "reason": "failure at C:/qa-private-root/outside.bin", "provenance": {"source": "test_fixture declaration"}}]
        result.caveats = ["failure at /qa-private-root/outside.bin", "byte_match_does_not_prove_source_authenticity"]
        original = result.as_dict
        result.as_dict = lambda: {**original(), "resolved_root": "C:/qa-private-root", "error": "trace /qa-private-root"}
        return result

    def test_boundary_structured_path_rejection_hides_diagnostics_and_failure_bytes(self):
        injected = self.injected_failure()
        with patch.object(self.access.registry, 'read_verified', return_value=injected):
            response = self.read()
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('qa-private-root',response.text)
        result=response.json()['fact']['result']
        self.assertEqual(result['status'],'path_rejected')
        self.assertFalse(result['bytes_returned'])
        self.assertIsNone(result['preview']['text'])
        self.assertTrue(response.json()['saved'])

    def test_boundary_root_replacement_policy_exception_becomes_saved_rejection(self):
        with patch.object(self.access.registry,'read_verified',side_effect=RegistryError('root replaced: C:/qa-private-root')):
            response=self.read()
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()['fact']['result']
        self.assertEqual(result['status'],'path_rejected')
        self.assertEqual(result['result_origin'],'adapter_exception_mapping')
        self.assertIsNone(result['preview']['text'])
        self.assertNotIn('qa-private-root',response.text)

    def test_boundary_IO_exceptions_are_saved_unreadable_without_preview_or_paths(self):
        self.client.close();self.client=TestClient(self.app,raise_server_exceptions=False)
        for error in [PermissionError('C:/qa-private-root'),FileNotFoundError('/qa-private-root'),OSError('IO C:/qa-private-root')]:
            with self.subTest(exception=type(error).__name__):
                with patch.object(self.access.registry,'read_verified',side_effect=error):
                    response=self.read()
                self.assertEqual(response.status_code,200,response.text)
                result=response.json()['fact']['result']
                self.assertEqual(result['status'],'unreadable')
                self.assertEqual(result['diagnostic_code'],'registry_read_io_error')
                self.assertIsNone(result['preview']['text'])
                self.assertFalse(result['bytes_returned'])
                self.assertNotIn('qa-private-root',response.text)

    def test_boundary_structured_unreadable_error_does_not_expose_execution_details(self):
        with patch.object(self.access.registry,'read_verified',return_value=self.injected_failure('unreadable')):
            response=self.read()
        self.assertNotIn('qa-private-root',response.text)
        self.assertEqual(response.json()['fact']['result']['status'],'unreadable')

    def test_boundary_missing_helper_import_during_initialization_fails_closed(self):
        def missing_helper():
            raise ImportError('missing helper C:/qa-private-root')
        access=EvidenceAccess(registry_factory=missing_helper)
        self.assertFalse(access.metadata()['available'])
        self.assertEqual(access.metadata()['availability_reason'],'registry_initialization_failed')
        self.assertNotIn('qa-private-root',json.dumps(access.metadata()))

    def test_boundary_hash_mismatch_keeps_old_pin_and_reports_unavailable(self):
        with patch('evidence_access.Path.read_bytes',return_value=b'not the accepted module'):
            access=EvidenceAccess()
        self.assertFalse(access.metadata()['available'])
        self.assertEqual(access.metadata()['availability_reason'],'accepted_module_hash_mismatch')
        self.assertEqual(access.metadata()['accepted_module_sha256'],MODULE_SHA256)

    def test_boundary_describe_service_error_is_not_read_history(self):
        self.client.close();self.client=TestClient(self.app,raise_server_exceptions=False)
        with patch.object(self.access.registry,'describe',side_effect=OSError('C:/qa-private-root')):
            response=self.client.post(self.url+'/describe',json={'artifact_id':'qa-html'})
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(response.json()['status'],'registry_unavailable')
        self.assertNotIn('qa-private-root',response.text)
        self.assertNotIn('artifact_reads',self.view()['records'][0])

    def test_boundary_unexpected_service_exception_records_failure_not_success(self):
        self.client.close();self.client=TestClient(self.app,raise_server_exceptions=False)
        with patch.object(self.access.registry,'read_verified',side_effect=RuntimeError('C:/qa-private-root')):
            response=self.read()
        self.assertEqual(response.status_code,503,response.text)
        self.assertTrue(response.json()['saved'])
        self.assertEqual(response.json()['fact']['result']['status'],'registry_unavailable')
        self.assertNotIn('qa-private-root',response.text)

    def test_boundary_contradictory_verified_results_do_not_attest_or_preview(self):
        for condition in ['missing_bytes','no_byte_match','wrong_artifact']:
            with self.subTest(condition=condition):
                value=self.access.registry.read_verified('qa-html')
                if condition=='missing_bytes':value.bytes=None
                elif condition=='no_byte_match':value.byte_match=False
                else:value.artifact_id='different-artifact'
                with patch.object(self.access.registry,'read_verified',return_value=value):
                    response=self.read()
                result=response.json()['fact']['result']
                self.assertEqual(result['status'],'unreadable')
                self.assertEqual(result['diagnostic_code'],'inconsistent_registry_result')
                self.assertIsNone(result['preview']['text'])

    def test_boundary_verified_payload_and_both_hash_size_facts_must_agree(self):
        for condition in ('payload_same_size', 'actual_hash', 'actual_size', 'expected_hash',
                          'expected_size', 'boolean_size', 'bytes_not_returned'):
            with self.subTest(condition=condition):
                value = self.access.registry.read_verified('qa-html')
                self.assertEqual(value.status, 'verified', value.as_dict())
                if condition == 'payload_same_size': value.bytes = b'X' * len(value.bytes)
                elif condition == 'actual_hash': value.actual['byte_sha256'] = 'f' * 64
                elif condition == 'actual_size': value.actual['bytes'] += 1
                elif condition == 'expected_hash': value.expected['byte_sha256'] = 'f' * 64
                elif condition == 'expected_size': value.expected['bytes'] += 1
                elif condition == 'boolean_size': value.actual['bytes'] = True
                else:
                    as_dict = value.as_dict
                    value.as_dict = lambda: {**as_dict(), 'bytes_returned': False}
                with patch.object(self.access.registry, 'read_verified', return_value=value):
                    response = self.read()
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()['fact']['result']
                self.assertEqual(result['status'], 'unreadable')
                self.assertEqual(result['diagnostic_code'], 'inconsistent_registry_result')
                self.assertIsNone(result['preview']['text'])
                self.assertFalse(result['bytes_returned'])
                self.assertIsNone(result['byte_match'])

    def test_boundary_injection_is_not_production_module_acceptance(self):
        metadata=self.access.metadata()
        self.assertEqual(metadata['registry_source'],'trusted_in_process_injection')
        self.assertFalse(metadata['production_module_accepted'])
        self.assertEqual(metadata['accepted_module_sha256'],MODULE_SHA256)

    def test_boundary_failure_preserves_history_context_and_single_whole_exports(self):
        self.client.post(self.base+f"/checks/{self.r['id']}/reviews",json=self.opinion())
        before=copy.deepcopy(self.app.state.store.get(self.p['id']))
        with patch.object(self.access.registry,'read_verified',return_value=self.injected_failure('unreadable')):
            response=self.read()
        self.assertEqual(response.status_code,200,response.text)
        after=self.app.state.store.get(self.p['id']);item=after['theory']['checking_records'][0]
        facts=item.pop('artifact_reads');self.assertEqual(item,before['theory']['checking_records'][0])
        self.assertEqual(theory.next_context(before)['messages'],theory.next_context(after)['messages'])
        for path in [self.base+f"/checks/{self.r['id']}/export",self.base+'/export']:
            for fmt in ['json','md']:
                exported=self.client.get(path+'?format='+fmt)
                self.assertEqual(exported.status_code,200)
                self.assertNotIn('qa-private-root',exported.text)
                self.assertIn('unreadable',exported.text)
        self.assertEqual(facts[0]['result']['status'],'unreadable')
