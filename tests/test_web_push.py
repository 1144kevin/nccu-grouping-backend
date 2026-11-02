"""Pure local fixtures: no real accounts, credentials or push-service requests."""

import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from app.core_cloud import web_push


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def subscription(endpoint="https://web.push.apple.com/synthetic-device"):
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return {
        "endpoint": endpoint,
        "keys": {"p256dh": encoded(public), "auth": encoded(b"0123456789abcdef")},
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://web.push.apple.com/test",
        "https://127.0.0.1/test",
        "https://web.push.apple.com.attacker.test/test",
        "https://user:password@web.push.apple.com/test",
        "https://web.push.apple.com:8443/test",
    ],
)
def test_push_rejects_untrusted_endpoints(endpoint):
    with pytest.raises(ValidationError):
        web_push.PushSubscription.model_validate(subscription(endpoint))


def test_public_key_is_stable_and_never_contains_the_private_key(tmp_path):
    delivery = web_push.WebPushDelivery(SimpleNamespace(), tmp_path)
    public = delivery.public_key()
    assert len(public) == 87 and public[0] == "B"
    assert web_push.WebPushDelivery(SimpleNamespace(), tmp_path).public_key() == public
    assert "PRIVATE" not in public


def test_delivery_uses_only_scoped_proof_and_sends_each_new_notice_once(tmp_path, monkeypatch):
    snapshots = [
        {"count": 0},
        {
            "count": 1,
            "created_at": "2026-11-02T05:00:00Z",
            "kind": "accepted",
            "url": "/order/" + str(uuid4()),
        },
    ]
    calls = []
    proof_hash = None

    class Store:
        token = "synthetic-session"

        def _request(self, method, path, *, body, token=None):
            nonlocal proof_hash
            calls.append((path, body, token))
            if path.endswith("core_notifications"):
                proof_hash = body["args"].get("proof_hash", proof_hash)
                return {"ok": True}
            assert (
                token is None and hashlib.sha256(body["proof"].encode()).hexdigest() == proof_hash
            )
            return snapshots[-1] if len(snapshots) == 1 else snapshots.pop(0)

    store = Store()
    monkeypatch.setattr(web_push, "SupabaseCoreStore", lambda _: store)
    delivery = web_push.WebPushDelivery(SimpleNamespace(), tmp_path)
    value = web_push.PushDevice(device_id=uuid4(), subscription=subscription())
    delivery.subscribe(store, "synthetic-owner", value)
    delivery._deliver = Mock(return_value=201)
    delivery.tick()
    delivery.tick()
    assert delivery._deliver.call_count == 1
    payload = delivery._deliver.call_args.args[1]
    assert payload["body"] == "你的拼單申請已獲接受"
    assert "synthetic-owner" not in str(payload)
    assert all(token is None for path, _, token in calls if path.endswith("core_push_snapshot"))
    delivery.unsubscribe(store, "synthetic-owner", value.device_id)
    delivery.tick()
    assert delivery._deliver.call_count == 1
