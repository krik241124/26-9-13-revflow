from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime
import mimetypes
import hashlib
import requests
import secrets


class ImageUploadError(RuntimeError):
    pass


def _mime_type(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0]
    if mime:
        return mime
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


def _safe_piece(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "file"


def make_remote_name(
    sku: str,
    index: int,
    path: Path,
    run_tag: str,
    attempt: int,
) -> str:
    """
    Give every uploaded material a unique human-readable name.
    Example:
      83B-934V00BG__01__01_main1__a1b2c3d4.jpg
    """
    sku_part = _safe_piece(sku)
    stem = _safe_piece(path.stem)
    # Stable short hash: same local file -> same suffix, but SKU/index also differentiate.
    h = hashlib.sha1(
        f"{sku}|{index}|{path.name}|{path.stat().st_size}".encode("utf-8")
    ).hexdigest()[:8]
    return (
        f"{sku_part}__{index+1:02d}"
        f"__{h}__{run_tag}_{attempt + 1}"
        f"{path.suffix.lower()}"
    )


def _put_binary(path: Path, presigned_url: str, timeout: int) -> None:
    data = path.read_bytes()
    r = requests.put(
        presigned_url,
        data=data,
        headers={
            "Content-Type": _mime_type(path),
            "X-Amz-tagging": "temp=1",
        },
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise ImageUploadError(
            f"OSS PUT {path.name}: HTTP {r.status_code}: {r.text[:300]}"
        )


def _object_url(presigned_url: str) -> str:
    return presigned_url.split("?", 1)[0]


def _relative_object_path(object_url: str) -> str:
    return urlsplit(object_url).path.lstrip("/")


def upload_images(
    client,
    image_paths: list[Path],
    sku: str,
    concurrency: int = 3,
    timeout: int = 120,
    retries: int = 2,
) -> list[dict]:
    """
    1) ArkSwift presigns (use unique remote names)
    2) 3-concurrent PUT to OSS
    3) one mutiupload registration for all successful files
    4) return save-draft imgs[] in original image order
    """
    run_tag = secrets.token_hex(3)
    indexed = list(enumerate(image_paths))
    pending = indexed[:]
    uploaded: dict[int, dict] = {}
    last_errors: dict[int, str] = {}

    for attempt in range(retries + 1):
        if not pending:
            break

        presigned_jobs: list[tuple[int, Path, str, str]] = []
        for idx, path in pending:
            remote_name = make_remote_name(
                sku,
                idx,
                path,
                run_tag,
                attempt,
            )
            try:
                url = client.presign(remote_name, path.stat().st_size, pid="0")
                presigned_jobs.append((idx, path, remote_name, url))
            except Exception as exc:
                last_errors[idx] = f"presign({remote_name}): {exc}"

        next_pending: list[tuple[int, Path]] = []

        with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as pool:
            futures = {
                pool.submit(_put_binary, path, url, timeout): (idx, path, remote_name, url)
                for idx, path, remote_name, url in presigned_jobs
            }

            for future in as_completed(futures):
                idx, path, remote_name, url = futures[future]
                try:
                    future.result()
                    obj_url = _object_url(url)
                    uploaded[idx] = {
                        "fileName": remote_name,
                        "fileType": "1",
                        "fileSize": int(path.stat().st_size),
                        "s3url": obj_url,
                        "fileSubType": path.suffix.lower(),
                    }
                    print(f"    [UPLOAD OK] {path.name} -> {remote_name}")
                except Exception as exc:
                    last_errors[idx] = f"PUT({remote_name}): {exc}"
                    next_pending.append((idx, path))

        successfully_presigned = {idx for idx, _, _, _ in presigned_jobs}
        for idx, path in pending:
            if idx not in uploaded and idx not in successfully_presigned:
                next_pending.append((idx, path))

        seen = set()
        deduped = []
        for item in sorted(next_pending, key=lambda x: x[0]):
            if item[0] not in seen:
                deduped.append(item)
                seen.add(item[0])
        pending = deduped

        if pending and attempt < retries:
            print(f"    [UPLOAD RETRY] pending={len(pending)} attempt={attempt + 2}")

    if pending:
        details = "; ".join(
            f"{path.name}: {last_errors.get(idx, 'unknown error')}"
            for idx, path in pending
        )
        raise ImageUploadError(f"图片上传失败: {details}")

    ordered_materials = [uploaded[i] for i in range(len(image_paths))]
    client.register_materials(ordered_materials, pid="0")

    imgs: list[dict] = []
    for idx, material in enumerate(ordered_materials):
        item = {"url": _relative_object_path(material["s3url"])}
        if idx == 0:
            item["pri"] = 1
        imgs.append(item)

    return imgs
