"""Shared market configuration and file handoff checks; no network operations."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"缺少文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"JSON 格式错误：{path}；请检查双引号和逗号。") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON 顶层必须为对象：{path}")
    return data


def load_runtime() -> dict:
    cfg = read_json(PROJECT_ROOT / "config" / "runtime.json")
    market = cfg.get("market", "")
    if not isinstance(market, str) or not re.fullmatch(r"[a-z]{2}", market):
        raise ValueError("config/runtime.json 的 market 必须是小写两位国家代码。")
    return cfg


def market_config(market: str) -> dict:
    markets = read_json(PROJECT_ROOT / "config" / "markets.json")
    info = markets.get(market)
    if not isinstance(info, dict):
        raise ValueError(f"config/markets.json 未配置市场 {market}")
    for key in ("store_id", "seller_id"):
        value = info.get(key)
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            raise ValueError(f"{market.upper()} 的 {key} 尚未确认；请填写 config/markets.json（ID 必须用引号）。")
    if info.get("country_code") != market.upper():
        raise ValueError(f"{market} 的 country_code 不匹配，请检查 config/markets.json。")
    return info


def workspace_paths(market: str) -> dict[str, Path]:
    if not re.fullmatch(r"[a-z]{2}", market):
        raise ValueError("无效市场代码")
    root = PROJECT_ROOT / "workspace" / market
    write = root / "03_write"
    return {"input": root / "00_input", "detect": root / "01_detect",
            "extract": root / "02_extract", "write": write,
            **{name: write / name for name in ("review", "audit", "state", "logs")}}


def load_write_config() -> dict:
    runtime = load_runtime()
    market = runtime["market"]
    cfg = read_json(PROJECT_ROOT / "sku_write" / "config.json")
    # Market, IDs and data paths have one authority. Refuse old duplicated fields.
    if any(key in cfg for key in ("market", "arkswift", "paths")):
        raise ValueError("sku_write/config.json 仍含旧 market/arkswift/paths；请使用交付版配置。")
    paths = workspace_paths(market)
    cfg["market"] = market
    cfg["arkswift"] = {"base_url": "https://www.arkswift.com", "lang": "cn", **market_config(market)}
    cfg["paths"] = {"summary_csv": str(paths["extract"] / "summary.csv"),
                    "output_root": str(paths["extract"])}
    if cfg.get("run", {}).get("mode") != "draft":
        raise ValueError("当前版本只允许 run.mode=draft")
    if not 5 <= int(cfg.get("upload", {}).get("min_images", 5)) <= 12:
        raise ValueError("upload.min_images 必须在 5～12 之间。")
    return cfg


def load_auth() -> dict:
    path = PROJECT_ROOT / "sku_write" / "auth.json"
    if not path.is_file():
        raise FileNotFoundError("请复制 sku_write/auth.example.json 为 auth.json，再填入当前 ArkSwift 登录凭证。")
    auth = read_json(path)
    if not any(str(auth.get(key) or "").strip() for key in ("raw_cookie", "authorization_web")):
        raise ValueError("sku_write/auth.json 凭证为空；请重新登录 ArkSwift 并更新。")
    return auth


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_detect(market: str) -> dict:
    directory = workspace_paths(market)["detect"]
    names = [f"{market}-{suffix}.txt" for suffix in ("all", "need_create", "errors")]
    for name in names + [f"{market}-detect.json"]:
        if not (directory / name).is_file():
            raise FileNotFoundError(f"SKU Detect 未完成，缺少：{directory / name}。请先检测并保存整套结果。")
    if (directory / names[2]).read_text(encoding="utf-8-sig").strip():
        raise ValueError(f"SKU Detect 存在 ERROR：{directory / names[2]}。请重新检测原始完整 SKU 清单；禁止继续 RevFlow。")
    manifest = read_json(directory / f"{market}-detect.json")
    info = market_config(market)
    if manifest.get("market") != market or manifest.get("store_id") != info["store_id"]:
        raise ValueError("Detect 结果的市场/店铺与当前配置不一致，请重新检测。")
    if manifest.get("complete") is not True or manifest.get("errors") != 0:
        raise ValueError("Detect 未成功完成，请重新检测原始完整 SKU 清单。")
    hashes = {name: sha256_file(directory / name) for name in names}
    if hashes != manifest.get("files"):
        raise ValueError("Detect 文件不属于同一次完整保存，或已被修改。请重新检测并保存整套结果。")
    return manifest


def write_extract_context(market: str, manifest: dict, *, complete: bool) -> None:
    directory = workspace_paths(market)["extract"]
    info = market_config(market)
    context = {"market": market, "store_id": info["store_id"], "complete": complete,
               "detect_files": manifest["files"],
               "summary_sha256": sha256_file(directory / "summary.csv") if complete else None}
    path = directory / "run_context.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def validate_extract(market: str) -> None:
    manifest = validate_detect(market)
    directory = workspace_paths(market)["extract"]
    context = read_json(directory / "run_context.json")
    if (context.get("market") != market
            or context.get("store_id") != market_config(market)["store_id"]
            or context.get("detect_files") != manifest["files"]
            or context.get("complete") is not True):
        raise ValueError("RevFlow 结果未完成或与当前 Detect/店铺不一致，请重新运行 RevFlow。")
    if context.get("summary_sha256") != sha256_file(directory / "summary.csv"):
        raise ValueError("summary.csv 已变化，请重新运行 RevFlow 后再进行写入检查。")
