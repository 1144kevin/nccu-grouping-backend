from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from app.core_cloud.store import SupabaseCoreStore
from app.core_cloud.user_blocks import UserBlocks


def test_block_list_is_account_scoped_and_has_no_email_or_private_metadata():
    store = Mock(spec=SupabaseCoreStore)
    store.token = "synthetic-session"
    target = uuid4()
    store._request.return_value = {
        "items": [
            {
                "user_id": str(target),
                "display_name": "合成目標",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]
    }
    result = UserBlocks(store, uuid4()).blocks()
    assert set(result.items[0].model_dump()) == {"user_id", "display_name", "created_at"}
    assert store._request.call_args.kwargs["body"] == {"action": "list", "args": {}}
    assert store._request.call_args.kwargs["token"] == "synthetic-session"


def test_put_and_remove_use_immutable_target_without_accepting_a_forged_owner():
    store = Mock(spec=SupabaseCoreStore)
    store.token = "synthetic-session"
    actor, target = uuid4(), uuid4()
    service = UserBlocks(store, actor)
    for blocked in (True, False):
        store._request.return_value = {"user_id": str(target), "blocked": blocked}
        assert service.block(target, blocked).blocked is blocked
        assert store._request.call_args.kwargs["body"] == {
            "action": "put" if blocked else "remove",
            "args": {"user_id": str(target)},
        }
