from __future__ import annotations

import re
from typing import Any


def norm(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def build_category_index(tree: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}

    def walk(nodes: list[dict], parents: list[str]) -> None:
        for node in nodes or []:
            name = str(node.get("name") or "").strip()
            if not name:
                continue
            path_parts = parents + [name]
            full_path = " > ".join(path_parts)
            index[norm(full_path)] = {
                "id": int(node["id"]),
                "name": name,
                "path": full_path,
                "level": node.get("level"),
                "pid": node.get("pid"),
            }
            walk(node.get("children") or [], path_parts)

    walk(tree, [])
    return index


def build_attr_index(attr_list: list[dict]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for attr in attr_list or []:
        code = str(attr.get("code") or attr.get("name") or "").strip()
        if not code:
            continue
        values: dict[str, dict] = {}
        for item in attr.get("attrVals") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            values[norm(name)] = {
                "id": int(item["id"]),
                "name": name,
            }
        out[code] = values
    return out


def load_catalogs(client) -> tuple[dict[str, dict], dict[str, dict[str, dict]]]:
    category_index = build_category_index(client.get_category_tree())
    attr_index = build_attr_index(client.get_attr_list())
    return category_index, attr_index
