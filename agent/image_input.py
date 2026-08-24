"""源图片（source-image）的严格解析与描述。

客户端不信任前端上传的字节，因此不依赖 Pillow 等第三方库，而是手写
PNG / JPEG / WebP 三种格式的最小解析器，仅做两件事：

1. 校验结构完整性（magic、chunk CRC、长度、熵流是否可解压）；
2. 提取真实宽高，用于像素上限校验。

`describe()` 返回的 descriptor（含 sha256 与稳定的 content id）会作为
「source-image」角色写入项目，后续 canonical 与 GLB 校验都沿用同一套
「内容寻址 + 完整性校验」思路（见 artifacts.py）。
"""

from __future__ import annotations

import hashlib
import struct
import uuid
import zlib
from pathlib import Path

from agent.constants import SOURCE_MAX_BYTES, SOURCE_MAX_PIXELS, SOURCE_MIME_TYPES

# 各 MIME 允许的文件扩展名，用于「扩展名 ↔ 实际内容」一致性校验。
_SUFFIXES = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/webp": {".webp"},
}


def _png(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not PNG")
    offset = 8
    width = height = None
    idat = bytearray()
    saw_iend = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("PNG 已截断")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = int.from_bytes(data[offset + 8 + length : end], "big")
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("PNG chunk 校验失败")
        if kind == b"IHDR":
            if length != 13:
                raise ValueError("PNG IHDR 无效")
            width, height = struct.unpack(">II", payload[:8])
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            saw_iend = True
            offset = end
            break
        offset = end
    if not width or not height or not idat or not saw_iend or offset != len(data):
        raise ValueError("PNG 结构不完整")
    try:
        zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError("PNG 图像数据损坏") from exc
    return width, height


def _jpeg(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("JPEG 结构不完整")
    offset = 2
    dimensions: tuple[int, int] | None = None
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset < len(data) - 2:
        if data[offset] != 0xFF:
            raise ValueError("JPEG marker 无效")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xDA:  # Start of Scan; entropy stream runs until EOI.
            break
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if offset + 2 > len(data):
            raise ValueError("JPEG segment 已截断")
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            raise ValueError("JPEG segment 长度无效")
        if marker in sof_markers:
            if length < 7:
                raise ValueError("JPEG SOF 无效")
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            dimensions = (width, height)
        offset += length
    if not dimensions or not all(dimensions):
        raise ValueError("JPEG 缺少有效尺寸")
    return dimensions


def _webp(data: bytes) -> tuple[int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("WebP 结构无效")
    if int.from_bytes(data[4:8], "little") + 8 != len(data):
        raise ValueError("WebP RIFF 长度无效")
    offset = 12
    dimensions: tuple[int, int] | None = None
    while offset + 8 <= len(data):
        kind = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        start = offset + 8
        end = start + size
        if end > len(data):
            raise ValueError("WebP chunk 已截断")
        payload = data[start:end]
        if kind == b"VP8X" and len(payload) >= 10:
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
            dimensions = (width, height)
        elif kind == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            packed = int.from_bytes(payload[1:5], "little")
            dimensions = ((packed & 0x3FFF) + 1, ((packed >> 14) & 0x3FFF) + 1)
        elif kind == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            dimensions = (
                int.from_bytes(payload[6:8], "little") & 0x3FFF,
                int.from_bytes(payload[8:10], "little") & 0x3FFF,
            )
        offset = end + (size & 1)
    if offset != len(data) or not dimensions or not all(dimensions):
        raise ValueError("WebP 缺少有效尺寸")
    return dimensions


def describe(data: bytes, filename: str, limits: dict | None = None) -> dict:
    """解析源图片并返回 source-image descriptor。

    检测顺序即 SOURCE_MIME_TYPES 的顺序：逐个尝试解析器，取第一个成功者
    作为真实 MIME（不信任扩展名），再校验该 MIME 是否在 capability 允许范围内。
    """
    limits = limits or {
        "mime": list(SOURCE_MIME_TYPES),
        "max_bytes": SOURCE_MAX_BYTES,
        "max_pixels": SOURCE_MAX_PIXELS,
    }
    max_bytes = int(limits["max_bytes"])
    max_pixels = int(limits["max_pixels"])
    allowed_mime = set(limits["mime"])
    if not data:
        raise ValueError("图片为空")
    if len(data) > max_bytes:
        raise ValueError(f"图片不能超过 {max_bytes // (1024 * 1024)} MiB")

    parsers = (
        ("image/png", _png),
        ("image/jpeg", _jpeg),
        ("image/webp", _webp),
    )
    detected = None
    dimensions = None
    errors: list[ValueError] = []
    for mime, parser in parsers:
        try:
            dimensions = parser(data)
            detected = mime
            break
        except ValueError as exc:
            errors.append(exc)
    if detected is None or dimensions is None:
        raise ValueError("图片内容不是有效的 PNG、JPEG 或 WebP") from errors[-1]
    if detected not in allowed_mime:
        raise ValueError(f"当前 capability 不支持 {detected}")

    suffix = Path(filename or "source").suffix.lower()
    if suffix not in _SUFFIXES[detected]:
        raise ValueError("图片扩展名与实际内容 MIME 不一致")
    width, height = dimensions
    pixels = width * height
    if pixels > max_pixels:
        raise ValueError(f"图片像素不能超过 {max_pixels}")

    digest = hashlib.sha256(data).hexdigest()
    identity = uuid.uuid5(uuid.NAMESPACE_URL, f"modal-3d:source:{digest}").hex
    return {
        "id": f"src_{identity}",
        "role": "source-image",
        "mime": detected,
        "bytes": len(data),
        "sha256": digest,
        "width": width,
        "height": height,
        "pixels": pixels,
        "alpha": {"required": False},
    }
