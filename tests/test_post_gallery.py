import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from app.core_cloud.images import normalize_image
from app.core_cloud.media import ImagePublish, MediaService
from tests.test_core_local import post_body
from tests.test_core_media import configured as _configured

configured = _configured


def test_gallery_rejects_empty_over_limit_duplicate_and_ambiguous_inputs():
    ids = [uuid4() for _ in range(6)]
    for invalid in (
        {},
        {"image_ids": []},
        {"image_ids": ids},
        {"image_ids": [ids[0], ids[0]]},
        {"image_id": ids[0], "image_ids": [ids[1]]},
    ):
        with pytest.raises(ValidationError):
            ImagePublish(value=post_body(), **invalid)
    assert ImagePublish(value=post_body(), image_ids=ids[:5]).image_ids == ids[:5]
    assert ImagePublish(value=post_body(), image_id=ids[0]).image_id == ids[0]


def test_gallery_service_preserves_selection_order_and_returns_all_dimensions(configured):
    store, owner = configured
    ids = [uuid4(), uuid4()]
    photos = [
        {"id": str(ids[0]), "width": 800, "height": 1200},
        {"id": str(ids[1]), "width": 1200, "height": 800},
    ]
    store._request.return_value = {
        **post_body(),
        "id": str(uuid4()),
        "author_id": str(owner),
        "author_name": "fixture",
        "created_at": datetime.now(UTC).isoformat(),
        "joined": 1,
        "status": "open",
        "my_status": "host",
        "images": photos,
        "image_id": str(ids[0]),
    }
    result = MediaService(store, owner).publish(
        ImagePublish(value=post_body(), image_ids=ids), "gallery-same-request-key"
    )
    request = store._request.call_args.kwargs["body"]
    assert request["action"] == "publish_many"
    assert request["args"]["image_ids"] == [str(i) for i in ids]
    assert [p.id for p in result.images] == ids
    assert [(p.width, p.height) for p in result.images] == [(800, 1200), (1200, 800)]


@pytest.mark.parametrize("size", [(600, 1200), (1200, 600), (2000, 1000), (1000, 2000)])
def test_normalization_keeps_portrait_landscape_ratio_and_all_corners(size):
    source = Image.new("RGB", size, "white")
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    boxes = [
        (0, 0, 100, 100),
        (size[0] - 100, 0, size[0], 100),
        (0, size[1] - 100, 100, size[1]),
        (size[0] - 100, size[1] - 100, *size),
    ]
    for color, box in zip(colors, boxes, strict=True):
        source.paste(color, box)
    raw = io.BytesIO()
    source.save(raw, "PNG")
    result = normalize_image(raw.getvalue())
    assert result.width / result.height == size[0] / size[1]
    with Image.open(io.BytesIO(result.content)) as output:
        positions = [
            (4, 4),
            (output.width - 5, 4),
            (4, output.height - 5),
            (output.width - 5, output.height - 5),
        ]
        for color, point in zip(colors, positions, strict=True):
            assert max(abs(a - b) for a, b in zip(color, output.getpixel(point), strict=True)) < 25
