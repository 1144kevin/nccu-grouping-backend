from uuid import UUID

from pydantic import AwareDatetime

from app.core_cloud.store import SupabaseCoreStore
from app.stealth.schemas import StrictModel


class BlockView(StrictModel):
    user_id: UUID
    display_name: str
    created_at: AwareDatetime


class BlockList(StrictModel):
    items: list[BlockView]


class BlockResult(StrictModel):
    user_id: UUID
    blocked: bool


class UserBlocks:
    def __init__(self, store: SupabaseCoreStore, actor: UUID, *, transport=None):
        self.store, self.actor, self.transport = store, actor, transport

    def rpc(self, name: str, action: str, args: dict | None = None):
        assert name in ("core_user_blocks", "core_account_deletion")
        return self.store._request(
            "POST",
            "/rest/v1/rpc/" + name,
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )

    def blocks(self):
        return BlockList.model_validate(self.rpc("core_user_blocks", "list"))

    def block(self, user: UUID, blocked: bool):
        return BlockResult.model_validate(
            self.rpc("core_user_blocks", "put" if blocked else "remove", {"user_id": str(user)})
        )
