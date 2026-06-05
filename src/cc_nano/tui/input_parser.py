"""输入解析 — 从用户输入中提取 @image 图片引用。"""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMG_PATH_RE = re.compile(r"@(\S+?\.(?:png|jpg|jpeg|gif|webp))\b", re.IGNORECASE)


def parse_input(text: str) -> str | list:
    """解析用户输入，将 @路径 形式的图片引用提取为内容块。

    如果没有图片则返回纯字符串，如果找到图片则返回包含内容块的列表。
    """
    matches = list(_IMG_PATH_RE.finditer(text))
    if not matches:
        return text

    image_blocks = []
    for m in matches:
        fpath = Path(m.group(1))
        if fpath.suffix.lower() not in _IMAGE_EXTS:
            continue
        if not fpath.exists():
            continue
        file_size = fpath.stat().st_size
        if file_size > 10 * 1024 * 1024:  # 超过 10MB 跳过
            continue
        media_type = mimetypes.guess_type(str(fpath))[0] or "image/png"
        data = base64.standard_b64encode(fpath.read_bytes()).decode("ascii")
        image_blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            }
        )

    if not image_blocks:
        return text

    # 从文本中移除 @路径 标记
    cleaned = _IMG_PATH_RE.sub("", text).strip()
    content: list[dict] = list(image_blocks)
    if cleaned:
        content.append({"type": "text", "text": cleaned})
    return content
