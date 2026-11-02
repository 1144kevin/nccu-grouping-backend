from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from app.core_cloud.web_push import PushDevice
from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import Input

router = APIRouter(prefix="/push", tags=["web-push"])


def manager(request):
    result = getattr(request.app.state, "web_push", None)
    if result is None:
        raise HTTPException(503, "web_push_unavailable")
    return result


class DeviceId(Input):
    device_id: UUID


@router.get("/key")
def key(request: Request, response: Response, _: UserDep):
    response.headers["Cache-Control"] = "no-store"
    return {"public_key": manager(request).public_key()}


@router.post("/subscribe")
def subscribe(value: PushDevice, request: Request, store: StoreDep, user: UserDep):
    return manager(request).subscribe(store, user.id, value)


@router.post("/unsubscribe")
def unsubscribe(value: DeviceId, request: Request, store: StoreDep, user: UserDep):
    return manager(request).unsubscribe(store, user.id, value.device_id)


@router.post("/test")
def test_notification(value: DeviceId, request: Request, user: UserDep):
    import json
    import sqlite3

    delivery = manager(request)
    with delivery.lock, sqlite3.connect(delivery.path) as db:
        row = db.execute(
            "select subscription from devices where id=? and owner=?",
            (str(value.device_id), str(user.id)),
        ).fetchone()
    if not row:
        raise HTTPException(404, "device_not_found")
    status = delivery._deliver(
        json.loads(row[0]),
        {"title": "政缺一咖", "body": "手機推播已連線成功", "url": "/notifications", "badge": 0},
    )
    if not 200 <= status < 300:
        raise HTTPException(503, "web_push_delivery_failed")
    return {"sent": True}
