from __future__ import annotations

import json
from pathlib import Path
from catalogs import norm


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class Mapper:
    def __init__(self, mapping_dir: Path, category_index: dict, attr_index: dict):
        self.category_map = _load_json(mapping_dir / "category_map.json")
        self.aliases = _load_json(mapping_dir / "attr_aliases.json")
        self.sku_overrides = _load_json(mapping_dir / "sku_overrides.json")
        self.category_index = category_index
        self.attr_index = attr_index

    def resolve_category(self, row: dict) -> dict:
        sku = (row.get("seller_sku") or row.get("requested_sku") or "").strip()
        override = self.sku_overrides.get(sku, {})

        target = (override.get("category_path") or "").strip()
        if not target:
            target = (row.get("arkswift_category") or "").strip()

        if not target:
            source_parts = []
            for key in ("category_1", "category_2", "category_3"):
                value = (row.get(key) or "").strip()
                if value and value != "*":
                    source_parts.append(value)
            source_path = " > ".join(source_parts)
            target = self.category_map.get(source_path, "")

        if not target:
            raise KeyError(
                f"{sku}: 无 ArkSwift 分类映射。"
                "请补 mapping/category_map.json 或 sku_overrides.json"
            )

        item = self.category_index.get(norm(target))
        if not item:
            raise KeyError(f"{sku}: ArkSwift category path 不存在: {target}")
        return item

    def _resolve_attr(self, code: str, raw_value: str, sku: str) -> dict:
        override = self.sku_overrides.get(sku, {})
        source_value = (override.get(code) or raw_value or "").strip()
        if not source_value:
            raise KeyError(f"{sku}: {code} 为空")

        target_value = self.aliases.get(code, {}).get(source_value, source_value)
        values = self.attr_index.get(code)
        if not values:
            raise KeyError(f"ArkSwift attr-list 没有 attrCode={code}")

        item = values.get(norm(target_value))
        if not item:
            raise KeyError(
                f"{sku}: {code} 无法映射: {source_value!r} -> {target_value!r}"
            )
        return item

    def resolve_sale_attrs(self, row: dict) -> tuple[list[dict], dict]:
        sku = (row.get("seller_sku") or row.get("requested_sku") or "").strip()
        color = self._resolve_attr("color", row.get("color") or "", sku)
        material = self._resolve_attr("material", row.get("material") or "", sku)

        sale_attrs = [
            {"attrCode": "color", "attrValId": color["id"]},
            {"attrCode": "material", "attrValId": material["id"]},
        ]
        debug = {"color": color, "material": material}
        return sale_attrs, debug
