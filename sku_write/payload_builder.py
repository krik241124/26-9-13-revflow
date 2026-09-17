from __future__ import annotations

from typing import Any


def _blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def _num(v: Any, field: str):
    if _blank(v):
        raise ValueError(f"{field} 为空")
    try:
        x = float(str(v).strip().replace(",", "."))
    except Exception as exc:
        raise ValueError(f"{field} 非数字: {v!r}") from exc
    return int(x) if x.is_integer() else x


def _bool_or_default(v: Any, default: bool) -> bool:
    if _blank(v):
        return bool(default)
    s = str(v).strip().casefold()
    if s in {"1", "true", "yes", "y", "是"}:
        return True
    if s in {"0", "false", "no", "n", "否"}:
        return False
    return bool(default)


def _weight_num(v: Any, field: str):
    if _blank(v):
        raise ValueError(f"{field} 为空")

    parts = [
        x.strip()
        for x in str(v).split(",")
        if x.strip()
    ]

    try:
        return round(
            sum(float(x) for x in parts),
            2,
        )
    except Exception as exc:
        raise ValueError(
            f"{field} 非数字列表: {v!r}"
        ) from exc


def build_draft_body(
    row: dict,
    cfg: dict,
    category_id: int,
    sale_attrs: list[dict],
    imgs: list[dict],
    sku_id: str | None = None,
) -> dict:
    sku = (row.get("seller_sku") or row.get("requested_sku") or "").strip()
    title = (row.get("product_title") or "").strip()
    desc = (row.get("description") or "").strip()

    if not sku:
        raise ValueError("seller_sku/requested_sku 为空")
    if not title:
        raise ValueError(f"{sku}: product_title 为空")
    if not desc:
        raise ValueError(f"{sku}: description 为空")

    box_qty = int(_num(row.get("box_qty"), "box_qty"))
    def _ark_dim(v: Any, field: str):
        x = _num(v, field)

        if not 0.01 <= float(x) <= 1999.99:
            raise ValueError(
                f"{field} 超出 ArkSwift 允许范围 0.01~1999.99: {x}"
            )

        return x
    def _sum_num(v: Any, field: str):
        if _blank(v):
            raise ValueError(f"{field} 为空")

        parts = [
            x.strip()
            for x in str(v).split(",")
            if x.strip()
        ]

        try:
            return round(sum(float(x) for x in parts), 2)
        except Exception as exc:
            raise ValueError(f"{field} 非数字列表: {v!r}") from exc

    features = []
    for i in range(1, 6):
        value = (row.get(f"feature_{i}") or "").strip()
        if value:
            features.append(value)
    if not features:
        raise ValueError(f"{sku}: 没有 product features")

    size = {
        "length": _ark_dim(row.get("length_cm"), "length_cm"),
        "width": _ark_dim(row.get("width_cm"), "width_cm"),
        "height": _ark_dim(row.get("height_cm"), "height_cm"),
        "weight": _weight_num(
            row.get("net_weight_kg"),
            "net_weight_kg",
        ),
        "isLengthNotAvailable": False,
        "isWidthNotAvailable": False,
        "isHeightNotAvailable": False,
        "isWeightNotAvailable": False,
    }

    box = {
        "subBoxNumber": "1",
        "quantity": 1,
        "length": _ark_dim(row.get("box_length_cm"), "box_length_cm"),
        "width": _ark_dim(row.get("box_width_cm"), "box_width_cm"),
        "height": _ark_dim(row.get("box_height_cm"), "box_height_cm"),
        "weight": _weight_num(
            row.get("gross_weight_kg"),
            "gross_weight_kg",
        ),
    }

    defaults = cfg["defaults"]
    body = {
        "sellerId": str(cfg["arkswift"]["seller_id"]),
        "title": title,
        "desc": desc,
        "moreInfo": "",
        # UPC schema has not yet been reverse-tested; keep confirmed-safe empty form for MVP.
        "upcs": [],
        "sellerSku": sku,
        "associatedSkuIds": [],
        "categoryId": int(category_id),
        "specs": features,
        "size": size,
        "packageInfo": {
            "productType": int(defaults.get("product_type", 1)),
            "length": box["length"],
            "width": box["width"],
            "height": box["height"],
            "weight": box["weight"],
            "boxes": [box],
            "boxImgs": [],
        },
        "imgs": imgs,
        "isWhiteBrand": _bool_or_default(
            row.get("is_white_label"),
            defaults.get("is_white_brand", False),
        ),
        "isOriginal": _bool_or_default(
            row.get("is_original"),
            defaults.get("is_original", False),
        ),
        "saleAttrs": sale_attrs,
        "attachments": [],
    }

    if sku_id:
        body["skuId"] = str(sku_id)

    return body
