"""Offline regression tests: synthetic fixtures, no production credentials or network."""
from __future__ import annotations
import contextlib
import csv
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sku_write')]
import project_config as pc
import main as writer
import preflight
import apply_mapping_review as mapping
from state_store import StateStore
from image_policy import select_images_for_upload, ImagePolicyError
from arkswift_client import ArkSwiftClient, ArkSwiftError

spec = importlib.util.spec_from_file_location('revflow_test_module', ROOT / 'revflow/revflow.py')
revflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revflow)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='arkswift_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('config', 'sku_write'):
            (self.root / name).mkdir()
        shutil.copy2(ROOT / 'sku_write/config.json', self.root / 'sku_write/config.json')
        save_json(self.root / 'config/runtime.json', {'market': 'fr', 'delay': 0})
        save_json(self.root / 'config/markets.json', {
            'fr': {'store_id': '1001', 'seller_id': '2001', 'country_code': 'FR'},
            'ca': {'store_id': '1002', 'seller_id': '2001', 'country_code': 'CA'},
            'de': {'store_id': '', 'seller_id': '2001', 'country_code': 'DE'}})
        save_json(self.root / 'sku_write/auth.json', {'raw_cookie': 'test-only'})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for module, key, value in ((pc, 'PROJECT_ROOT', self.root), (revflow, 'PROJECT_ROOT', self.root),
                                  (writer, 'PROJECT_DIR', self.root / 'sku_write'),
                                  (preflight, 'PROJECT_DIR', self.root / 'sku_write'),
                                  (mapping, 'PROJECT_DIR', self.root / 'sku_write')):
            self.stack.enter_context(patch.object(module, key, value))
        # Any accidental live request fails this test suite immediately.
        self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden in tests')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.paths = pc.workspace_paths('fr')
        for path in self.paths.values():
            path.mkdir(parents=True, exist_ok=True)
        self.manifest = self.detect(['A', 'B'], ['A'])
        self.rows = [self.row('A'), self.row('B', 'SKIP_EXISTS')]
        self.summary(self.rows)
        self.category = {'garden > chairs': {'id': 8, 'path': 'Garden > Chairs', 'name': 'Chairs'}}
        self.attrs = {'color': {'black': {'id': 11, 'name': 'Black'}},
                      'material': {'steel': {'id': 12, 'name': 'Steel'}}}
        for name, data in {'category_map': {'Furniture': 'Garden > Chairs'},
                           'attr_aliases': {}, 'sku_overrides': {}}.items():
            save_json(self.root / f'sku_write/mapping/{name}.json', data)

    def detect(self, all_skus, todo, errors=(), market='fr'):
        directory = pc.workspace_paths(market)['detect']
        directory.mkdir(parents=True, exist_ok=True)
        names = [f'{market}-{name}.txt' for name in ('all', 'need_create', 'errors')]
        for name, values in zip(names, (all_skus, todo, errors)):
            (directory / name).write_bytes('\r\n'.join(values).encode('utf-8'))
        manifest = {'market': market, 'store_id': pc.market_config(market)['store_id'], 'complete': True,
                    'errors': len(errors), 'files': {name: pc.sha256_file(directory / name) for name in names}}
        save_json(directory / f'{market}-detect.json', manifest)
        return manifest

    def row(self, sku, status='OK'):
        directory = self.paths['extract'] / sku / 'images'
        directory.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (directory / f'{i}.jpg').write_bytes(b'synthetic-image-test')
        return {'seller_sku': sku, 'requested_sku': sku, 'status': status, 'product_title': 'Chair',
                'description': 'Chair description', 'category_1': 'Furniture', 'color': 'Black', 'material': 'Steel',
                'feature_1': 'Feature', 'box_qty': '1', 'length_cm': '50', 'width_cm': '40', 'height_cm': '70',
                'box_length_cm': '55', 'box_width_cm': '45', 'box_height_cm': '75',
                'net_weight_kg': '5', 'gross_weight_kg': '6', 'images_dir': f'{sku}/images',
                'image_files': '|'.join(f'{i}.jpg' for i in range(5))}

    def summary(self, rows):
        with (self.paths['extract'] / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as file:
            w = csv.DictWriter(file, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        pc.write_extract_context('fr', self.manifest, complete=True)

    def run_writer(self, dry=False, force=False, client=None):
        client = client or Mock()
        client.save_draft.return_value = '999'
        with patch.object(writer, 'parse_args', return_value=SimpleNamespace(sku=None, dry_run=dry, force=force)), \
             patch.object(writer, 'setup_auto_log', return_value=self.paths['logs'] / 'test.log'), \
             patch.object(writer, 'ArkSwiftClient', return_value=client), \
             patch.object(writer, 'load_catalogs', return_value=(self.category, self.attrs)), \
             patch.object(writer, 'upload_images', return_value=[{'url': 'https://example.invalid/test.jpg'}]) as upload:
            code = writer.main()
        return code, client, upload

    def test_paths_use_project_not_cwd_and_markets_are_isolated(self):
        cfg = pc.load_write_config()
        self.assertEqual(Path(cfg['paths']['output_root']), self.paths['extract'])
        self.assertNotEqual(pc.workspace_paths('ca')['state'], self.paths['state'])
        self.assertEqual(cfg['arkswift']['store_id'], '1001')

    def test_unknown_market_id_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, '尚未确认'):
            pc.market_config('de')

    def test_detect_error_stops_before_network(self):
        self.detect(['A', 'B'], ['A'], ['B'])
        with patch.object(sys, 'argv', ['revflow.py']), patch.object(revflow, 'parse_curl_file') as curl:
            with self.assertRaisesRegex(ValueError, 'ERROR'):
                revflow.main()
            curl.assert_not_called()

    def test_detect_missing_files_never_falls_back(self):
        (self.paths['detect'] / 'fr-need_create.txt').unlink()
        (self.root / 'skus.txt').write_text('A')
        with self.assertRaisesRegex(FileNotFoundError, 'SKU Detect 未完成'):
            pc.validate_detect('fr')

    def test_detect_empty_todo_is_valid(self):
        self.detect(['A'], [])
        pc.validate_detect('fr')

    def test_mixed_save_and_wrong_store_block(self):
        (self.paths['detect'] / 'fr-need_create.txt').write_text('B')
        with self.assertRaisesRegex(ValueError, '完整保存'):
            pc.validate_detect('fr')
        manifest = self.detect(['A'], ['A'])
        manifest['store_id'] = 'wrong'
        save_json(self.paths['detect'] / 'fr-detect.json', manifest)
        with self.assertRaisesRegex(ValueError, '店铺'):
            pc.validate_detect('fr')

    def test_incomplete_extract_and_edited_summary_block(self):
        pc.write_extract_context('fr', self.manifest, complete=False)
        with self.assertRaisesRegex(ValueError, '未完成'):
            pc.validate_extract('fr')
        pc.write_extract_context('fr', self.manifest, complete=True)
        with (self.paths['extract'] / 'summary.csv').open('a') as file:
            file.write('edited')
        with self.assertRaisesRegex(ValueError, 'summary.csv'):
            pc.validate_extract('fr')

    def test_revflow_existing_only_never_calls_cl(self):
        self.detect(['A', 'B'], [])
        (self.root / 'revflow').mkdir()
        (self.root / 'revflow/getdetail.curl.txt').write_text('test placeholder')
        with patch.object(sys, 'argv', ['revflow.py']), \
             patch.object(revflow, 'parse_curl_file', return_value=('https://example.invalid/GetDetail', {})), \
             patch.object(revflow, 'fetch_getdetail_for_sku') as fetch:
            self.assertEqual(revflow.main(), 0)
            fetch.assert_not_called()
        pc.validate_extract('fr')
        with (self.paths['extract'] / 'summary.csv').open(encoding='utf-8-sig') as file:
            self.assertEqual([r['status'] for r in csv.DictReader(file)], ['SKIP_EXISTS', 'SKIP_EXISTS'])

    def test_revflow_resume_reuses_complete_product_images(self):
        self.detect(['A'], ['A'])
        (self.root / 'revflow').mkdir()
        (self.root / 'revflow/getdetail.curl.txt').write_text('test placeholder')
        product = {'source': {'requested_sku': 'A', 'country': 'FR'}, 'identity': {'seller_sku': 'A'},
                   'arkswift_fields': {'seller_sku': 'A', 'product_title': 'Chair'}, 'category_source': {}}
        save_json(self.paths['extract'] / 'A/product.json', product)
        save_json(self.paths['extract'] / 'A/images/manifest.json', {'complete': True, 'downloaded': [{'file': '0.jpg'}]})
        with patch.object(sys, 'argv', ['revflow.py']), \
             patch.object(revflow, 'parse_curl_file', return_value=('https://example.invalid/GetDetail', {})), \
             patch.object(revflow, 'resolve_line_guid_via_getpagelist') as fetch:
            self.assertEqual(revflow.main(), 0)
            fetch.assert_not_called()
        pc.validate_extract('fr')

    def test_preflight_existing_and_mapping_gate(self):
        with patch.object(preflight, 'ArkSwiftClient'), patch.object(preflight, 'load_catalogs', return_value=(self.category, self.attrs)):
            self.assertEqual(preflight.main(), 0)
            with (self.paths['review'] / 'preflight_fr.csv').open(encoding='utf-8-sig') as file:
                self.assertEqual([r['status'] for r in csv.DictReader(file)], ['READY_WITH_WARNINGS', 'CL_SKIPPED'])
            self.rows[0]['category_1'] = 'Unmapped'
            self.summary(self.rows)
            self.assertEqual(preflight.main(), 1)
            self.assertTrue((self.paths['review'] / 'mapping_review_fr.csv').exists())
            self.rows[0]['category_1'] = 'Furniture'
            self.summary(self.rows)
            self.assertEqual(preflight.main(), 0)
            self.assertFalse((self.paths['review'] / 'mapping_review_fr.csv').exists())

    def test_interrupt_marks_extract_incomplete_and_restart_recovers(self):
        self.detect(['A'], ['A'])
        (self.root / 'revflow').mkdir()
        (self.root / 'revflow/getdetail.curl.txt').write_text('test placeholder')
        with patch.object(sys, 'argv', ['revflow.py']), \
             patch.object(revflow, 'parse_curl_file', return_value=('https://example.invalid/GetDetail', {})), \
             patch.object(revflow, 'resolve_line_guid_via_getpagelist', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                revflow.main()
        with self.assertRaisesRegex(ValueError, '未完成'):
            pc.validate_extract('fr')
        self.detect(['A'], [])
        with patch.object(sys, 'argv', ['revflow.py']), \
             patch.object(revflow, 'parse_curl_file', return_value=('https://example.invalid/GetDetail', {})):
            self.assertEqual(revflow.main(), 0)
        pc.validate_extract('fr')

    def test_dry_run_preserves_real_state_and_audit_even_on_errors(self):
        state = self.paths['state'] / 'results.jsonl'
        audit = self.paths['audit'] / 'arkswift_draft_summary_fr.csv'
        state.write_text('{"sku":"A","status":"DRAFT_SAVED","sku_id":"777"}\n', encoding='utf-8')
        audit.write_bytes(b'production audit sentinel')
        before = (state.read_bytes(), audit.read_bytes())
        self.rows[0]['description'] = ''
        self.summary(self.rows)
        code, client, upload = self.run_writer(dry=True)
        self.assertEqual(code, 1)
        client.save_draft.assert_not_called()
        upload.assert_not_called()
        self.assertEqual((state.read_bytes(), audit.read_bytes()), before)

    def test_valid_dry_run_has_no_upload(self):
        code, client, upload = self.run_writer(dry=True)
        self.assertEqual(code, 0)
        upload.assert_not_called()
        client.save_draft.assert_not_called()
        self.assertFalse((self.paths['state'] / 'results.jsonl').exists())

    def test_write_resume_and_force_preserve_payload_behavior(self):
        code, client, upload = self.run_writer()
        self.assertEqual(code, 0)
        self.assertEqual(client.save_draft.call_count, 1)
        self.assertEqual(upload.call_count, 1)
        body = client.save_draft.call_args.args[0]
        self.assertEqual(body['sellerSku'], 'A')
        self.assertEqual(body['categoryId'], 8)
        code, client, upload = self.run_writer()
        client.save_draft.assert_not_called()
        upload.assert_not_called()
        code, client, upload = self.run_writer(force=True)
        self.assertEqual(client.save_draft.call_args.args[0]['skuId'], '999')
        with (self.paths['audit'] / 'arkswift_draft_summary_fr.csv').open(encoding='utf-8-sig') as file:
            self.assertEqual([r['run_status'] for r in csv.DictReader(file)], ['DRAFT_SAVED', 'SKIP_EXISTS'])

    def test_already_exists_remains_terminal(self):
        client = Mock()
        client.save_draft.side_effect = ArkSwiftError('150035 Seller SKU信息已存在')
        self.assertEqual(self.run_writer(client=client)[0], 0)
        self.assertEqual(StateStore(self.paths['state'] / 'results.jsonl').latest_for_sku('A')['status'], 'ALREADY_EXISTS')
        self.run_writer()[1].save_draft.assert_not_called()

    def test_legacy_unlabelled_state_is_not_copied(self):
        legacy = self.root / 'sku_write/state/results.jsonl'
        legacy.parent.mkdir()
        legacy.write_text('{"sku":"A","status":"DRAFT_SAVED"}')
        results, _ = writer.ensure_market_state_dir('ca')
        self.assertFalse(results.exists())

    def test_image_minimum_and_fallback_are_preserved(self):
        row = self.rows[0]
        (self.paths['extract'] / 'A/images/0.jpg').unlink()
        with self.assertRaises(ImagePolicyError):
            select_images_for_upload(row, self.paths['extract'])
        (self.paths['extract'] / 'A/images/fallback.jpg').write_bytes(b'fixture')
        self.assertEqual(select_images_for_upload(row, self.paths['extract']).used_count, 5)

    def test_mapping_rejects_invalid_target_then_backs_up_valid_change(self):
        review = self.paths['review'] / 'mapping_review_fr_suggested.csv'
        review.write_text('issue_type,source_value,suggested_target\ncategory,New,Not real\n', encoding='utf-8')
        target = self.root / 'sku_write/mapping/category_map.json'
        original = target.read_bytes()
        with patch.object(mapping, 'parse_args', return_value=SimpleNamespace(file=None)), \
             patch.object(mapping, 'ArkSwiftClient'), patch.object(mapping, 'load_catalogs', return_value=(self.category, self.attrs)):
            with self.assertRaises(RuntimeError):
                mapping.main()
            self.assertEqual(target.read_bytes(), original)
            review.write_text('issue_type,source_value,suggested_target\ncategory,New,Garden > Chairs\n', encoding='utf-8')
            mapping.main()
        self.assertEqual(json.loads(target.read_text())['New'], 'Garden > Chairs')
        self.assertEqual(len(list((self.paths['review'] / 'mapping_backups').glob('*.json'))), 2)

    def test_expired_auth_is_actionable(self):
        client = ArkSwiftClient({'base_url': 'https://www.arkswift.com', 'store_id': '1001'}, {'raw_cookie': 'test-only'})
        response = Mock(status_code=401)
        response.json.return_value = {'code': 401, 'msg': 'expired'}
        with self.assertRaisesRegex(ArkSwiftError, '重新登录'):
            client._decode(response, 'category-tree')


if __name__ == '__main__':
    unittest.main()
