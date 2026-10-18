import io

import pytest
from fastapi import HTTPException
from PIL import Image

from app.core_cloud import images
from app.core_cloud.images import normalize_image


def picture(format="PNG", size=(30, 20), *, exif=None):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format, **({"exif": exif} if exif else {}))
    return output.getvalue()


def test_images_become_bounded_jpeg_and_have_deterministic_retry_hashes():
    raw = picture(size=(2000, 1000))
    value = normalize_image(raw)
    assert value.width == 1600 and value.height == 800
    assert value.sha256 == normalize_image(raw).sha256
    with Image.open(io.BytesIO(value.content)) as output:
        assert output.format == "JPEG" and output.mode == "RGB"


def test_orientation_is_applied_but_private_metadata_is_removed():
    exif = Image.Exif()
    exif[274] = 6
    exif[305] = "private-test-metadata"
    value = normalize_image(picture("JPEG", exif=exif))
    assert (value.width, value.height) == (20, 30)
    with Image.open(io.BytesIO(value.content)) as output:
        assert not output.getexif() and "exif" not in output.info
    assert b"private-test-metadata" not in value.content


@pytest.mark.parametrize(
    "raw", [b"<script>unsafe</script>", b"\x89PNG\r\n\x1a\ntruncated", picture("GIF")]
)
def test_fake_images_truncated_images_and_gif_are_rejected(raw):
    with pytest.raises(HTTPException) as error:
        normalize_image(raw)
    assert error.value.status_code == 415


def test_oversized_input_and_pixel_dimensions_fail_before_expensive_decode(monkeypatch):
    with pytest.raises(HTTPException) as error:
        normalize_image(b"x" * (images.MAX_INPUT_BYTES + 1))
    assert error.value.status_code == 413
    monkeypatch.setattr(images, "MAX_PIXELS", 100)
    with pytest.raises(HTTPException) as error:
        normalize_image(picture(size=(11, 10)))
    assert error.value.detail == "image_dimensions_too_large"


def test_animated_png_is_not_silently_published_as_a_still_image():
    raw = io.BytesIO()
    first = Image.new("RGB", (10, 10), "red")
    first.save(raw, "PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "blue")])
    with pytest.raises(HTTPException) as error:
        normalize_image(raw.getvalue())
    assert error.value.status_code == 415
