from __future__ import annotations

from typing import Any
import json
import requests


class ArkSwiftError(RuntimeError):
    pass


class ArkSwiftClient:
    def __init__(self, cfg: dict, auth: dict):
        self.cfg = cfg
        self.base_url = cfg["base_url"].rstrip("/")
        self.store_id = str(cfg["store_id"])
        self.lang = cfg.get("lang", "cn")
        self.session = requests.Session()

        self.session.headers.update({
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN",
            "origin": self.base_url,
            "referer": f"{self.base_url}/seller-console/product/add",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
        })

        raw_cookie = (auth.get("raw_cookie") or "").strip()
        authorization_web = (auth.get("authorization_web") or "").strip()

        if raw_cookie:
            self.session.headers["Cookie"] = raw_cookie
        elif authorization_web:
            self.session.cookies.set(
                "Authorization_web",
                authorization_web,
                domain="www.arkswift.com",
                path="/",
            )
            self.session.cookies.set(
                "loginflag",
                "1",
                domain="www.arkswift.com",
                path="/",
            )
            self.session.cookies.set(
                "aw-country",
                cfg.get("country_code", "FR"),
                domain="www.arkswift.com",
                path="/",
            )
        else:
            raise ArkSwiftError(
                "auth.json 缺少 authorization_web/raw_cookie。"
            )

    def _decode(self, r: requests.Response, action: str) -> dict:
        if r.status_code in (401, 403):
            raise ArkSwiftError(
                f"{action}: HTTP {r.status_code}，登录失效或无店铺权限；请重新登录 ArkSwift，更新 sku_write/auth.json 并核对店铺。"
            )
        try:
            data = r.json()
        except Exception as exc:
            raise ArkSwiftError(
                f"{action}: HTTP {r.status_code}, non-JSON response: {r.text[:500]}"
            ) from exc

        if r.status_code >= 400:
            raise ArkSwiftError(
                f"{action}: HTTP {r.status_code}: {data}"
            )
        if data.get("code") in (401, 403, "401", "403"):
            raise ArkSwiftError(
                f"{action}: 登录失效或无店铺权限；请重新登录 ArkSwift，更新 sku_write/auth.json 并核对店铺。"
            )
        if data.get("code") != 200:
            raise ArkSwiftError(
                f"{action}: ArkSwift code={data.get('code')}: {data}"
            )
        return data

    def _post_wrapped_form(
        self,
        url: str,
        payload: dict,
        *,
        action: str,
        timeout: int,
    ) -> dict:
        """
        ArkSwift seller BFF 的已验证 POST wire format：

          Content-Type: application/x-www-form-urlencoded;charset=UTF-8
          body=<JSON string>

        presigns / mutiupload / save-draft 都统一走这里，避免以后其中
        某个 endpoint 又被误改回 requests.post(..., json=payload)。
        """
        encoded_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        r = self.session.post(
            url,
            data={"body": encoded_json},
            headers={
                "Content-Type":
                    "application/x-www-form-urlencoded;charset=UTF-8",
            },
            timeout=timeout,
        )
        return self._decode(r, action)

    def get_category_tree(self) -> list[dict]:
        url = f"{self.base_url}/rest/v1/seller/goods/category-tree"
        r = self.session.get(
            url,
            params={"_storeId": self.store_id, "_lang": self.lang},
            timeout=30,
        )
        return self._decode(r, "category-tree")["data"]

    def get_attr_list(self) -> list[dict]:
        url = f"{self.base_url}/rest/v1/seller/goods/attr-list"
        r = self.session.get(
            url,
            params={"_storeId": self.store_id, "_lang": self.lang},
            timeout=30,
        )
        return self._decode(r, "attr-list")["data"]

    def presign(self, file_name: str, file_size: int, pid: str = "0") -> str:
        url = f"{self.base_url}/rest/v1/seller/material/file/presigns"
        payload = {
            "body": {
                "fileName": file_name,
                "fileSize": int(file_size),
                "pid": str(pid),
            },
            "_storeId": self.store_id,
            "_lang": self.lang,
        }

        data = self._post_wrapped_form(
            url,
            payload,
            action=f"presigns {file_name}",
            timeout=30,
        )
        presigned_url = (data.get("data") or {}).get("presignedUrl")
        if not presigned_url:
            raise ArkSwiftError(
                f"presigns {file_name}: response missing presignedUrl: {data}"
            )
        return presigned_url

    def register_materials(
        self,
        sub_file_info_list: list[dict],
        pid: str = "0",
    ) -> None:
        url = f"{self.base_url}/rest/v1/seller/material/file/mutiupload"
        payload = {
            "body": {
                "pid": str(pid),
                "subFileInfoList": sub_file_info_list,
            },
            "_storeId": self.store_id,
            "_lang": self.lang,
        }

        data = self._post_wrapped_form(
            url,
            payload,
            action="mutiupload",
            timeout=60,
        )
        fail_list = ((data.get("data") or {}).get("failList") or [])
        if fail_list:
            raise ArkSwiftError(f"mutiupload partial failure: {fail_list}")

    def save_draft(self, body: dict) -> str:
        url = f"{self.base_url}/rest/v1/seller/goods/apply-info/save-draft"
        payload = {
            "body": body,
            "_storeId": self.store_id,
            "_lang": self.lang,
        }

        data = self._post_wrapped_form(
            url,
            payload,
            action="save-draft",
            timeout=90,
        )
        sku_id = data.get("data")
        if not sku_id:
            raise ArkSwiftError(f"save-draft missing skuId: {data}")
        return str(sku_id)
