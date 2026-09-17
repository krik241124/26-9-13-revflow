from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
import sys
import time

from arkswift_client import ArkSwiftClient
from catalogs import load_catalogs
from image_policy import select_images_for_upload
from image_uploader import upload_images
from mapper import Mapper
from payload_builder import build_draft_body
from state_store import StateStore
from summary_reader import read_summary, pick_rows


PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR.parent))
from project_config import load_write_config, load_auth, workspace_paths, validate_extract


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def setup_auto_log(market: str) -> Path:
    log_dir = workspace_paths(market)["logs"]
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"run_{market}_{stamp}.log"
    log_file = log_path.open("w", encoding="utf-8", buffering=1)

    sys.stdout = Tee(sys.__stdout__, log_file)
    sys.stderr = Tee(sys.__stderr__, log_file)

    print(f"[LOG] {log_path}")
    return log_path


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)

    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(value: str) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    return (PROJECT_DIR / p).resolve()


def parse_args():
    p = argparse.ArgumentParser(description="RevFlow -> ArkSwift Draft")
    p.add_argument("--sku", help="只处理指定 SKU")
    p.add_argument("--dry-run", action="store_true", help="只校验/映射，不上传、不保存")
    p.add_argument("--force", action="store_true", help="忽略已成功状态；若已有 skuId 则更新该草稿")
    return p.parse_args()


def ensure_market_state_dir(market: str) -> tuple[Path, Path]:
    # Historical market-specific state is migrated once during delivery cleanup.
    # Never copy an unlabelled legacy state into the selected market.
    market_dir = workspace_paths(market)["state"]
    results_path = market_dir / "results.jsonl"
    payload_dir = market_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    return results_path, payload_dir


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


def is_already_exists_error(exc: Exception) -> bool:
    text = str(exc)
    return "150035" in text or "Seller SKU信息已存在" in text


AUDIT_FIELDS = [
    "market",
    "seller_sku",
    "run_status",
    "arkswift_sku_id",
    "saved_at",
    "title",
    "category_id",
    "category_path",
    "source_color",
    "target_color",
    "color_id",
    "source_material",
    "target_material",
    "material_id",
    "description",
    "feature_1",
    "feature_2",
    "feature_3",
    "feature_4",
    "feature_5",
    "length_cm",
    "width_cm",
    "height_cm",
    "net_weight_kg",
    "source_box_qty",
    "box_qty",
    "package_aggregation",
    "box_length_cm",
    "box_width_cm",
    "box_height_cm",
    "gross_weight_kg",
    "source_image_count",
    "usable_image_count",
    "image_count",
    "skipped_images",
    "warnings",
    "image_urls",
    "is_white_brand",
    "is_original",
    "error",
    "payload_json",
]


