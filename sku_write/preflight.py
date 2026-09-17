from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from arkswift_client import ArkSwiftClient
from catalogs import load_catalogs
from image_policy import select_images_for_upload
from mapper import Mapper
from payload_builder import build_draft_body
from summary_reader import read_summary


PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR.parent))
from project_config import load_write_config, load_auth, workspace_paths, validate_extract


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(value: str) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    return (PROJECT_DIR / p).resolve()


def src_category_path(row: dict) -> str:
    parts = []
    for key in ("category_1", "category_2", "category_3"):
        value = (row.get(key) or "").strip()
        if value and value != "*":
            parts.append(value)
    return " > ".join(parts)


def _source_box_qty(row: dict) -> str:
    return (row.get("box_qty") or "").strip()


def _is_multi_box(row: dict) -> bool:
    raw = _source_box_qty(row)
    if not raw:
        return False
    try:
        return float(raw.replace(",", ".")) > 1
    except Exception:
        return False


def main():
    cfg = load_write_config()
    auth = load_auth()

    market = (cfg.get("market") or "unknown").lower()
    validate_extract(market)
    summary_path = resolve_path(cfg["paths"]["summary_csv"])
    output_root = resolve_path(cfg["paths"]["output_root"])
    min_images = int(cfg["upload"].get("min_images", 5))

    print(f"[PRE-FLIGHT] market={market}")
    print(f"summary={summary_path}")

    rows = read_summary(summary_path)
    if not rows:
        raise ValueError("summary.csv 没有数据，请先完成 RevFlow。")

    client = ArkSwiftClient(cfg["arkswift"], auth)

    print("[1/3] 拉取 ArkSwift category-tree + attr-list ...")
    category_index, attr_index = load_catalogs(client)
    mapper = Mapper(PROJECT_DIR / "mapping", category_index, attr_index)

    report_dir = workspace_paths(market)["review"]
    report_dir.mkdir(parents=True, exist_ok=True)

    sku_report = []
    mapping_issues = defaultdict(
        lambda: {
            "issue_type": "",
            "source_value": "",
            "affected_skus": [],
            "sample_titles": [],
            "sample_item_names": [],
        }
    )

    print("[2/3] 扫描 mapping / 图片 / 完整 payload ...")

    for row in rows:
        sku = (
            row.get("seller_sku")
            or row.get("requested_sku")
            or ""
        ).strip()
        title = (row.get("product_title") or "").strip()
        item_name = (row.get("item_name_en") or "").strip()

        source_status = (
            row.get("status") or ""
        ).strip().upper()

        if source_status == "SKIPPED" and sku and title:
            source_status = "OK"
        if source_status != "OK":
            cl_status = (
                "CL_SKIPPED"
                if source_status in {"SKIPPED", "SKIP_EXISTS"}
                else "CL_ERROR"
            )

            sku_report.append({
                "sku": sku,
                "status": cl_status,
                "source_category": "",
                "target_category": "",
                "source_color": "",
                "target_color": "",
                "source_material": "",
                "target_material": "",
                "source_box_qty": "",
                "source_image_count": "",
                "usable_image_count": "",
                "blockers": (
                    row.get("error")
                    or f"RevFlow status={source_status}"
                ),
                "warnings": "",
                "title": title,
                "item_name_en": item_name,
            })

            continue

        blockers: list[tuple[str, str]] = []
        warnings: list[str] = []

        category_target = ""
        color_target = ""
        material_target = ""
        category = None
        sale_attrs = None

        # Mapping
        try:
            category = mapper.resolve_category(row)
            category_target = category["path"]
        except Exception as exc:
            source = src_category_path(row)
            blockers.append(("category", source or str(exc)))

        try:
            color = mapper._resolve_attr(
                "color",
                row.get("color") or "",
                sku,
            )
            color_target = color["name"]
        except Exception:
            blockers.append(
                ("color", (row.get("color") or "").strip())
            )

        try:
            material = mapper._resolve_attr(
                "material",
                row.get("material") or "",
                sku,
            )
            material_target = material["name"]
        except Exception:
            blockers.append(
                ("material", (row.get("material") or "").strip())
            )

        # Images: skip individual bad images; only < min_images is blocking.
        image_selection = None
        try:
            image_selection = select_images_for_upload(
                row,
                output_root,
                min_images=min_images,
            )
            if image_selection.skipped:
                warnings.append(
                    "images_skipped="
                    + ";".join(image_selection.skipped)
                )
            if image_selection.used_count < 12:
                warnings.append(
                    f"image_count_degraded="
                    f"{image_selection.used_count}/12"
                )
        except Exception as exc:
            blockers.append(
                ("image", f"{type(exc).__name__}:{exc}")
            )

        # Multi-box is now an intentional compatibility fallback, not an error.
        if _is_multi_box(row):
            warnings.append(
                "package_aggregation="
                "max_dimensions_sum_weights"
            )

        missing_required = (
            row.get("missing_required") or ""
        ).strip()
        if missing_required:
            warnings.append(
                f"summary_missing_required={missing_required}"
            )

        # Full local payload build. No network upload happens here.
        mapping_blocked = any(
            issue_type in {"category", "color", "material"}
            for issue_type, _ in blockers
        )

        if not mapping_blocked and image_selection is not None:
            try:
                sale_attrs, _ = mapper.resolve_sale_attrs(row)
                build_draft_body(
                    row=row,
                    cfg=cfg,
                    category_id=category["id"],
                    sale_attrs=sale_attrs,
                    imgs=[],
                )
            except Exception as exc:
                blockers.append(
                    ("payload", f"{type(exc).__name__}:{exc}")
                )

        if blockers:
            status = "BLOCKED"
        elif warnings:
            status = "READY_WITH_WARNINGS"
        else:
            status = "READY"

        sku_report.append(
            {
                "sku": sku,
                "status": status,
                "source_category": src_category_path(row),
                "target_category": category_target,
                "source_color": (row.get("color") or "").strip(),
                "target_color": color_target,
                "source_material": (row.get("material") or "").strip(),
                "target_material": material_target,
                "source_box_qty": _source_box_qty(row),
                "source_image_count": (
                    image_selection.source_count
                    if image_selection
                    else ""
                ),
                "usable_image_count": (
                    image_selection.used_count
                    if image_selection
                    else ""
                ),
                "blockers": " | ".join(
                    f"{t}:{v}" for t, v in blockers
                ),
                "warnings": " | ".join(warnings),
                "title": title,
                "item_name_en": item_name,
            }
        )

        # Only mapping problems belong in mapping_review_<market>.csv.
        for issue_type, source_value in blockers:
            if issue_type not in {"category", "color", "material"}:
                continue

            key = (issue_type, source_value)
            bucket = mapping_issues[key]
            bucket["issue_type"] = issue_type
            bucket["source_value"] = source_value
            bucket["affected_skus"].append(sku)

            if (
                title
                and title not in bucket["sample_titles"]
                and len(bucket["sample_titles"]) < 3
            ):
                bucket["sample_titles"].append(title)

            if (
                item_name
                and item_name not in bucket["sample_item_names"]
                and len(bucket["sample_item_names"]) < 3
            ):
                bucket["sample_item_names"].append(item_name)

    sku_report_path = report_dir / f"preflight_{market}.csv"
    with sku_report_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(sku_report[0].keys()),
        )
        writer.writeheader()
        writer.writerows(sku_report)

    review_rows = []
    for bucket in mapping_issues.values():
        review_rows.append(
            {
                "issue_type": bucket["issue_type"],
                "source_value": bucket["source_value"],
                "affected_count": len(bucket["affected_skus"]),
                "sample_skus": " | ".join(
                    bucket["affected_skus"][:5]
                ),
                "sample_item_names": " | ".join(
                    bucket["sample_item_names"]
                ),
                "sample_titles": " | ".join(
                    bucket["sample_titles"]
                ),
                "suggested_target": "",
                "confidence": "",
                "decision": "",
                "final_target": "",
                "review_note": "",
            }
        )

    issue_rank = {
        "category": 0,
        "color": 1,
        "material": 2,
    }
    review_rows.sort(
        key=lambda x: (
            issue_rank.get(x["issue_type"], 9),
            -int(x["affected_count"]),
            x["source_value"],
        )
    )

    review_path = (
        report_dir
        / f"mapping_review_{market}.csv"
    )

    # Always overwrite the mapping review so stale issues disappear.
    if review_rows:
        with review_path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=list(review_rows[0].keys()),
            )
            writer.writeheader()
            writer.writerows(review_rows)
    elif review_path.exists():
        review_path.unlink()

    counts = defaultdict(int)
    for item in sku_report:
        counts[item["status"]] += 1

    runnable = (
        counts["READY"]
        + counts["READY_WITH_WARNINGS"]
    )

    print("[3/3] 完成")
    print()
    print(f"Total SKU            : {len(rows)}")
    print(f"READY                : {counts['READY']}")
    print(
        f"READY_WITH_WARNINGS  : "
        f"{counts['READY_WITH_WARNINGS']}"
    )
    print(f"BLOCKED              : {counts['BLOCKED']}")
    print(
        f"CL_ERROR             : "
        f"{counts['CL_ERROR']}"
    )
    print(f"CL_SKIPPED           : {counts['CL_SKIPPED']}")
    print(f"Runnable by main.py  : {runnable}")
    print()
    print(f"SKU report    : {sku_report_path}")
    if review_rows:
        print(f"Mapping review: {review_path}")
    else:
        print("Mapping review: none")

    if review_rows:
        print("[GATE] 尚有 Mapping 问题；请完成建议、apply mapping，再重新执行 preflight。")
    if counts["BLOCKED"] or counts["CL_ERROR"]:
        print("[GATE] 请先处理报告中的 BLOCKED / CL_ERROR，再做 dry-run 和正式写入。")
    return 1 if counts["BLOCKED"] or counts["CL_ERROR"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] 已中断；可以重新运行。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
