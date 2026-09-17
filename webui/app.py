"""Local-only Flask controller. Existing CLIs remain the workflow implementation."""
from __future__ import annotations
import csv
import io
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import shutil
import sys
import tempfile
import threading
import traceback
from urllib.parse import urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Flask, jsonify, render_template, request, abort
from werkzeug.exceptions import HTTPException
import project_config as pc
from sku_detect.detect import parse_skus
from webui import workflow as wf

SETTING_RULES = {'delay': (.1, 30, float), 'timeout': (5, 300, int), 'image_timeout': (5, 300, int),
                 'image_workers': (1, 8, int), 'max_images': (5, 12, int),
                 'checkpoint_every': (1, 1000, int), 'min_images': (5, 12, int)}


def create_app(manager=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024, JSON_AS_ASCII=False, CONSOLE_PORT=8765)
    manager = manager or wf.JobManager()
    app.extensions['jobs'] = manager
    app.config['CONSOLE_TOKEN'] = secrets.token_urlsafe(32)

    @app.before_request
    def local_only():
        port = app.config['CONSOLE_PORT']
        if request.host not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
            abort(403)
        if request.method != 'GET' and request.method != 'HEAD':
            if request.headers.get('Origin') not in {None, 'http://' + request.host}:
                abort(403)
            if not secrets.compare_digest(request.headers.get('X-Console-Token', ''), app.config['CONSOLE_TOKEN']):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(Exception)
    def failure(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error='请求被拒绝或文件超过 2 MB，请刷新页面后重试。'), exc.code
        if isinstance(exc, (ValueError, FileNotFoundError)):
            return jsonify(error=str(exc)), 400
        try:
            directory = pc.workspace_paths(pc.load_runtime()['market'])['logs']
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / 'console_errors.log').open('a', encoding='utf-8') as handle:
                handle.write(wf.scrubber()(traceback.format_exc()) + '\n')
        except Exception:
            pass
        return jsonify(error='操作失败，请查看工作目录中的 console_errors.log。'), 500

    def current(data=None):
        market = pc.load_runtime()['market']
        if data is not None and data.get('market') != market:
            raise ValueError('当前市场已变化，请刷新页面后重试。')
        return market

    @app.get('/')
    def index():
        return render_template('index.html', token=app.config['CONSOLE_TOKEN'])

    @app.get('/api/state')
    def state():
        with manager.lock:
            market = current()
            result = wf.status(market)
            result['markets'] = list(pc.read_json(ROOT / 'config/markets.json'))
            result['auth'] = any(wf.read_json(ROOT / 'sku_write/auth.json').get(k) for k in ('raw_cookie', 'authorization_web'))
            curl = ROOT / 'revflow/getdetail.curl.txt'
            result['cl'] = curl.is_file() and bool(curl.read_text(encoding='utf-8-sig', errors='replace').strip())
            result['job'] = manager.latest(market)
            result['busy'] = bool(manager.active and manager.active['status'] == 'running')
            result['log'] = manager.log_tail(market)
            result['version'] = (ROOT / 'VERSION.txt').read_text(encoding='utf-8').strip()
            return jsonify(result)

    @app.post('/api/market')
    def market():
        data = request.get_json()
        with manager.lock:
            manager.idle()
            current(data)
            selected = data.get('target', '')
            pc.market_config(selected)
            runtime = pc.load_runtime()
            runtime['market'] = selected
            wf.write_json(ROOT / 'config/runtime.json', runtime)
            return jsonify(ok=True)

    @app.get('/api/settings')
    def settings():
        runtime = pc.load_runtime()
        runtime['min_images'] = pc.load_write_config()['upload']['min_images']
        return jsonify({key: runtime[key] for key in SETTING_RULES})

    @app.post('/api/settings')
    def save_settings():
        data = request.get_json()
        with manager.lock:
            manager.idle()
            current(data)
            values = {}
            for key, (low, high, typ) in SETTING_RULES.items():
                value = data.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or (typ is int and value != int(value)):
                    raise ValueError(f'{key} 请输入 {low}～{high} 范围内的' + ('整数。' if typ is int else '数值。'))
                values[key] = typ(value)
            if values['max_images'] < values['min_images']:
                raise ValueError('最多图片数不能小于最低图片数。')
            runtime = pc.load_runtime()
            runtime.update({k: v for k, v in values.items() if k != 'min_images'})
            write_cfg = pc.read_json(ROOT / 'sku_write/config.json')
            write_cfg['upload']['min_images'] = values['min_images']
            wf.write_json(ROOT / 'config/runtime.json', runtime)
            wf.write_json(ROOT / 'sku_write/config.json', write_cfg)
            return jsonify(ok=True)

    @app.post('/api/credentials/<kind>')
    def credentials(kind):
        data = request.get_json()
        with manager.lock:
            manager.idle()
            current(data)
            if kind == 'ark':
                cookie, authorization = data.get('cookie', ''), data.get('authorization', '')
                if not isinstance(cookie, str) or not isinstance(authorization, str):
                    raise ValueError('请粘贴有效的凭证文本。')
                if not cookie.strip() and not authorization.strip():
                    raise ValueError('请填 Cookie 或 Authorization_web；空白不会覆盖现有凭证。')
                if '\n' in cookie or '\r' in cookie or '\n' in authorization or '\r' in authorization:
                    raise ValueError('Cookie / Authorization 应为单行内容。')
                wf.write_json(ROOT / 'sku_write/auth.json', {'raw_cookie': cookie.strip(), 'authorization_web': authorization.strip()})
            elif kind == 'cl':
                text = data.get('curl', '')
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('请粘贴 GetDetail cURL；空白不会覆盖现有凭证。')
                from revflow.revflow import parse_curl_file
                with tempfile.TemporaryDirectory(prefix='arkswift_curl_') as temporary:
                    path = Path(temporary) / 'request.txt'
                    path.write_text(text, encoding='utf-8')
                    try:
                        url, _ = parse_curl_file(path)
                    except Exception:
                        raise ValueError('无法解析 cURL，请复制完整 GetDetail 请求。') from None
                parts = urlsplit(url)
                if parts.scheme != 'https' or parts.hostname != 'cl.aosom.cloud' or 'getdetail' not in parts.path.lower():
                    raise ValueError('仅接受 https://cl.aosom.cloud 的 GetDetail 请求。')
                (ROOT / 'revflow/getdetail.curl.txt').write_text(text.strip() + '\n', encoding='utf-8')
            else:
                raise ValueError('未知凭证类型。')
            return jsonify(ok=True)

    @app.post('/api/jobs')
    def start_job():
        data = request.get_json()
        with manager.lock:
            manager.idle()
            market = current(data)
            action = data.get('action')
            snapshot = wf.status(market)
            if action in {'detect', 'retry', 'preflight', 'mapping', 'dry-run', 'write', 'test-ark'}:
                pc.load_auth()
            if action == 'detect':
                skus = parse_skus(data.get('skus', ''))
                path = pc.workspace_paths(market)['input'] / 'skus.txt'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('\n'.join(skus) + '\n', encoding='utf-8')
            elif action == 'retry' and not (snapshot['detect']['errors'] or snapshot['detect'].get('pending')):
                raise ValueError('没有可重试的 ERROR；不完整结果请重新检测完整清单。')
            elif action == 'extract' and not snapshot['detect']['ready']:
                raise ValueError('请先完成检测，并解决所有 ERROR。')
            elif action in {'preflight', 'mapping'} and not snapshot['extract']['ready']:
                raise ValueError('请先完成当前批次的抓取。')
            elif action == 'dry-run' and not snapshot['preflight']['ready']:
                raise ValueError('预检未通过或已过期，请重新运行预检。')
            elif action == 'write':
                if not snapshot['dry_run'] or not snapshot['write_count']:
                    raise ValueError('请先通过预检和 Dry Run，并确认有待创建 SKU。')
                if data.get('confirmed') is not True or data.get('fingerprint') != snapshot['fingerprint'] or data.get('count') != snapshot['write_count']:
                    raise ValueError('草稿确认已过期，请重新核对市场与 SKU 数量后确认。')
            return jsonify(manager.start(market, action)), 202

    @app.post('/api/open')
    def open_file():
        data = request.get_json()
        with manager.lock:
            market = current(data)
            kind = data.get('kind')
            path = wf.artifact(market, kind)
            if kind == 'suggested' and not path.exists():
                manager.idle()
                original = wf.artifact(market, 'mapping')
                if original.exists():
                    shutil.copy2(original, path)
            if not path.exists():
                raise ValueError('该结果尚未生成。')
            os.startfile(str(path))
            return jsonify(ok=True)

    @app.post('/api/mapping-upload')
    def mapping_upload():
        data = request.get_json()
        with manager.lock:
            manager.idle()
            market = current(data)
            if not wf.status(market)['extract']['ready']:
                raise ValueError('请先完成当前批次的抓取。')
            text = data.get('csv', '')
            reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
            if not {'issue_type', 'source_value', 'suggested_target'} <= set(reader.fieldnames or []):
                raise ValueError('请上传填写建议后的原格式 Mapping CSV。')
            if not list(reader):
                raise ValueError('Mapping CSV 为空。')
            wf.artifact(market, 'suggested').write_text(text, encoding='utf-8-sig')
            return jsonify(ok=True)

    @app.get('/api/report/<kind>')
    def report(kind):
        market = current()
        if kind == 'failed':
            rows = [r for r in wf.current_audit(market) if r.get('run_status') in {'ERROR', 'CL_ERROR'}]
            return jsonify(text=wf.scrubber()('\n'.join(f"{r.get('seller_sku')}: {r.get('error', '')}" for r in rows[:200])) or '没有失败记录。')
        path = wf.artifact(market, kind)
        if kind not in {'need', 'errors', 'summary', 'preflight', 'mapping', 'audit_csv'} or not path.is_file():
            raise ValueError('该结果尚未生成。')
        with path.open(encoding='utf-8-sig', errors='replace') as handle:
            text = handle.read(50000)
        return jsonify(text=wf.scrubber()(text), note='最多预览 50,000 字；完整内容请打开结果文件。')

    return app


