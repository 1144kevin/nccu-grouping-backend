"""Local Web Push delivery. No account passwords, refresh tokens or admin keys."""

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from pydantic import Field, field_validator
from pywebpush import WebPushException, webpush

from app.core_cloud.store import SupabaseCoreStore
from app.core_local.schemas import Input

ROOT = Path(__file__).resolve().parents[2]
PUSH_HOSTS = {"web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com"}


class PushKeys(Input):
    p256dh: str = Field(min_length=80, max_length=100, repr=False)
    auth: str = Field(min_length=20, max_length=30, repr=False)

    @field_validator("p256dh", "auth")
    @classmethod
    def valid_key(cls, value, info):
        try:
            data = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        except ValueError:
            raise ValueError("invalid_push_key") from None
        if (info.field_name == "p256dh" and (len(data) != 65 or data[0] != 4)) or (
            info.field_name == "auth" and len(data) != 16
        ):
            raise ValueError("invalid_push_key")
        if info.field_name == "p256dh":
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), data)
        return value


class PushSubscription(Input):
    endpoint: str = Field(max_length=3000, repr=False)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in PUSH_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            raise ValueError("invalid_push_endpoint")
        return value


class PushDevice(Input):
    device_id: UUID
    subscription: PushSubscription


class WebPushDelivery:
    def __init__(self, settings, root: Path | None = None):
        self.settings = settings
        self.root = root or ROOT / ".local/web-push"
        self.root.mkdir(parents=True, exist_ok=True)
        self.key_path = self.root / "private.pem"
        self.path = self.root / "devices.sqlite3"
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "create table if not exists devices(id text primary key,owner text,"
                "subscription text,proof text,cursor text,expires real)"
            )

    def public_key(self):
        with self.lock:
            if not self.key_path.exists():
                key = ec.generate_private_key(ec.SECP256R1())
                data = key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                with self.key_path.open("xb") as file:
                    file.write(data)
            key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        public = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return base64.urlsafe_b64encode(public).rstrip(b"=").decode()

    def _snapshot(self, store, device_id, proof):
        return store._request(
            "POST", "/rest/v1/rpc/core_push_snapshot", body={"device_id": device_id, "proof": proof}
        )

    def subscribe(self, store, actor, value: PushDevice):
        self.public_key()  # Ensure the stable signing key exists; never return its private part.
        device_id = str(value.device_id)
        proof = secrets.token_urlsafe(32)
        store._request(
            "POST",
            "/rest/v1/rpc/core_notifications",
            body={
                "action": "device",
                "args": {"id": device_id, "proof_hash": hashlib.sha256(proof.encode()).hexdigest()},
            },
            token=store.token,
        )
        baseline = self._snapshot(store, device_id, proof)
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("delete from devices where expires<?", (time.time(),))
            if (
                db.execute("select count(*) from devices").fetchone()[0] >= 32
                and not db.execute("select 1 from devices where id=?", (device_id,)).fetchone()
            ):
                raise HTTPException(429, "too_many_devices")
            db.execute(
                "insert or replace into devices values(?,?,?,?,?,?)",
                (
                    device_id,
                    str(actor),
                    value.subscription.model_dump_json(),
                    proof,
                    baseline.get("created_at") or "",
                    time.time() + 30 * 86400,
                ),
            )
        return {"enabled": True}

    def unsubscribe(self, store, actor, device_id):
        store._request(
            "POST",
            "/rest/v1/rpc/core_notifications",
            body={"action": "device", "args": {"id": str(device_id), "remove": True}},
            token=store.token,
        )
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("delete from devices where id=? and owner=?", (str(device_id), str(actor)))
        return {"enabled": False}

    def _deliver(self, subscription, payload):
        session = requests.Session()
        session.trust_env = False
        session.max_redirects = 0
        try:
            response = webpush(
                subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=str(self.key_path),
                vapid_claims={
                    "sub": self.settings.core_google_public_web_callback.removesuffix(
                        "/auth/callback"
                    )
                },
                timeout=8,
                ttl=300,
                headers={"Urgency": "high"},
                requests_session=session,
            )
            return response.status_code
        except WebPushException as error:
            return error.response.status_code if error.response is not None else 503
        except requests.RequestException:
            return 503
        finally:
            session.close()

    def tick(self):
        # Nothing is polled until a member explicitly enables phone notifications.
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("delete from devices where expires<?", (time.time(),))
            rows = db.execute("select id,subscription,proof,cursor from devices").fetchall()
        if not rows:
            return
        store = SupabaseCoreStore(self.settings)
        for device_id, subscription, proof, cursor in rows:
            if self.stopped.is_set():
                break
            try:
                snapshot = self._snapshot(store, device_id, proof)
                newest = snapshot.get("created_at") or ""
                if not snapshot.get("count") or not newest or newest <= cursor:
                    continue
                subscription = PushSubscription.model_validate_json(subscription).model_dump()
                titles = {
                    "accepted": "你的拼單申請已獲接受",
                    "completed": "拼單已完成，信譽積分已更新",
                    "join_request": "有人申請加入你的拼單",
                    "message": "你有新的聊天室訊息",
                }
                status = self._deliver(
                    subscription,
                    {
                        "title": "政缺一咖",
                        "body": titles.get(snapshot.get("kind"), "你有新的拼單通知"),
                        "url": snapshot.get("url") or "/notifications",
                        "badge": snapshot["count"],
                        "tag": device_id,
                    },
                )
                with self.lock, sqlite3.connect(self.path) as db:
                    if status in (404, 410):
                        db.execute("delete from devices where id=?", (device_id,))
                    elif 200 <= status < 300:
                        db.execute("update devices set cursor=? where id=?", (newest, device_id))
            except HTTPException as error:
                if error.status_code == 404:
                    with self.lock, sqlite3.connect(self.path) as db:
                        db.execute("delete from devices where id=?", (device_id,))
            except (ValueError, OSError):
                pass  # No raw endpoint, key or notice payload in logs.

    def run(self):
        while not self.stopped.wait(15):
            self.tick()
