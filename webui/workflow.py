"""Small disk-backed adapter around the existing CLI workflow. No business rewrites."""
from __future__ import annotations
from collections import Counter, deque
from datetime import datetime, timezone
import hashlib
import json
import csv
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sku_write')]
import project_config as pc
from sku_detect.detect import retry_results
import browser_auth


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path):
    try:
        return pc.read_json(path)
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def csv_rows(path):
    if not path.is_file():
        return []
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def artifact(market, kind):
    paths = pc.workspace_paths(market)
    choices = {**paths, 'workspace': paths['input'].parent, 'payloads': paths['state'] / 'payloads',
        'need': paths['detect'] / f'{market}-need_create.txt', 'errors': paths['detect'] / f'{market}-errors.txt',
        'summary': paths['extract'] / 'summary.csv', 'preflight': paths['review'] / f'preflight_{market}.csv',
        'mapping': paths['review'] / f'mapping_review_{market}.csv',
        'suggested': paths['review'] / f'mapping_review_{market}_suggested.csv',
        'audit_csv': paths['audit'] / f'arkswift_draft_summary_{market}.csv'}
    if kind not in choices:
        raise ValueError('未知结果类型。')
    return choices[kind]


def proof_path(market):
    return pc.workspace_paths(market)['write'] / 'console_checks.json'


