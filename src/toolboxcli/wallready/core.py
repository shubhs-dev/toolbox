"""What wallready decides about a single image: target geometry, classification, magick args.

Pure arithmetic and policy — every filesystem and subprocess call lives in `cli.py`, so the
rules below (what counts as too small, how a crop is derived) can be read on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from toolboxcli._common.console import die

DEFAULT_TARGET = (3840, 2160)  # 4K UHD, 16:9

_SIZE_RE = re.compile(r"^\s*(\d+)\s*[x*×]\s*(\d+)\s*$", re.IGNORECASE)

# Named targets, so -s doesn't have to be typed out as WxH for the common ones.
SIZE_PRESETS = {
    "8k": (7680, 4320),
    "5k": (5120, 2880),
    "4k": (3840, 2160),
    "2160p": (3840, 2160),
    "1440p": (2560, 1440),
    "1080p": (1920, 1080),
    "ultrawide": (3440, 1440),
}

# EXIF orientations that store the pixels a quarter turn out, so the width and height
# an image reports are the wrong way round once a viewer has rotated it. ImageMagick
# spells these Left*/Right*; the Top*/Bottom* ones are upright or mirrored only.
_SWAPPED_ORIENTATIONS = {"LeftTop", "RightTop", "RightBottom", "LeftBottom"}


class Action(Enum):
    """What should happen to one image."""

    READY = "ready"          # already the target size in the target format — left alone
    CONVERT = "convert"      # already the target size, wrong format — re-encoded only
    RESIZE = "resize"        # downscaled to cover the target, then centre-cropped
    MISMATCH = "mismatch"    # portrait against a landscape target (or the reverse)
    TOO_SMALL = "too_small"  # short of the target on at least one axis


# The actions that move a file aside untouched rather than producing a wallpaper.
SET_ASIDE = (Action.MISMATCH, Action.TOO_SMALL)


def parse_size(spec: str) -> tuple[int, int]:
    """Parse a -s/--size spec (`4k`, `3840x2160`) into (width, height)."""
    key = spec.strip().lower()
    if key in SIZE_PRESETS:
        return SIZE_PRESETS[key]

    m = _SIZE_RE.match(key)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
        if width and height:
            return width, height

    die(
        f"invalid size '{spec}' — use a preset ({', '.join(SIZE_PRESETS)}) "
        "or WIDTHxHEIGHT (e.g. 3840x2160)"
    )


def is_rotated(orientation: str) -> bool:
    """True when the stored pixels are a quarter turn out from how they'll be seen."""
    return orientation.strip() in _SWAPPED_ORIENTATIONS


def oriented_size(width: int, height: int, orientation: str) -> tuple[int, int]:
    """Width and height the way a viewer will see them, swapping the pair for the EXIF
    orientations that store the image rotated. Without this a portrait phone photo
    measures as landscape and gets cropped along the wrong axis."""
    if is_rotated(orientation):
        return height, width
    return width, height


