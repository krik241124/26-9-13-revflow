from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from summary_reader import image_paths_for_row


MAX_SIMPLE_UPLOAD_BYTES = 10 * 1024 * 1024
TARGET_IMAGES = 12
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif"}


class ImagePolicyError(ValueError):
    pass


@dataclass
class ImageSelection:
    selected: list[Path]
    skipped: list[str]
    source_count: int

    @property
    def used_count(self) -> int:
        return len(self.selected)

    @property
    def degraded(self) -> bool:
        return bool(self.skipped) or self.used_count < TARGET_IMAGES


def select_images_for_upload(
    row: dict,
    output_root: Path,
    *,
    min_images: int = 5,
    target_images: int = TARGET_IMAGES,
    max_bytes: int = MAX_SIMPLE_UPLOAD_BYTES,
) -> ImageSelection:
    """
    ArkSwift 图片容错策略：

    1. 优先按 summary.csv 的 image_files 顺序使用；
    2. 缺失 / >10MB / 不支持格式的图片直接跳过；
    3. 若不足 target_images，则从同一 SKU images 目录继续找可用图片补位；
    4. 最多使用 target_images 张；
    5. 最终 >= min_images 即允许继续，低于 min_images 才报错。

    这样不需要实现 ArkSwift multipart upload，也不会因为一两张异常图片
    让整个 SKU 失败。
    """
    listed_paths = image_paths_for_row(row, output_root)
    source_count = len(listed_paths)

    selected: list[Path] = []
    skipped: list[str] = []
    seen: set[str] = set()

    def key_for(path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except Exception:
            return str(path).casefold()

    def consider(path: Path, *, fallback: bool = False) -> None:
        key = key_for(path)
        if key in seen:
            return
        seen.add(key)

        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            skipped.append(f"{path.name}:unsupported_format")
            return

        if not path.is_file():
            skipped.append(f"{path.name}:missing")
            return

        size = path.stat().st_size
        if size > max_bytes:
            skipped.append(
                f"{path.name}:too_large_{size / 1024 / 1024:.1f}MB"
            )
            return

        selected.append(path)

    # First preserve RevFlow's preferred image order.
    for path in listed_paths:
        if len(selected) >= target_images:
            break
        consider(path)

    # If bad/missing images created gaps, look for extra local images in the same folder.
    if len(selected) < target_images and listed_paths:
        image_dir = listed_paths[0].parent
        if image_dir.is_dir():
            for path in sorted(
                image_dir.iterdir(),
                key=lambda p: p.name.casefold(),
            ):
                if len(selected) >= target_images:
                    break
                consider(path, fallback=True)

    selected = selected[:target_images]

    if len(selected) < int(min_images):
        detail = "; ".join(skipped[:12])
        raise ImagePolicyError(
            f"可用图片不足: {len(selected)} < {min_images}"
            + (f"；跳过={detail}" if detail else "")
        )

    return ImageSelection(
        selected=selected,
        skipped=skipped,
        source_count=source_count,
    )