def fingerprint(market):
    """Approval applies only to these inputs/settings/mappings/images, not to stale files."""
    paths = pc.workspace_paths(market)
    files = [ROOT / 'config/runtime.json', ROOT / 'config/markets.json', ROOT / 'sku_write/config.json',
             ROOT / 'sku_write/auth.json', ROOT / 'revflow/getdetail.curl.txt',
             paths['detect'] / f'{market}-detect.json', paths['extract'] / 'run_context.json',
             paths['extract'] / 'summary.csv', *sorted((ROOT / 'sku_write/mapping').glob('*.json'))]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes() if path.is_file() else b'<missing>')
    # Image policy depends on the local inventory; a changed/missing image invalidates approval.
    for path in sorted(paths['extract'].glob('*/images/*')):
        if path.is_file():
            stat = path.stat()
            digest.update(f'{path.relative_to(ROOT)}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return digest.hexdigest()


def scrubber():
    """Snapshot current secret strings; never send them back to the browser or logs."""
    tokens = []
    auth = read_json(ROOT / 'sku_write/auth.json')
    values = list(auth.values())
    values.extend(read_json(ROOT / 'workspace/_auth/cl.json').values())
    curl = ROOT / 'revflow/getdetail.curl.txt'
    if curl.is_file():
        values.append(curl.read_text(encoding='utf-8-sig', errors='replace'))
    for value in values:
        if isinstance(value, str):
            if value:
                tokens.append(value)
            tokens.extend(re.findall(r'[A-Za-z0-9_./+=%-]{24,}', value))
            for item in value.split(';'):
                if '=' in item:
                    token = item.split('=', 1)[1].strip()
                    if len(token) >= 8:
                        tokens.append(token)
    def clean(text):
        for token in sorted(set(tokens), key=len, reverse=True):
            text = text.replace(token, '[已隐藏]')
        text = re.sub(r'(?im)^.*(?:cookie|authorization|curl\s+https?).*$', '[认证细节已隐藏]', text)
        return text
    return clean


def batch_path(market):
    return pc.workspace_paths(market)['write'] / 'console_batch.json'


def current_audit(market):
    batch = read_json(batch_path(market))
    started = batch.get('write_started_at')
    if not started:
        return []
    try:
        cutoff = datetime.fromisoformat(started)
        detected = retry_results(market, pc.market_config(market)['store_id'])
    except (ValueError, OSError, KeyError):
        return []
    targets = {r['sku'] for r in detected if r['status'] == 'NEED_CREATE'}
    rows = []
    for row in csv_rows(artifact(market, 'audit_csv')):
        try:
            saved = datetime.fromisoformat(row.get('saved_at', ''))
            if row.get('seller_sku') in targets and saved >= cutoff:
                rows.append(row)
        except (ValueError, TypeError):
            continue
    return rows


def _preflight_view(market):
    """Translate CLI preflight output into batch/UI semantics without changing CLI business logic."""
    reports = csv_rows(artifact(market, 'preflight'))
    raw_mapping = csv_rows(artifact(market, 'mapping'))

    # A blank source value cannot be mapped. It is a source-data problem for that SKU,
    # not an actionable Mapping task. Keep the original CSV untouched; only filter it
    # from the UI gate/count.
    actionable_mapping = [
        row for row in raw_mapping
        if (row.get('issue_type') or '').strip().lower() in {'category', 'color', 'material'}
        and (row.get('source_value') or '').strip()
    ]

    counts = Counter(r.get('status') for r in reports)
    runnable = counts['READY'] + counts['READY_WITH_WARNINGS']
    excluded = counts['BLOCKED'] + counts['CL_ERROR']

    blockers = []
    for row in reports:
        if row.get('status') not in {'BLOCKED', 'CL_ERROR'}:
            continue

        reason = (row.get('blockers') or '').strip()
        parts = [part.strip() for part in reason.split('|') if part.strip()]
        missing = []
        kept = []
        source_color = (row.get('source_color') or '').strip()
        source_material = (row.get('source_material') or '').strip()

        for part in parts:
            key, sep, value = part.partition(':')
            normalized_key = key.strip().lower()
            if sep and normalized_key == 'color' and not source_color and not value.strip():
                missing.append('color')
                continue
            if sep and normalized_key == 'material' and not source_material and not value.strip():
                missing.append('material')
                continue
            kept.append(part)

        if missing:
            kept.insert(0, 'source data missing: ' + ', '.join(missing))

        blockers.append({
            'sku': row.get('sku'),
            'reason': ' | '.join(kept) or reason,
        })

    return {
        'reports': reports,
        'counts': counts,
        'runnable': runnable,
        'excluded': excluded,
        'actionable_mapping': actionable_mapping,
        'blank_source_mapping': len(raw_mapping) - len(actionable_mapping),
        'blockers': blockers,
    }


def _preflight_can_continue(market):
    view = _preflight_view(market)
    if not view['reports'] or not view['runnable'] or view['actionable_mapping']:
        return False
    try:
        pc.validate_extract(market)
    except (OSError, ValueError, KeyError):
        return False
    return True


def _excluded_preflight_skus(market):
    return {
        (row.get('sku') or '').strip()
        for row in _preflight_view(market)['reports']
        if row.get('status') not in {'READY', 'READY_WITH_WARNINGS'}
        and (row.get('sku') or '').strip()
    }


def _audit_failures_since(path, started_at):
    try:
        cutoff = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return set()

    failed = set()
    for row in csv_rows(path):
        if (row.get('run_status') or '').strip().upper() not in {'ERROR', 'CL_ERROR'}:
            continue
        try:
            saved = datetime.fromisoformat(row.get('saved_at', ''))
        except (TypeError, ValueError):
            continue
        if saved >= cutoff:
            sku = (row.get('seller_sku') or '').strip()
            if sku:
                failed.add(sku)
    return failed


def _only_expected_failures(market, action, started_at):
    excluded = _excluded_preflight_skus(market)
    if not excluded:
        return False

    paths = pc.workspace_paths(market)
    if action == 'dry-run':
        audit_path = paths['review'] / f'dry_run_{market}.csv'
    elif action == 'write':
        audit_path = paths['audit'] / f'arkswift_draft_summary_{market}.csv'
    else:
        return False

    failures = _audit_failures_since(audit_path, started_at)
    return bool(failures) and failures <= excluded


def status(market):
    paths = pc.workspace_paths(market)
    detected, detect_error = [], ''
    try:
        detected = retry_results(market, pc.market_config(market)['store_id'])
    except (OSError, ValueError, KeyError):
        if (paths['detect'] / f'{market}-detect.json').exists():
            detect_error = '检测文件不完整或不匹配，请重新检测。'
    dc = Counter(x['status'] for x in detected)
    detect_ready = bool(detected) and dc['ERROR'] == 0 and dc['PENDING'] == 0
    extract_ready = False
    try:
        pc.validate_extract(market)
        extract_ready = True
    except (OSError, ValueError, KeyError):
        pass
    summary = csv_rows(artifact(market, 'summary'))
    preflight_view = _preflight_view(market)
    reports = preflight_view['reports']
    mapping = preflight_view['actionable_mapping']
    counts = preflight_view['counts']
    proof = read_json(proof_path(market))
    current = fingerprint(market)

    # Batch semantics: unresolved real mappings block the handoff; isolated bad SKUs do not.
    # As long as at least one SKU is runnable, BLOCKED / CL_ERROR rows are excluded from
    # creation and remain visible for Audit instead of blocking the whole market batch.
    preflight_ok = (
        bool(reports)
        and extract_ready
        and proof.get('preflight') == current
        and not mapping
        and preflight_view['runnable'] > 0
    )

    # Keep the legacy app.py write gate compatible without making Dry Run mandatory.
    # dry_run_actual is exposed separately for honest UI wording.
    dry_run_actual = preflight_ok and proof.get('dry-run') == current
    dry_ok = preflight_ok
    audit = current_audit(market)
    ac = Counter(r.get('run_status') for r in audit)
    # Count the same pending selection main.py uses, for the explicit write confirmation.
    from summary_reader import pick_rows
    cfg = pc.load_write_config()
    selected = pick_rows(summary, cfg['run'].get('target_sku'), cfg['run'].get('max_items')) if summary else []
    latest = {}
    state_path = paths['state'] / 'results.jsonl'
    if state_path.is_file():
        with state_path.open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                    latest[item['sku']] = item
                except (ValueError, KeyError):
                    continue
    runnable_skus = {
        (r.get('sku') or '').strip()
        for r in reports
        if r.get('status') in {'READY', 'READY_WITH_WARNINGS'}
    }
    pending = [r for r in selected if r.get('status') in {'OK', 'SKIPPED'} and not (
        cfg['run'].get('skip_if_success', True) and
        latest.get(r.get('seller_sku') or r.get('requested_sku'), {}).get('status') in {'DRAFT_SAVED', 'ALREADY_EXISTS'})]
    pending_runnable = [
        r for r in pending
        if (r.get('seller_sku') or r.get('requested_sku') or '').strip() in runnable_skus
    ]
    return {'market': market, 'store_id': cfg['arkswift']['store_id'],
        'detect': {'total': len(detected), 'existing': dc['EXISTS'], 'need': dc['NEED_CREATE'], 'errors': dc['ERROR'], 'pending': dc['PENDING'],
                   'ready': detect_ready, 'message': detect_error},
        'extract': {'ready': extract_ready, 'counts': dict(Counter(r.get('status') for r in summary)), 'total': len(summary)},
        'preflight': {
            'ready': preflight_ok,
            'counts': dict(counts),
            'mapping': len(mapping),
            'blank_source_mapping': preflight_view['blank_source_mapping'],
            'runnable': preflight_view['runnable'],
            'excluded': preflight_view['excluded'],
            'has_report': bool(reports),
            'blockers': preflight_view['blockers'],
            'suggested': artifact(market, 'suggested').is_file(),
        },
        'dry_run': dry_ok, 'dry_run_actual': dry_run_actual,
        'write_count': len(pending_runnable), 'audit': dict(ac), 'audit_total': len(audit),
        'audit_scope': '本批次', 'write_started': bool(read_json(batch_path(market)).get('write_started_at')), 'fingerprint': current,
        'limited': bool(cfg['run'].get('target_sku') or cfg['run'].get('max_items') is not None)}


ENTRY = {'detect': 'sku_detect/detect.py', 'retry': 'sku_detect/detect.py',
         'extract': 'revflow/revflow.py', 'preflight': 'sku_write/preflight.py',
         'mapping': 'sku_write/apply_mapping_review.py', 'dry-run': 'sku_write/main.py', 'write': 'sku_write/main.py'}


class JobManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.thread = None

    def idle(self):
        if self.active and self.active['status'] == 'running':
            raise ValueError('当前任务正在运行，请完成后再切换市场、修改设置或开始其他任务。')

    def latest(self, market):
        if self.active and self.active['market'] == market:
            return dict(self.active)
        job = read_json(pc.workspace_paths(market)['logs'] / 'console_latest.json')
        if job.get('status') == 'running':
            job.update(status='interrupted', message='上次服务已中断，请核对日志后重新运行。')
        return job

    def start(self, market, action):
        self.idle()
        if action not in ENTRY and action not in {'test-ark', 'test-cl'}:
            raise ValueError('未知操作。')
        stamp = uuid.uuid4().hex
        directory = pc.workspace_paths(market)['logs']
        directory.mkdir(parents=True, exist_ok=True)
        job = {'id': stamp, 'market': market, 'action': action, 'status': 'running', 'return_code': None,
               'started_at': now(), 'finished_at': None, 'log_file': f'console_{stamp}.log', 'message': '运行中…'}
        # Re-running any upstream stage invalidates downstream approval before launching.
        if action in ENTRY:
            proof = read_json(proof_path(market))
            proof.pop('dry-run', None)
            if action not in {'dry-run', 'write'}:
                proof.pop('preflight', None)
            write_json(proof_path(market), proof)
        if action == 'detect':
            write_json(batch_path(market), {'id': stamp, 'started_at': job['started_at']})
        elif action == 'write':
            batch = read_json(batch_path(market))
            batch.setdefault('write_started_at', job['started_at'])
            write_json(batch_path(market), batch)
        self.active = job
        write_json(directory / 'console_latest.json', job)
        self.thread = threading.Thread(target=self._work, args=(job,), daemon=True)
        self.thread.start()
        return dict(job)

    def _cli(self, action, log, clean):
        command = [sys.executable, '-u', str(ROOT / ENTRY[action])]
        if action == 'retry':
            command.append('--retry-errors')
        if action == 'dry-run':
            command.append('--dry-run')
        with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}) as process:
            self.process = process
            for line in process.stdout:
                log.write(clean(line))
                log.flush()
            return process.wait()

    def _test_connection(self, action):
        if action == 'test-ark':
            from arkswift_client import ArkSwiftClient
            client = ArkSwiftClient(pc.load_write_config()['arkswift'], pc.load_auth())
            response = client.session.get(client.base_url + '/rest/v1/seller/store/status',
                params={'_storeId': client.store_id, '_lang': client.lang}, timeout=20, allow_redirects=False)
            client._decode(response, '测试连接')
        else:
            if not browser_auth.validate_cl():
                raise ValueError('CL login session is invalid or expired.')

    def _work(self, job):
        market, action = job['market'], job['action']
        directory = pc.workspace_paths(market)['logs']
        clean = scrubber()
        before = None
        code = 1
        message = '任务失败，请查看结果与最近日志。'
        try:
            before = fingerprint(market)
            with (directory / job['log_file']).open('w', encoding='utf-8', buffering=1) as log:
                try:
                    if action.startswith('test-'):
                        self._test_connection(action)
                        code = 0
                        message = 'ArkSwift 登录有效，当前市场连接成功。' if action == 'test-ark' else 'CL 数据源有效；请确认来源国家与当前市场一致。'
                        log.write(message + '\n')
                    else:
                        code = self._cli(action, log, clean)
                        if action == 'mapping' and code == 0:
                            log.write('[CONSOLE] Mapping 已应用，自动重新运行 Preflight。\n')
                            before = fingerprint(market)
                            code = self._cli('preflight', log, clean)

                        # The CLI intentionally returns non-zero when any SKU is BLOCKED.
                        # For the batch UI, that is not a whole-batch failure: if real
                        # mapping issues are cleared and at least one SKU is runnable,
                        # continue with the runnable subset and keep exclusions for Audit.
                        if action in {'preflight', 'mapping'} and code != 0 and _preflight_can_continue(market):
                            view = _preflight_view(market)
                            code = 0
                            log.write(
                                f"[CONSOLE] {view['runnable']} runnable SKU(s); "
                                f"{view['excluded']} excluded SKU(s) will not block the batch.\n"
                            )
                            if view['blank_source_mapping']:
                                log.write(
                                    f"[CONSOLE] {view['blank_source_mapping']} blank-source mapping row(s) "
                                    "treated as source-data blockers, not actionable Mapping.\n"
                                )
                            message = (
                                f"预检完成：{view['runnable']} 个 SKU 可继续；"
                                f"{view['excluded']} 个异常 SKU 将跳过并记录到 Audit。"
                            )

                        if action == 'dry-run' and code != 0 and _preflight_can_continue(market) and _only_expected_failures(
                            market, 'dry-run', job.get('started_at')
                        ):
                            code = 0
                            message = 'Dry Run 完成；仅预检已排除的 SKU 失败，其余 SKU 可继续创建草稿。'
                            log.write('[CONSOLE] Dry Run failures are limited to preflight-excluded SKUs.\n')

                        if action == 'write' and code != 0 and _only_expected_failures(
                            market, 'write', job.get('started_at')
                        ):
                            code = 0
                            message = '批次完成：可运行 SKU 已处理；被排除的异常 SKU 已记录到 Audit。'
                            log.write('[CONSOLE] Write failures are limited to preflight-excluded SKUs.\n')

                        if code == 0 and not message.startswith(('预检完成', 'Dry Run 完成', '批次完成')):
                            message = '任务完成。' if action != 'mapping' else 'Mapping 已应用，重新预检完成。'
                        elif code != 0 and action in {'detect', 'retry'}:
                            message = '检测未全部通过，请查看 Errors 并重试；不能继续抓取。'
                        elif code != 0 and action in {'preflight', 'mapping'}:
                            message = '仍有可处理 Mapping 问题，或当前没有可运行 SKU；请查看报告。'
                except Exception:
                    log.write(clean(traceback.format_exc()))
                    if action.startswith('test-'):
                        message = '连接失败，请检查登录是否过期、当前店铺权限或网络，再更新凭证。'
        except Exception:
            message = '无法执行任务或写入日志，请检查工作目录权限。'
        finally:
            with self.lock:
                try:
                    current = fingerprint(market)
                    if code == 0 and action in {'preflight', 'mapping', 'dry-run'} and current == before:
                        proof = read_json(proof_path(market))
                        proof['preflight' if action == 'mapping' else action] = current
                        write_json(proof_path(market), proof)
                    elif code == 0 and action in {'preflight', 'mapping', 'dry-run'}:
                        code, message = 1, '运行期间输入或配置发生变化，请重新预检。'
                except Exception:
                    code, message = 1, '任务结束但状态保存失败，请检查日志和配置后重新预检。'
                try:
                    job.update(status='success' if code == 0 else 'failed', return_code=code,
                               finished_at=now(), message=message)
                    write_json(directory / 'console_latest.json', job)
                    write_json(directory / f"console_{job['id']}.json", job)
                finally:
                    self.process = None

    def log_tail(self, market):
        job = self.latest(market)
        name = job.get('log_file', '')
        if not re.fullmatch(r'console_[a-f0-9]{32}\.log', name):
            return ''
        path = pc.workspace_paths(market)['logs'] / name
        if not path.is_file():
            return ''
        with path.open(encoding='utf-8', errors='replace') as log:
            return scrubber()(''.join(deque(log, maxlen=150)))[-30000:]

    def shutdown(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        if self.thread:
            self.thread.join(timeout=3)
