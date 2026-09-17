"""CLI/Python equivalent of sku_detect.js, sharing the existing client and credentials."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sku_write')]
from arkswift_client import ArkSwiftClient
from project_config import load_write_config, load_auth, workspace_paths, read_json, sha256_file


def parse_skus(text: str) -> list[str]:
    values = [x.strip().lstrip('\ufeff') for x in re.split(r'[\r\n,;\t]+', text)]
    values = list(dict.fromkeys(x for x in values if x and not x.startswith('#')))
    if not values:
        raise ValueError('请上传 TXT/CSV 或粘贴 SKU，每行一个、无表头。')
    if len(values) > 20000 or any(len(x) > 150 or not re.fullmatch(r'[\w.-]+', x) or x in {'.', '..'} for x in values):
        raise ValueError('SKU 格式不正确：仅支持字母、数字、下划线、点和短横线；单批最多 20,000 条。')
    return values


def exact_sku(value, sku: str) -> bool:
    if isinstance(value, list):
        return any(exact_sku(item, sku) for item in value)
    if not isinstance(value, dict):
        return False
    for key, val in value.items():
        if key.lower() in {'sellersku', 'seller_sku', 'seller-sku', 'sku'} and isinstance(val, str) and val.strip().upper() == sku.upper():
            return True
    return any(exact_sku(val, sku) for val in value.values() if isinstance(val, (dict, list)))


def check_sku(client: ArkSwiftClient, sku: str, sleep=time.sleep) -> dict:
    for attempt in range(1, 4):
        try:
            page = 1
            while True:
                response = client.session.get(client.base_url + '/rest/v1/seller/goods/list', params={
                    '_storeId': client.store_id, '_lang': client.lang, 'pageNum': page, 'pageSize': 20,
                    'sellerSku': sku, 'status': '1', 'freezeStatus': '1'}, timeout=30, allow_redirects=False)
                data = client._decode(response, 'SKU Detect').get('data')
                if isinstance(data, dict) and data.get('list') is None and str(data.get('total')) == '0' and str(data.get('pages')) == '0':
                    data = {**data, 'list': []}
                if not isinstance(data, dict) or not isinstance(data.get('list'), list):
                    raise ValueError('商品列表结构异常')
                pages, total = int(data['pages']), int(data['total'])
                if pages < 0 or total < 0 or (total > 0 and pages < 1) or page > 10000:
                    raise ValueError('商品分页结构异常')
                if exact_sku(data['list'], sku):
                    return {'sku': sku, 'status': 'EXISTS'}
                if page >= pages:
                    return {'sku': sku, 'status': 'NEED_CREATE'}
                page += 1
        except Exception:
            # Do not persist response bodies, cookies or request URLs in error text.
            print(f'[RETRY] {sku} {attempt}/3：查询失败，请检查登录、权限或网络。', flush=True)
            if attempt < 3:
                sleep(attempt)
    return {'sku': sku, 'status': 'ERROR'}


def save_results(market: str, store_id: str, results: list[dict]) -> dict:
    directory = workspace_paths(market)['detect']
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / f'{market}-detect.json'
    manifest_path.write_text('{"complete": false}', encoding='utf-8')
    files = {
        f'{market}-all.txt': [x['sku'] for x in results],
        f'{market}-need_create.txt': [x['sku'] for x in results if x['status'] == 'NEED_CREATE'],
        f'{market}-errors.txt': [x['sku'] for x in results if x['status'] in {'ERROR', 'PENDING'}],
    }
    for name, values in files.items():
        (directory / name).write_bytes('\r\n'.join(values).encode('utf-8'))
    manifest = {'market': market, 'store_id': store_id, 'complete': True, 'count': len(results),
                'pending': [x['sku'] for x in results if x['status'] == 'PENDING'],
                'errors': len(files[f'{market}-errors.txt']), 'checked_at': datetime.now(timezone.utc).isoformat(),
                'files': {name: sha256_file(directory / name) for name in files}}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def retry_results(market: str, store_id: str) -> list[dict]:
    directory = workspace_paths(market)['detect']
    manifest = read_json(directory / f'{market}-detect.json')
    names = [f'{market}-{part}.txt' for part in ('all', 'need_create', 'errors')]
    if (manifest.get('complete') is not True or manifest.get('market') != market or manifest.get('store_id') != store_id
            or manifest.get('files') != {name: sha256_file(directory / name) for name in names}):
        raise ValueError('检测结果不完整或店铺不一致，请重新检测完整清单。')
    all_skus = parse_skus((directory / names[0]).read_text(encoding='utf-8-sig'))
    todo = set((directory / names[1]).read_text(encoding='utf-8-sig').splitlines())
    errors = set((directory / names[2]).read_text(encoding='utf-8-sig').splitlines())
    if not (todo | errors) <= set(all_skus) or todo & errors:
        raise ValueError('检测结果不一致，请重新检测完整清单。')
    pending = set(manifest.get('pending', []))
    if not pending <= errors:
        raise ValueError('检测进度不一致，请重新检测。')
    return [{'sku': sku, 'status': 'PENDING' if sku in pending else 'ERROR' if sku in errors else 'NEED_CREATE' if sku in todo else 'EXISTS'} for sku in all_skus]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retry-errors', action='store_true')
    args = parser.parse_args()
    cfg = load_write_config()
    market = cfg['market']
    client = ArkSwiftClient(cfg['arkswift'], load_auth())
    paths = workspace_paths(market)
    results = retry_results(market, client.store_id) if args.retry_errors else [
        {'sku': sku, 'status': 'PENDING'} for sku in parse_skus((paths['input'] / 'skus.txt').read_text(encoding='utf-8-sig'))]
    pending = [i for i, item in enumerate(results) if item['status'] in {'ERROR', 'PENDING'}]
    for index in pending:
        results[index]['status'] = 'PENDING'
    save_results(market, client.store_id, results)  # Unfinished SKUs remain blocked, but are displayed separately from failures.
    for done, index in enumerate(pending, 1):
        results[index] = check_sku(client, results[index]['sku'])
        print(f"[DETECT] {done}/{len(pending)} {results[index]['sku']} → {results[index]['status']}", flush=True)
        save_results(market, client.store_id, results)
        time.sleep(.25)
    manifest = save_results(market, client.store_id, results)
    print(f"[DONE] total={len(results)} existing={sum(x['status'] == 'EXISTS' for x in results)} errors={manifest['errors']}", flush=True)
    return 1 if manifest['errors'] else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('[STOP] 检测已中断；未完成的 SKU 保持 ERROR，可重试。', file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        print('[ERROR] 检测未完成，请检查输入清单、市场配置与 ArkSwift 登录。', file=sys.stderr)
        raise SystemExit(1)
