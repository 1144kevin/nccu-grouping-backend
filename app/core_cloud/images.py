"""Bounded in-memory image normalization; never reads filesystem paths or URLs."""

import hashlib
import io
import warnings
from dataclasses import dataclass
from threading import BoundedSemaphore

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_EDGE = 1600
IMAGE_SLOTS = BoundedSemaphore(2)


@dataclass(frozen=True)
class NormalizedImage:
    content: bytes
    width: int
    height: int
    sha256: str


def normalize_image(content: bytes) -> NormalizedImage:
    if not content or len(content) > MAX_INPUT_BYTES:
        raise HTTPException(413, "image_too_large")
    if not IMAGE_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "image_processing_busy")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content), formats=["JPEG", "PNG"]) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise HTTPException(413, "image_dimensions_too_large")
                if getattr(source, "n_frames", 1) != 1:
                    raise HTTPException(415, "image_format_not_supported")
                source.verify()
            with Image.open(io.BytesIO(content), formats=["JPEG", "PNG"]) as source:
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
                # A fresh canvas drops EXIF, GPS, comments, ICC and original filenames.
                canvas = Image.new("RGB", oriented.size, "white")
                if oriented.mode in ("RGBA", "LA") or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    canvas.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    canvas.paste(oriented.convert("RGB"))
                output = io.BytesIO()
                canvas.save(output, "JPEG", quality=80, optimize=True)
                normalized = output.getvalue()
                width, height = canvas.size
        if len(normalized) > MAX_OUTPUT_BYTES:
            raise HTTPException(413, "image_too_large")
        return NormalizedImage(normalized, width, height, hashlib.sha256(normalized).hexdigest())
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "image_dimensions_too_large") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise HTTPException(415, "image_format_not_supported") from None
    finally:
        IMAGE_SLOTS.release()
