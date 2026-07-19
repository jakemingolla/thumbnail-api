from io import BytesIO

import pytest
from PIL import Image

from thumbnail_api.images import ImageProcessingError, resize_to_jpeg


def _png_bytes(
    width: int,
    height: int,
    *,
    mode: str = "RGB",
    color: object = (0, 128, 255),
) -> bytes:
    image = Image.new(mode, (width, height), color)  # type: ignore[arg-type]
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_resize_to_jpeg_fits_within_max_size() -> None:
    source = _png_bytes(400, 200)

    result = resize_to_jpeg(source, max_size=100)

    with Image.open(BytesIO(result)) as out:
        assert out.format == "JPEG"
        assert out.size == (100, 50)
        assert max(out.size) == 100


def test_resize_to_jpeg_does_not_upscale() -> None:
    source = _png_bytes(40, 20)

    result = resize_to_jpeg(source, max_size=100)

    with Image.open(BytesIO(result)) as out:
        assert out.size == (40, 20)


def test_resize_to_jpeg_portrait_preserves_aspect() -> None:
    source = _png_bytes(100, 400)

    result = resize_to_jpeg(source, max_size=100)

    with Image.open(BytesIO(result)) as out:
        assert out.size == (25, 100)


def test_resize_to_jpeg_flattens_rgba() -> None:
    source = _png_bytes(80, 80, mode="RGBA", color=(255, 0, 0, 128))

    result = resize_to_jpeg(source, max_size=40)

    with Image.open(BytesIO(result)) as out:
        assert out.mode == "RGB"
        assert out.format == "JPEG"
        assert out.size == (40, 40)


@pytest.mark.parametrize(
    "bad_input",
    [
        b"",
        b"not-an-image",
        b"\x00\x01\x02\x03",
    ],
)
def test_resize_to_jpeg_rejects_non_image(bad_input: bytes) -> None:
    with pytest.raises(ImageProcessingError):
        resize_to_jpeg(bad_input, max_size=128)


@pytest.mark.parametrize("max_size", [0, -1, -128])
def test_resize_to_jpeg_rejects_invalid_max_size(max_size: int) -> None:
    source = _png_bytes(16, 16)

    with pytest.raises(ImageProcessingError, match="max_size"):
        resize_to_jpeg(source, max_size=max_size)
