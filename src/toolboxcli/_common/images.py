"""Image file extensions and format-name normalization, shared by the image scripts."""

from __future__ import annotations

IMAGE_EXTS = {
    "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif",
    "webp", "avif", "heic", "heif", "ico", "svg",
}

# Spellings of the same format, so "jpg" matches .jpeg files and vice versa.
EXT_ALIASES = {"jpeg": "jpg", "tif": "tiff", "heif": "heic"}


def normalize_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return EXT_ALIASES.get(ext, ext)
