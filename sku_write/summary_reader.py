from __future__ import annotations

import csv
import re
from pathlib import Path


def read_summary(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pick_rows(rows: list[dict], target_sku: str | None, max_items: int | None) -> list[dict]:
    """
    Preserve RevFlow's original SKU order and include non-action rows in the handoff.

    Why: summary.csv is now the full market worklist. Rows such as SKIP_EXISTS must
    reach sku_write so the final audit can explicitly record that sku_detect already
    found an ArkSwift record. Only OK/cached-SKIPPED rows count toward max_items,
    keeping the old "limit actionable SKUs" behavior useful for test runs.
    """
    if target_sku:
        selected = [
            r for r in rows
            if (r.get("seller_sku") or r.get("requested_sku") or "").strip() == target_sku
        ]
        if not selected:
            raise KeyError(f"summary.csv 未找到 SKU: {target_sku}")
        return selected[:1]

    if max_items is None:
        return list(rows)

    limit = max(0, int(max_items))
    if limit == 0:
        return []

    selected: list[dict] = []
    actionable = 0
    for row in rows:
        selected.append(row)
        status = (row.get("status") or "").strip().upper()
        if status in {"OK", "SKIPPED"}:
            actionable += 1
            if actionable >= limit:
                break
    return selected


def rel_path(value: str) -> Path:
    parts = [p for p in re.split(r"[\\/]+", (value or "").strip()) if p]
    return Path(*parts)


def image_paths_for_row(row: dict, output_root: Path) -> list[Path]:
    images_dir = rel_path(row.get("images_dir") or "")
    names = [
        x.strip()
        for x in (row.get("image_files") or "").split("|")
        if x.strip()
    ]
    if not images_dir.parts:
        raise ValueError(f"{row.get('seller_sku')}: images_dir 为空")
    if not names:
        raise ValueError(f"{row.get('seller_sku')}: image_files 为空")
    return [output_root / images_dir / name for name in names]
