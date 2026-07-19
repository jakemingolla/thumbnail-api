"""Shared image resize helper for thumbnail output.

Policy (normative detail in ``docs/specification/s3-keys.md``):

- Fit within a ``max_size`` x ``max_size`` box (preserve aspect ratio; no upscale).
- Encode as JPEG (``image/jpeg``), quality 85.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

# Must match docs/specification/s3-keys.md thumbnail encoding.
JPEG_QUALITY = 85


class ImageProcessingError(Exception):
    """Input bytes are not a usable image, or resize/encode failed permanently.

    Workers must treat this as a permanent size failure (mark size ``failed``).
    """


def resize_to_jpeg(image_bytes: bytes, max_size: int) -> bytes:
    """Decode ``image_bytes``, fit to ``max_size``, and return JPEG bytes.

    Args:
        image_bytes: Raw input image (JPEG, PNG, or WebP expected).
        max_size: Maximum pixel length of the longest edge after resize.
            Must be a positive integer. Images already within the box are
            not upscaled.

    Returns:
        JPEG-encoded bytes suitable for ``Content-Type: image/jpeg``.

    Raises:
        ImageProcessingError: Empty/corrupt/non-image input, invalid
            ``max_size``, or other permanent processing failure.

    """
    if max_size <= 0:
        msg = "max_size must be a positive integer"
        raise ImageProcessingError(msg)
    if not image_bytes:
        msg = "image bytes are empty"
        raise ImageProcessingError(msg)

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            rgb = _to_rgb(ImageOps.exif_transpose(image))
            rgb.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            output = BytesIO()
            rgb.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return output.getvalue()
    except ImageProcessingError:
        raise
    except UnidentifiedImageError as exc:
        msg = "input is not a recognized image"
        raise ImageProcessingError(msg) from exc
    except OSError as exc:
        msg = f"failed to process image: {exc}"
        raise ImageProcessingError(msg) from exc


def _to_rgb(image: Image.Image) -> Image.Image:
    """Convert to RGB so JPEG encode succeeds (flatten alpha onto white)."""
    if image.mode == "RGB":
        return image

    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[3])
        return background

    return image.convert("RGB")
