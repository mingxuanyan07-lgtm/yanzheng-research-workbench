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
from evidence_registry import Registry
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
        fixture.CheckingTest.setUp(self)
        root = Path(self.tmp.name)
        self.payload = b'<html><script>window.qaExecuted=true</script><p>test_fixture</p></html>'
        self.file = root / 'sample.html'
        self.file.write_bytes(self.payload)
        self.sha = hashlib.sha256(self.payload).hexdigest()
        entry = {"artifact_id":"qa-html", "expected_byte_sha256":self.sha, "expected_bytes":len(self.payload),
            "hash_scope":"exact_saved_file_bytes", "acquired_at":None,
            "acquired_at_status":"unknown_not_inferred_from_mtime", "identity_kind":"test_fixture",
            "provenance":{"source":"test_fixture, not original model input"},
            "locations":[{"path":"sample.html", "expected_byte_sha256":self.sha, "expected_bytes":len(self.payload)}]}
        manifest = {"schema_version":"evidence-registry-manifest-v1", "created_at":"2026-10-18T18:00:00+08:00",
            "read_limit_bytes":1048576, "artifacts":[entry]}
        path=root/'registry.json';path.write_text(json.dumps(manifest),encoding='utf-8')
        self.access=EvidenceAccess(Registry.from_manifest(path,allowed_root=str(root)))
        self.client.close()
        self.app=create_app(self.db,model_client=self.model,lean_client=lambda *_: self.fail('no Lean'),evidence_client=self.access)
        self.client=TestClient(self.app)
        self.r=self.save(self.body(artifact={"artifact_id":"qa-html","original_bytes_sha256":"f"*64,"acquired_at":None},artifact_refs=[{
            "artifact_id":"qa-html","original_bytes_sha256":self.sha,"acquired_at":None,
            "label":"QA HTML","source_version":"v1","locator":"paragraph","support_scope":"no math certification","origin":"test_fixture"}]))
        self.url=self.base+f"/checks/{self.r['id']}/artifacts"

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

    def test_preview_bounds_binary_and_fixed_production_files_are_exact_accepted_copies(self):
        from evidence_registry import ReadResult
        source=self.access.registry.read_verified('qa-html')
        source.bytes=('中'*12001).encode()
        with patch.object(self.access.registry,'read_verified',return_value=source):
            result=self.access.read('qa-html',[])['preview']
        self.assertEqual(len(result['text']),12000);self.assertTrue(result['truncated'])
        source.bytes=b'\x00binary'
        with patch.object(self.access.registry,'read_verified',return_value=source):
            self.assertEqual(self.access.read('qa-html',[])['preview']['status'],'binary_or_non_utf8')
        base=Path(__file__).resolve().parents[1]
        for name,expected in [('evidence_registry.py',MODULE_SHA256),('evidence_registry_manifest.json',MANIFEST_SHA256)]:
            self.assertEqual(hashlib.sha256((base/name).read_bytes()).hexdigest(),expected)