def _shape(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def mismatch_dir(target: tuple[int, int]) -> str:
    """Name of the subfolder for images whose shape can't fill this target — the images
    set aside are the portrait ones for a landscape target, and vice versa."""
    return "portrait" if _shape(*target) == "landscape" else "landscape"


def cover_size(width: int, height: int, target: tuple[int, int]) -> tuple[int, int]:
    """Size the image is scaled to so it just *covers* the target, before the crop.

    This is ImageMagick's `-resize WxH^` arithmetic: the scale factor is the larger of
    the two ratios, so the image ends up at least as big as the target on both axes and
    the excess on one axis is what the centre crop takes off.
    """
    tw, th = target
    scale = max(tw / width, th / height)
    # Round the way ImageMagick does, and never land under the target through rounding.
    return max(tw, round(width * scale)), max(th, round(height * scale))


@dataclass(frozen=True)
class Plan:
    """One image's dimensions (EXIF-corrected) and what wallready will do with it."""

    action: Action
    width: int
    height: int
    target: tuple[int, int]
    rotated: bool = False  # stored a quarter turn out, per its EXIF orientation

    @property
    def scaled(self) -> tuple[int, int]:
        return cover_size(self.width, self.height, self.target)

    @property
    def crop_note(self) -> str:
        """How much the centre crop discards, for the per-file line."""
        sw, sh = self.scaled
        tw, th = self.target
        if sw > tw:
            return f"crops {round(100 * (sw - tw) / sw)}% off the width"
        if sh > th:
            return f"crops {round(100 * (sh - th) / sh)}% off the height"
        return "no crop"

    def describe(self) -> str:
        tw, th = self.target
        if self.action is Action.RESIZE:
            sw, sh = self.scaled
            step = f"{self.width}x{self.height} → {sw}x{sh} → {tw}x{th}"
            return f"{step} ({self.crop_note})"
        if self.action is Action.CONVERT:
            if self.rotated:
                return f"{self.width}x{self.height} once rotated — re-encode to apply it"
            return f"{self.width}x{self.height}, already {tw}x{th} — re-encode only"
        if self.action is Action.READY:
            return f"{self.width}x{self.height} — already a {tw}x{th} JPG"
        if self.action is Action.MISMATCH:
            return f"{self.width}x{self.height} — wrong shape for a {tw}x{th} wallpaper"
        return f"{self.width}x{self.height} — smaller than {tw}x{th}"


def classify(
    width: int,
    height: int,
    target: tuple[int, int],
    is_jpeg: bool,
    rotated: bool = False,
) -> Plan:
    """Decide what to do with an image of this size.

    Shape is checked before size: a portrait photo can't become a landscape wallpaper at
    any resolution, so saying "wrong shape" is more use than "too small". Upscaling is
    never on the table — an image short of the target on either axis is set aside rather
    than stretched, since a stretched wallpaper is worse than no wallpaper.
    """
    tw, th = target

    img_shape = _shape(width, height)
    target_shape = _shape(*target)
    # A square image is croppable to either shape, so it's never a mismatch.
    if "square" not in (img_shape, target_shape) and img_shape != target_shape:
        return Plan(Action.MISMATCH, width, height, target, rotated)

    if width < tw or height < th:
        return Plan(Action.TOO_SMALL, width, height, target, rotated)

    if (width, height) == (tw, th):
        # A rotated file only measures as the target once its EXIF orientation is applied,
        # so leaving it alone would leave the pixels stored sideways for anything that
        # ignores the tag. Re-encoding bakes the rotation in.
        ready = is_jpeg and not rotated
        return Plan(Action.READY if ready else Action.CONVERT, width, height, target, rotated)

    return Plan(Action.RESIZE, width, height, target, rotated)


def convert_args(
    plan: Plan,
    quality: int,
    background: str,
    strip: bool,
) -> list[str]:
    """ImageMagick operators between the input and output paths.

    Order matters: `-auto-orient` first so everything downstream works on the image as it
    will be seen (it also rewrites the EXIF orientation tag, so the result isn't rotated a
    second time by the viewer); `-colorspace sRGB` next so a CMYK or wide-gamut source is
    in display space before anything composites or resamples it; alpha is flattened onto
    `background` because JPEG has no alpha and transparent areas otherwise come out black.
    """
    tw, th = plan.target
    geometry = f"{tw}x{th}"
    args = [
        "-auto-orient",
        "-colorspace", "sRGB",
        "-background", background,
        "-alpha", "remove",
        "-alpha", "off",
        # `^` scales to *cover* the target (the larger of the two ratios) instead of
        # fitting inside it, then -extent takes the centred cut. Both are no-ops on an
        # image already at the target size.
        "-resize", f"{geometry}^",
        "-gravity", "center",
        "-extent", geometry,
        "-quality", str(quality),
    ]
    if strip:
        args.append("-strip")
    return args
