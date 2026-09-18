from __future__ import annotations

import argparse
import csv
import json
import sys
import shutil
from datetime import datetime
from pathlib import Path

from arkswift_client import ArkSwiftClient
from catalogs import load_catalogs, norm

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR.parent))
from project_config import load_write_config, load_auth, workspace_paths, validate_extract


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find_default_review_file(market: str) -> Path:
    review_dir = workspace_paths(market)["review"]
    path = review_dir / f"mapping_review_{market}_suggested.csv"
    if path.is_file():
        return path
    raise FileNotFoundError(f"找不到已填写 suggested_target 的文件：{path}")


def parse_args():
    p = argparse.ArgumentParser(
        description="Apply AI mapping review CSV into category_map.json / attr_aliases.json"
    )
    p.add_argument(
        "--file",
        help="AI mapping review CSV 路径；省略时自动寻找 mapping_review_<market>_suggested.csv",
    )
    return p.parse_args()


def main():
    args = parse_args()
    cfg = load_write_config()
    auth = load_auth()
    market = (cfg.get("market") or "unknown").lower()

    review_path = Path(args.file) if args.file else find_default_review_file(market)
    if not review_path.is_absolute():
        review_path = (PROJECT_DIR.parent / review_path).resolve()

    print(f"[APPLY MAPPING] market={market}")
    print(f"review={review_path}")

    with review_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    client = ArkSwiftClient(cfg["arkswift"], auth)
    print("[1/3] 拉取 ArkSwift category-tree + attr-list 做最终合法性校验 ...")
    category_index, attr_index = load_catalogs(client)

    mapping_dir = PROJECT_DIR / "mapping"
    category_path = mapping_dir / "category_map.json"
    aliases_path = mapping_dir / "attr_aliases.json"

    category_map = load_json(category_path)
    aliases = load_json(aliases_path)
    aliases.setdefault("color", {})
    aliases.setdefault("material", {})
    aliases.setdefault("origin", {})

    applied = {"category": 0, "color": 0, "material": 0}
    skipped = 0
    errors = []

    print("[2/3] 使用 suggested_target 作为 AI 最终判断并写入 Mapping ...")

    for i, row in enumerate(rows, start=2):
        issue_type = (row.get("issue_type") or "").strip().lower()
        source = (row.get("source_value") or "").strip()
        # final_target 有值则优先；否则直接采用 AI 的 suggested_target。
        target = (row.get("final_target") or row.get("suggested_target") or "").strip()

        if issue_type not in {"category", "color", "material"}:
            skipped += 1
            continue
        if not source:
            skipped += 1
            print(
                f"[SKIP] CSV line {i}: {issue_type} source_value 为空，无可应用映射"
            )
            continue

        if not target:
            skipped += 1
            print(
                f"[SKIP] CSV line {i}: {issue_type} suggested_target 为空，保留待人工确认"
            )
            continue

        if issue_type == "category":
            if norm(target) not in category_index:
                errors.append(
                    f"CSV line {i}: ArkSwift category 不存在: {target!r}"
                )
                continue
            category_map[source] = category_index[norm(target)]["path"]
            applied["category"] += 1
        else:
            values = attr_index.get(issue_type, {})
            item = values.get(norm(target))
            if not item:
                errors.append(
                    f"CSV line {i}: ArkSwift {issue_type} 值不存在: {target!r}"
                )
                continue
            # 写 canonical ArkSwift name，避免大小写/空格漂移。
            aliases[issue_type][source] = item["name"]
            applied[issue_type] += 1

    if errors:
        print("\n[ABORT] 有 Mapping target 未通过 ArkSwift 实时字典校验，未修改 JSON：")
        for msg in errors[:30]:
            print("  -", msg)
        if len(errors) > 30:
            print(f"  ... 另有 {len(errors)-30} 条")
        raise RuntimeError(f"{len(errors)} 条 mapping 未通过校验")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = workspace_paths(market)["review"] / "mapping_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(category_path, backup_dir / f"category_map_{stamp}.json")
    shutil.copy2(aliases_path, backup_dir / f"attr_aliases_{stamp}.json")

    save_json(category_path, category_map)
    save_json(aliases_path, aliases)

    print("[3/3] 完成")
    print(f"category applied : {applied['category']}")
    print(f"color applied    : {applied['color']}")
    print(f"material applied : {applied['material']}")
    print(f"skipped          : {skipped}")
    print(f"updated          : {category_path}")
    print(f"updated          : {aliases_path}")
    print(f"backup           : {backup_dir}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] 已中断；可以重新运行。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