def upsert_audit_row(path: Path, row: dict) -> None:
    """
    审计 CSV 永远保持一行一个 SKU，重复运行时覆盖该 SKU 的最新状态，
    不无限追加重复记录。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}

    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for old in csv.DictReader(f):
                key = (old.get("market", ""), old.get("seller_sku", ""))
                existing[key] = old

    normalized = {field: row.get(field, "") for field in AUDIT_FIELDS}
    key = (normalized["market"], normalized["seller_sku"])
    existing[key] = normalized

    rows = list(existing.values())
    rows.sort(key=lambda x: (x.get("market", ""), x.get("seller_sku", "")))

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def reorder_audit_to_summary(path: Path, market: str, source_rows: list[dict]) -> None:
    """Keep final audit rows in the same SKU order as RevFlow summary/skus.txt."""
    if not path.exists():
        return

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    by_key = {
        (r.get("market", ""), r.get("seller_sku", "")): r
        for r in rows
    }
    ordered = []
    seen = set()

    for source in source_rows:
        sku = (source.get("seller_sku") or source.get("requested_sku") or "").strip()
        key = (market, sku)
        row = by_key.get(key)
        if row is not None and key not in seen:
            ordered.append(row)
            seen.add(key)

    # Preserve any historical/extra rows after the current source order.
    for row in rows:
        key = (row.get("market", ""), row.get("seller_sku", ""))
        if key not in seen:
            ordered.append(row)
            seen.add(key)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(ordered)


def build_audit_success(
    *,
    market: str,
    row: dict,
    sku_id: str,
    category: dict,
    attr_debug: dict,
    body: dict,
    source_image_count: int,
    skipped_images: list[str],
    warnings: list[str],
) -> dict:
    imgs = body.get("imgs") or []
    specs = body.get("specs") or []
    size = body.get("size") or {}
    package = body.get("packageInfo") or {}
    boxes = package.get("boxes") or []
    box = boxes[0] if boxes else {}

    source_box_qty = _source_box_qty(row)
    package_aggregation = (
        "max_dimensions_sum_weights"
        if _is_multi_box(row)
        else "direct"
    )

    out = {
        "market": market,
        "seller_sku": body.get("sellerSku", ""),
        "run_status": "DRAFT_SAVED",
        "arkswift_sku_id": sku_id,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": body.get("title", ""),
        "category_id": category["id"],
        "category_path": category["path"],
        "source_color": (row.get("color") or "").strip(),
        "target_color": attr_debug["color"]["name"],
        "color_id": attr_debug["color"]["id"],
        "source_material": (row.get("material") or "").strip(),
        "target_material": attr_debug["material"]["name"],
        "material_id": attr_debug["material"]["id"],
        "description": body.get("desc", ""),
        "length_cm": size.get("length", ""),
        "width_cm": size.get("width", ""),
        "height_cm": size.get("height", ""),
        "net_weight_kg": size.get("weight", ""),
        "source_box_qty": source_box_qty,
        "box_qty": len(boxes),
        "package_aggregation": package_aggregation,
        "box_length_cm": box.get("length", ""),
        "box_width_cm": box.get("width", ""),
        "box_height_cm": box.get("height", ""),
        "gross_weight_kg": box.get("weight", ""),
        "source_image_count": source_image_count,
        "usable_image_count": len(imgs),
        "image_count": len(imgs),
        "skipped_images": ";".join(skipped_images),
        "warnings": " | ".join(warnings),
        "image_urls": "|".join(str(x.get("url", "")) for x in imgs),
        "is_white_brand": body.get("isWhiteBrand", ""),
        "is_original": body.get("isOriginal", ""),
        "error": "",
        "payload_json": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    }

    for i in range(5):
        out[f"feature_{i+1}"] = specs[i] if i < len(specs) else ""

    return out


def main():
    args = parse_args()
    cfg = load_write_config()
    if cfg["run"].get("mode") != "draft":
        raise RuntimeError("当前版本只允许 run.mode=draft")

    market = (
        cfg.get("market")
        or cfg["arkswift"].get("country_code")
        or "unknown"
    ).lower()

    log_path = setup_auto_log(market)

    validate_extract(market)
    auth = load_auth()

    summary_path = resolve_path(cfg["paths"]["summary_csv"])
    output_root = resolve_path(cfg["paths"]["output_root"])
    min_images = int(cfg["upload"].get("min_images", 5))

    rows = read_summary(summary_path)
    target_sku = args.sku or cfg["run"].get("target_sku")
    selected = pick_rows(rows, target_sku, cfg["run"].get("max_items"))

    client = ArkSwiftClient(cfg["arkswift"], auth)

    print(f"[MARKET] {market.upper()}")
    print("[1/4] 拉取 ArkSwift category-tree + attr-list ...")
    category_index, attr_index = load_catalogs(client)
    print(f"      categories={len(category_index)} attrs={','.join(sorted(attr_index))}")

    mapper = Mapper(PROJECT_DIR / "mapping", category_index, attr_index)

    results_path, payload_dir = ensure_market_state_dir(market)
    state = StateStore(results_path)
    audit_path = workspace_paths(market)["audit"] / f"arkswift_draft_summary_{market}.csv"
    if args.dry_run:
        # A rehearsal must never overwrite a real success/error audit or resume state.
        audit_path = workspace_paths(market)["review"] / f"dry_run_{market}.csv"
        state = StateStore(None)

    terminal_statuses = {"DRAFT_SAVED", "ALREADY_EXISTS"}
    pending = []

    for row in selected:
        sku = (row.get("seller_sku") or row.get("requested_sku") or "").strip()
        source_status = (row.get("status") or "").strip().upper()
        previous = state.latest_for_sku(sku)

        # SKIP_EXISTS is intentionally re-emitted into the current audit even when
        # an older local state exists. It is cheap/local and keeps the new full audit
        # aligned with the latest sku_detect result for this market.
        if source_status != "SKIP_EXISTS" and (
            previous
            and previous.get("status") in terminal_statuses
            and cfg["run"].get("skip_if_success", True)
            and not args.force
        ):
            continue

        pending.append(row)

    total_count = len(selected)
    completed_before = total_count - len(pending)

    print(
        f"[QUEUE] total={total_count} "
        f"completed={completed_before} "
        f"pending={len(pending)}"
    )

    ok_count = 0
    exists_count = 0
    skip_exists_count = 0
    error_count = 0
    run_started = time.perf_counter()
    sku_durations: list[float] = []

    for run_index, row in enumerate(pending, start=1):
        sku_started = time.perf_counter()
        sku = (row.get("seller_sku") or row.get("requested_sku") or "").strip()
        processed_total = completed_before + run_index

        print(f"\n=== [{processed_total}/{total_count}] {sku} ===")

        category = None
        attr_debug = None
        body = None
        image_selection = None
        runtime_warnings: list[str] = []

        try:
            source_status = (
                row.get("status") or ""
            ).strip().upper()

            # New full-worklist handoff: sku_detect already confirmed that this SKU
            # exists in the CURRENT ArkSwift store. This is a normal terminal outcome
            # for this run, not a CL failure and not something to upload again.
            if source_status == "SKIP_EXISTS":
                skip_exists_count += 1
                upsert_audit_row(
                    audit_path,
                    {
                        "market": market,
                        "seller_sku": sku,
                        "run_status": "SKIP_EXISTS",
                        "saved_at": (
                            datetime.now()
                            .astimezone()
                            .isoformat(timespec="seconds")
                        ),
                        "title": (row.get("product_title") or "").strip(),
                        "warnings": (row.get("warnings") or "").strip(),
                        "error": "",
                    },
                )
                print("[SKIP_EXISTS] sku_detect 已确认目标店铺存在记录；跳过 ArkSwift 创建")
                continue

            # Backward compatibility: old RevFlow used SKIPPED for a fully cached CL
            # record. Such rows contain real product data and remain actionable.
            if source_status == "SKIPPED" and (
                (row.get("seller_sku") or "").strip()
                and (row.get("product_title") or "").strip()
            ):
                source_status = "OK"

            cl_status = "CL_ERROR"
            if source_status != "OK":
                error_count += 1
                source_error = (
                    row.get("error")
                    or row.get("warnings")
                    or f"RevFlow status={source_status}"
                ).strip()

                upsert_audit_row(
                    audit_path,
                    {
                        "market": market,
                        "seller_sku": sku,
                        "run_status": cl_status,
                        "saved_at": (
                            datetime.now()
                            .astimezone()
                            .isoformat(timespec="seconds")
                        ),
                        "title": (
                            row.get("product_title")
                            or ""
                        ).strip(),
                        "error": source_error,
                    },
                )

                print(
                    f"[{cl_status}] RevFlow/CL 未提取成功，"
                    f"跳过 ArkSwift：{source_error}"
                )

                continue

            previous = state.latest_for_sku(sku)

            print("[2/4] 分类/属性映射 + 图片容错 + 完整 Payload 校验 ...")
            category = mapper.resolve_category(row)
            sale_attrs, attr_debug = mapper.resolve_sale_attrs(row)

            image_selection = select_images_for_upload(
                row,
                output_root,
                min_images=min_images,
            )
            image_paths = image_selection.selected

            if image_selection.skipped:
                warning = "跳过异常图片: " + "; ".join(image_selection.skipped)
                runtime_warnings.append(warning)
                print(f"      [WARN] {warning}")

            if len(image_paths) < 12:
                warning = f"图片降级为 {len(image_paths)}/12，仍满足最低 {min_images} 张"
                runtime_warnings.append(warning)
                print(f"      [WARN] {warning}")

            if _is_multi_box(row):
                warning = "多箱已压缩为 1 行包装：尺寸取最大值，净重/毛重求和"
                runtime_warnings.append(warning)
                print(f"      [WARN] {warning}")

            existing_sku_id = None
            if (
                args.force
                and previous
                and previous.get("sku_id")
                and cfg["run"].get("update_existing_from_state", True)
            ):
                existing_sku_id = str(previous["sku_id"])
                print(f"      update existing draft skuId={existing_sku_id}")

            # Full local validation before any upload.
            body = build_draft_body(
                row=row,
                cfg=cfg,
                category_id=category["id"],
                sale_attrs=sale_attrs,
                imgs=[],
                sku_id=existing_sku_id,
            )

            print(f"      category={category['path']} (id={category['id']})")
            print(
                f"      color={attr_debug['color']['name']} "
                f"(id={attr_debug['color']['id']})"
            )
            print(
                f"      material={attr_debug['material']['name']} "
                f"(id={attr_debug['material']['id']})"
            )
            print(f"      images={len(image_paths)} usable")

            if args.dry_run:
                print("[DRY RUN] 完整本地校验通过；未上传、未保存草稿。")
                continue

            print(
                f"[3/4] 上传图片到 ArkSwift 素材库 "
                f"(OSS concurrency={cfg['upload']['concurrency']}) ..."
            )
            imgs = upload_images(
                client=client,
                image_paths=image_paths,
                sku=sku,
                concurrency=int(cfg["upload"].get("concurrency", 3)),
                timeout=int(cfg["upload"].get("timeout_seconds", 120)),
                retries=int(cfg["upload"].get("retries", 2)),
            )
            print(f"      registered={len(imgs)}")

            body["imgs"] = imgs

            if cfg["run"].get("save_payload_snapshot", True):
                with (payload_dir / f"{sku}.json").open("w", encoding="utf-8") as f:
                    json.dump(body, f, ensure_ascii=False, indent=2)

            print("[4/4] POST save-draft ...")
            sku_id = client.save_draft(body)

            state.append({
                "market": market,
                "sku": sku,
                "status": "DRAFT_SAVED",
                "sku_id": sku_id,
                "category_id": category["id"],
                "category_path": category["path"],
                "color_id": attr_debug["color"]["id"],
                "material_id": attr_debug["material"]["id"],
                "source_box_qty": _source_box_qty(row),
                "package_aggregation": (
                    "max_dimensions_sum_weights" if _is_multi_box(row) else "direct"
                ),
                "images": len(imgs),
                "warnings": runtime_warnings,
            })

            upsert_audit_row(
                audit_path,
                build_audit_success(
                    market=market,
                    row=row,
                    sku_id=sku_id,
                    category=category,
                    attr_debug=attr_debug,
                    body=body,
                    source_image_count=image_selection.source_count,
                    skipped_images=image_selection.skipped,
                    warnings=runtime_warnings,
                ),
            )

            ok_count += 1
            print(f"[OK] 草稿保存成功 skuId={sku_id}")

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"

            if is_already_exists_error(exc):
                exists_count += 1

                state.append({
                    "market": market,
                    "sku": sku,
                    "status": "ALREADY_EXISTS",
                    "error": error_text,
                })

                upsert_audit_row(
                    audit_path,
                    {
                        "market": market,
                        "seller_sku": sku,
                        "run_status": "ALREADY_EXISTS",
                        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "title": (row.get("product_title") or "").strip(),
                        "category_id": category["id"] if category else "",
                        "category_path": category["path"] if category else "",
                        "source_color": (row.get("color") or "").strip(),
                        "target_color": (
                            attr_debug["color"]["name"] if attr_debug else ""
                        ),
                        "color_id": (
                            attr_debug["color"]["id"] if attr_debug else ""
                        ),
                        "source_material": (row.get("material") or "").strip(),
                        "target_material": (
                            attr_debug["material"]["name"] if attr_debug else ""
                        ),
                        "material_id": (
                            attr_debug["material"]["id"] if attr_debug else ""
                        ),
                        "source_box_qty": _source_box_qty(row),
                        "package_aggregation": (
                            "max_dimensions_sum_weights" if _is_multi_box(row) else "direct"
                        ),
                        "source_image_count": (
                            image_selection.source_count if image_selection else ""
                        ),
                        "usable_image_count": (
                            image_selection.used_count if image_selection else ""
                        ),
                        "image_count": "",
                        "skipped_images": (
                            ";".join(image_selection.skipped) if image_selection else ""
                        ),
                        "warnings": " | ".join(runtime_warnings),
                        "error": error_text,
                    },
                )

                print("[EXISTS] Seller SKU 已存在，视为已处理；后续自动跳过")

            else:
                error_count += 1

                state.append({
                    "market": market,
                    "sku": sku,
                    "status": "ERROR",
                    "error": error_text,
                })

                upsert_audit_row(
                    audit_path,
                    {
                        "market": market,
                        "seller_sku": sku,
                        "run_status": "ERROR",
                        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "title": (row.get("product_title") or "").strip(),
                        "category_id": category["id"] if category else "",
                        "category_path": category["path"] if category else "",
                        "source_color": (row.get("color") or "").strip(),
                        "target_color": (
                            attr_debug["color"]["name"] if attr_debug else ""
                        ),
                        "color_id": (
                            attr_debug["color"]["id"] if attr_debug else ""
                        ),
                        "source_material": (row.get("material") or "").strip(),
                        "target_material": (
                            attr_debug["material"]["name"] if attr_debug else ""
                        ),
                        "material_id": (
                            attr_debug["material"]["id"] if attr_debug else ""
                        ),
                        "source_box_qty": _source_box_qty(row),
                        "box_qty": 1 if body else "",
                        "package_aggregation": (
                            "max_dimensions_sum_weights" if _is_multi_box(row) else "direct"
                        ),
                        "source_image_count": (
                            image_selection.source_count if image_selection else ""
                        ),
                        "usable_image_count": (
                            image_selection.used_count if image_selection else ""
                        ),
                        "image_count": "",
                        "skipped_images": (
                            ";".join(image_selection.skipped) if image_selection else ""
                        ),
                        "warnings": " | ".join(runtime_warnings),
                        "error": error_text,
                    },
                )

                print(f"[ERROR] {error_text}", file=sys.stderr)
                if cfg["run"].get("stop_on_error", False):
                    raise

        finally:
            sku_elapsed = time.perf_counter() - sku_started
            sku_durations.append(sku_elapsed)

            elapsed = time.perf_counter() - run_started
            avg = sum(sku_durations) / len(sku_durations)
            remaining = len(pending) - run_index
            eta = avg * remaining

            print(
                "[PROGRESS] "
                f"processed={processed_total}/{total_count} | "
                f"this_run={run_index}/{len(pending)} | "
                f"saved={ok_count} exists={exists_count} "
                f"pre_skipped={skip_exists_count} errors={error_count} | "
                f"elapsed={fmt_duration(elapsed)} | "
                f"avg={avg:.1f}s/SKU | "
                f"ETA={fmt_duration(eta)}"
            )

    if not args.dry_run:
        reorder_audit_to_summary(audit_path, market, selected)

    print("\nDone.")
    print(
        f"total={total_count} "
        f"completed_before={completed_before} "
        f"saved_this_run={ok_count} "
        f"exists_this_run={exists_count} "
        f"pre_skipped_this_run={skip_exists_count} "
        f"errors_this_run={error_count}"
    )

    if not args.dry_run:
        print(f"audit={audit_path}")
        print(f"state={results_path}")
    print(f"log={log_path}")
    return 1 if error_count else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] 已中断；可以重新运行。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
