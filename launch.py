"""Stable command entry point, independent of the caller's working directory."""
from __future__ import annotations
import argparse
import subprocess
import sys
from project_config import PROJECT_ROOT, load_runtime, load_write_config, read_json, workspace_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="ArkSwift AutoListing — Detect → Extract → Preflight → Dry Run → Draft")
    parser.add_argument("command", choices=["init", "check", "detect", "extract", "preflight", "mapping", "dry-run", "write", "test"])
    args, extra = parser.parse_known_args()
    if args.command in {"init", "check"}:
        if extra:
            parser.error("init/check 不接受额外参数")
        if args.command == "init":
            for market in read_json(PROJECT_ROOT / "config" / "markets.json"):
                for path in workspace_paths(market).values():
                    path.mkdir(parents=True, exist_ok=True)
            print("[OK] workspace 目录已准备好。")
        cfg = load_write_config()
        print(f"[MARKET] {cfg['market'].upper()} | storeId={cfg['arkswift']['store_id']}")
        print(f"[INPUT] {workspace_paths(cfg['market'])['input'] / 'skus.txt'}")
        print(f"[OUTPUT] {cfg['paths']['output_root']}")
        for name in ("revflow/getdetail.curl.txt", "sku_write/auth.json"):
            print(f"[{'PRESENT' if (PROJECT_ROOT / name).is_file() else 'MISSING'}] {name}")
        print("配置检查完成；未连接服务器，未验证会话或实际店铺权限。")
        return 0
    if args.command == "test":
        command = [sys.executable, "-m", "unittest", "discover", "-s", str(PROJECT_ROOT / "tests"), "-v", *extra]
    else:
        entry = {"detect": "sku_detect/detect.py", "extract": "revflow/revflow.py", "preflight": "sku_write/preflight.py",
                 "mapping": "sku_write/apply_mapping_review.py", "dry-run": "sku_write/main.py",
                 "write": "sku_write/main.py"}[args.command]
        if args.command == "preflight" and extra:
            parser.error("preflight 不接受额外参数")
        command = [sys.executable, str(PROJECT_ROOT / entry)]
        if args.command == "dry-run":
            command.append("--dry-run")
        command.extend(extra)
    return subprocess.call(command, cwd=PROJECT_ROOT)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
