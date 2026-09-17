"""Local console acceptance tests. Fake inputs, fake network, temporary workspace only."""
import csv
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import test_delivery
import project_config as pc
from sku_detect import detect
from webui import app as web
from webui import workflow as wf


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_delivery.DeliveryTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.root.joinpath('VERSION.txt').write_text('1.1.0')
        self.root.joinpath('revflow').mkdir()
        self.root.joinpath('revflow/getdetail.curl.txt').write_text('curl "https://cl.aosom.cloud/GetDetail?keyValue=A"')
        for module in (web, wf, detect):
            patcher = patch.object(module, 'ROOT', self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = wf.JobManager()
        self.app = web.create_app(self.manager)
        self.client = self.app.test_client()
        self.headers = {'X-Console-Token': self.app.config['CONSOLE_TOKEN']}

    def get(self, path='/api/state'):
        return self.client.get(path, base_url='http://127.0.0.1:8765')

    def post(self, path, data, **kwargs):
        return self.client.post(path, json={'market': 'fr', **data}, headers=self.headers,
                                base_url='http://127.0.0.1:8765', **kwargs)

    def approve(self):
        path = wf.artifact('fr', 'preflight')
        path.write_text('sku,status\nA,READY\nB,CL_SKIPPED\n', encoding='utf-8-sig')
        stamp = wf.fingerprint('fr')
        wf.write_json(wf.proof_path('fr'), {'preflight': stamp, 'dry-run': stamp})
        return stamp

    def test_page_renders_without_secrets(self):
        response = self.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'STEP 4', response.data)
        state = self.get()
        self.assertEqual(state.status_code, 200)
        self.assertTrue(state.json['auth'])
        self.assertNotIn(b'test-only', state.data)
        self.assertIn('frame-ancestors', state.headers['Content-Security-Policy'])

    def test_mutations_require_token_origin_and_local_host(self):
        response = self.client.post('/api/jobs', json={'market':'fr','action':'write'}, base_url='http://127.0.0.1:8765')
        self.assertEqual(response.status_code, 403)
        response = self.client.get('/api/state', base_url='http://evil.example:8765')
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/market', json={'market':'fr','target':'ca'},
            headers={**self.headers,'Origin':'https://evil.example'}, base_url='http://127.0.0.1:8765')
        self.assertEqual(response.status_code, 403)

    def test_market_changes_existing_runtime_and_locks_while_running(self):
        self.assertEqual(self.post('/api/market', {'target':'ca'}).status_code, 200)
        self.assertEqual(pc.load_runtime()['market'], 'ca')
        self.assertEqual(self.post('/api/jobs', {'action':'detect','skus':'A'}).status_code, 400)  # stale tab
        self.manager.active = {'status':'running'}
        response = self.post('/api/market', {'market':'ca','target':'fr'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(pc.load_runtime()['market'], 'ca')

    def test_server_rejects_unapproved_or_unconfirmed_write(self):
        self.assertEqual(self.post('/api/jobs', {'action':'write','confirmed':True}).status_code, 400)
        stamp = self.approve()
        self.assertEqual(self.post('/api/jobs', {'action':'write','fingerprint':stamp,'count':1}).status_code, 400)
        self.assertEqual(self.post('/api/jobs', {'action':'write','fingerprint':'stale','count':1,'confirmed':True}).status_code, 400)
        with patch.object(self.manager, '_cli', return_value=0) as command:
            response = self.post('/api/jobs', {'action':'write','fingerprint':stamp,'count':1,'confirmed':True})
            self.assertEqual(response.status_code, 202)
            self.manager.thread.join(3)
            command.assert_called_once()
        self.assertFalse(wf.status('fr')['dry_run'])  # consumed approval, duplicate click is blocked

    def test_refresh_restores_results_but_changed_inputs_expire_approval(self):
        self.approve()
        self.assertTrue(wf.status('fr')['dry_run'])
        another = web.create_app().test_client()
        self.assertTrue(another.get('/api/state',base_url='http://127.0.0.1:8765').json['dry_run'])
        image = self.fixture.paths['extract'] / 'A/images/0.jpg'
        image.write_bytes(b'changed')
        self.assertFalse(wf.status('fr')['preflight']['ready'])

    def test_old_csv_without_proof_never_unlocks_write(self):
        wf.artifact('fr','preflight').write_text('sku,status\nA,READY\n')
        result = wf.status('fr')
        self.assertTrue(result['preflight']['has_report'])
        self.assertFalse(result['preflight']['ready'])
        self.assertFalse(result['dry_run'])

    def test_detect_writes_input_automatically_and_async_job_completes(self):
        with patch.object(self.manager, '_cli', return_value=0):
            response = self.post('/api/jobs', {'action':'detect','skus':'A\nB\nA'})
            self.assertEqual(response.status_code, 202)
            self.manager.thread.join(3)
        self.assertEqual((self.fixture.paths['input']/'skus.txt').read_text(), 'A\nB\n')
        self.assertEqual(self.manager.latest('fr')['status'], 'success')
        self.assertTrue((self.fixture.paths['logs'] / 'console_latest.json').is_file())

    def test_apply_mapping_runs_existing_mapping_then_preflight(self):
        with patch.object(self.manager, '_cli', return_value=0) as command:
            response = self.post('/api/jobs', {'action':'mapping'})
            self.assertEqual(response.status_code,202)
            self.manager.thread.join(3)
        self.assertEqual([call.args[0] for call in command.call_args_list], ['mapping','preflight'])
        self.assertIn('preflight', wf.read_json(wf.proof_path('fr')))

    def test_settings_validate_before_writing(self):
        valid = {'delay':.8,'timeout':30,'image_timeout':20,'image_workers':4,'max_images':12,'min_images':5,'checkpoint_every':10}
        old = (self.root/'config/runtime.json').read_bytes()
        self.assertEqual(self.post('/api/settings',{**valid,'image_workers':99}).status_code,400)
        self.assertEqual((self.root/'config/runtime.json').read_bytes(),old)
        self.assertEqual(self.post('/api/settings',{**valid,'max_images':5,'min_images':6}).status_code,400)
        self.assertEqual(self.post('/api/settings',valid).status_code,200)
        self.assertEqual(pc.load_runtime()['image_workers'],4)

    def test_credentials_save_only_to_existing_locations_and_never_echo(self):
        self.assertEqual(self.post('/api/credentials/ark',{'cookie':'session=super-secret-test-value'}).status_code,200)
        self.assertEqual(pc.load_auth()['raw_cookie'],'session=super-secret-test-value')
        self.assertNotIn(b'super-secret',self.get().data)
        self.assertEqual(self.post('/api/credentials/ark',{'cookie':''}).status_code,400)
        self.assertEqual(pc.load_auth()['raw_cookie'],'session=super-secret-test-value')
        self.assertEqual(self.post('/api/credentials/cl',{'curl':'curl "https://evil.example/GetDetail" -b "secret"'}).status_code,400)
        self.assertEqual(self.post('/api/credentials/cl',{'curl':'curl "https://cl.aosom.cloud/GetDetail?keyValue=A" -b "session=secret"'}).status_code,200)

    def test_log_redaction_and_path_open_allowlist(self):
        wf.write_json(self.root/'sku_write/auth.json',{'raw_cookie':'session=super-secret-value-123456'})
        clean = wf.scrubber()
        self.assertNotIn('super-secret',clean('error super-secret-value-123456\nCookie: abc'))
        self.assertEqual(self.post('/api/open',{'kind':'../../sku_write/auth.json'}).status_code,400)
        self.assertEqual(self.get('/api/report/logs').status_code,400)
        with patch.object(os_module := __import__('os'),'startfile',create=True) as opener:
            self.assertEqual(self.post('/api/open',{'kind':'workspace'}).status_code,200)
            opener.assert_called_once_with(str(self.fixture.paths['input'].parent))

    def test_mapping_import_uses_expected_file(self):
        text = 'issue_type,source_value,suggested_target\ncategory,Furniture,Garden > Chairs\n'
        self.assertEqual(self.post('/api/mapping-upload',{'csv':text}).status_code,200)
        self.assertIn('Garden > Chairs',wf.artifact('fr','suggested').read_text(encoding='utf-8-sig'))
        self.assertEqual(self.post('/api/mapping-upload',{'csv':'bad\nvalue'}).status_code,400)

    def test_detect_retry_preserves_full_worklist_and_successes(self):
        results=[{'sku':'A','status':'EXISTS'},{'sku':'B','status':'NEED_CREATE'},{'sku':'C','status':'ERROR'}]
        detect.save_results('fr','1001',results)
        self.assertEqual(detect.retry_results('fr','1001'),results)
        with patch.object(sys,'argv',['detect.py','--retry-errors']), patch.object(detect,'ArkSwiftClient',return_value=Mock(store_id='1001')), \
             patch.object(detect,'check_sku',return_value={'sku':'C','status':'NEED_CREATE'}) as check, patch.object(detect.time,'sleep'):
            self.assertEqual(detect.main(),0)
        check.assert_called_once()
        self.assertEqual(check.call_args.args[1],'C')
        restored=detect.retry_results('fr','1001')
        self.assertEqual([x['sku'] for x in restored],['A','B','C'])
        self.assertEqual([x['status'] for x in restored],['EXISTS','NEED_CREATE','NEED_CREATE'])
        pc.validate_detect('fr')

    def test_python_detect_exact_match_pagination_and_error_classification(self):
        client=Mock(base_url='https://example.invalid',store_id='1001',lang='cn')
        client._decode.side_effect=[{'data':{'list':[{'sellerSku':'A-OTHER'}],'pages':2,'total':2}},
                                    {'data':{'list':[{'sellerSku':'a'}],'pages':2,'total':2}}]
        self.assertEqual(detect.check_sku(client,'A',sleep=lambda _:None)['status'],'EXISTS')
        self.assertEqual(client.session.get.call_count,2)
        client._decode.side_effect=None
        client._decode.return_value={'data':{}}
        self.assertEqual(detect.check_sku(client,'A',sleep=lambda _:None)['status'],'ERROR')
        client._decode.return_value={'data':{'list':[],'pages':0,'total':0}}
        self.assertEqual(detect.check_sku(client,'A',sleep=lambda _:None)['status'],'NEED_CREATE')

    def test_null_empty_list_is_missing_but_malformed_list_is_error(self):
        client=Mock(base_url='https://example.invalid',store_id='1001',lang='cn')
        client._decode.return_value={'data':{'list':None,'pages':0,'total':0}}
        self.assertEqual(detect.check_sku(client,'A',sleep=lambda _:None)['status'],'NEED_CREATE')
        client._decode.return_value={'data':{'list':None,'pages':1,'total':1}}
        self.assertEqual(detect.check_sku(client,'A',sleep=lambda _:None)['status'],'ERROR')

    def test_batch_audit_excludes_history_existing_and_dry_run(self):
        audit=wf.artifact('fr','audit_csv')
        audit.write_text('seller_sku,run_status,saved_at\nA,ERROR,2026-09-16T09:00:00+00:00\nB,DRAFT_SAVED,2026-09-17T10:00:00+00:00\n',encoding='utf-8')
        self.assertEqual(wf.status('fr')['audit_total'],0)
        wf.write_json(wf.batch_path('fr'),{'write_started_at':'2026-09-17T09:00:00+00:00'})
        self.assertEqual(wf.status('fr')['audit_total'],0)
        audit.write_text('seller_sku,run_status,saved_at\nA,DRAFT_SAVED,2026-09-17T18:00:00+08:00\nB,DRAFT_SAVED,2026-09-17T10:00:00+00:00\n',encoding='utf-8')
        self.assertEqual(wf.status('fr')['audit'],{'DRAFT_SAVED':1})
        with patch.object(self.manager,'_cli',return_value=0):
            self.post('/api/jobs',{'action':'detect','skus':'A\nB'})
            self.manager.thread.join(3)
        self.assertFalse(wf.status('fr')['write_started'])
        self.assertEqual(wf.status('fr')['audit_total'],0)

    def test_preflight_blockers_are_visible_without_mapping(self):
        wf.artifact('fr','preflight').write_text('sku,status,blockers\nA,BLOCKED,height invalid\n',encoding='utf-8')
        state=wf.status('fr')['preflight']
        self.assertEqual(state['mapping'],0)
        self.assertEqual(state['blockers'],[{'sku':'A','reason':'height invalid'}])

    def test_pending_separate_from_errors_and_blocks_extraction(self):
        results=[{'sku':'A','status':'EXISTS'},{'sku':'B','status':'PENDING'},{'sku':'C','status':'ERROR'}]
        detect.save_results('fr','1001',results)
        self.assertEqual(detect.retry_results('fr','1001'),results)
        state=wf.status('fr')['detect']
        self.assertEqual((state['existing'],state['pending'],state['errors']),(1,1,1))
        self.assertFalse(state['ready'])
        with self.assertRaises(ValueError):
            pc.validate_detect('fr')

    def test_failed_jobs_do_not_approve_and_restarted_server_shows_interruption(self):
        with patch.object(self.manager,'_cli',return_value=1):
            self.post('/api/jobs',{'action':'preflight'})
            self.manager.thread.join(3)
        self.assertEqual(self.manager.latest('fr')['status'],'failed')
        self.assertNotIn('preflight',wf.read_json(wf.proof_path('fr')))
        wf.write_json(self.fixture.paths['logs']/'console_latest.json',{'status':'running','market':'fr'})
        self.assertEqual(wf.JobManager().latest('fr')['status'],'interrupted')


if __name__=='__main__':
    unittest.main()