def main():
    from werkzeug.serving import make_server
    import msvcrt
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    # One console per project, including when start.bat is double-clicked twice.
    workspace = ROOT / 'workspace'
    workspace.mkdir(exist_ok=True)
    instance = (workspace / '.console.lock').open('a+b')
    if instance.tell() == 0:
        instance.write(b'1')
        instance.flush()
    instance.seek(0)
    try:
        msvcrt.locking(instance.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        instance.close()
        port = wf.read_json(workspace / '.console_server.json').get('port')
        if isinstance(port, int) and 8765 <= port <= 8775:
            webbrowser.open(f'http://127.0.0.1:{port}')
            print('控制台已运行，已打开现有窗口。')
            return
        print('控制台正在启动，请稍后重试。')
        return
    app = create_app()
    # Bind successfully before opening a browser. No debug mode, reloader, or public interface.
    server = None
    for port in range(8765, 8776):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                continue
        try:
            server = make_server('127.0.0.1', port, app, threaded=True)
        except (OSError, SystemExit):
            continue
        break
    if server is None:
        instance.close()
        print('无法启动：本机 8765～8775 端口均不可用。')
        raise SystemExit(1)
    app.config['CONSOLE_PORT'] = port
    wf.write_json(workspace / '.console_server.json', {'port': port, 'pid': os.getpid()})
    url = f'http://127.0.0.1:{port}'
    print(f'ArkSwift AutoListing: {url}\n请保留此窗口；关闭浏览器不会停止后台任务。')
    if port != 8765:
        print(f'默认端口被占用，已自动使用 {port}。')
    threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.extensions['jobs'].shutdown()
        server.server_close()
        instance.close()


if __name__ == '__main__':
    main()
