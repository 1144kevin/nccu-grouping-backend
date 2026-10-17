"""Dedicated local SQLite adapter, never the configured production DATABASE_URL.

All state transitions use BEGIN IMMEDIATE, so concurrent invitations and formations
are serialized across connections/processes. SQL identifiers are fixed internally.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.stealth.errors import StealthError
from app.stealth.schemas import Agent, Formation, Proposal, Signal

MODELS = {"agents": Agent, "signals": Signal, "proposals": Proposal, "formations": Formation}


def decode_record(table: str, value: str) -> Any:
    try:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("invalid record shape")
        if table == "agents":
            # Old synthetic records persisted the request's optimistic-lock version.
            # It is not part of agent state and must never be returned as a current version.
            data.pop("expected_version", None)
            data.setdefault("policy_version", data.get("version", 1))
        elif table == "proposals":
            for member in data.get("members", []):
                if isinstance(member, dict) and member.get("decision") == "accepted":
                    member.setdefault("accepted_terms_revision", 0)
        return MODELS[table].model_validate(data)
    except (ValueError, ValidationError, TypeError):
        raise StealthError(503, "local_storage_record_invalid") from None


class LocalUnitOfWork:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get(self, table: str, key: UUID) -> Any:
        if table not in MODELS:
            raise ValueError("unsupported table")
        row = self.connection.execute(
            f"SELECT data FROM {table} WHERE id=?", (str(key),)
        ).fetchone()
        return decode_record(table, row[0]) if row else None

    def all(self, table: str) -> list[Any]:
        if table not in MODELS:
            raise ValueError("unsupported table")
        return [
            decode_record(table, row[0])
            for row in self.connection.execute(f"SELECT data FROM {table} ORDER BY id")
        ]

    def put(self, table: str, model: BaseModel) -> None:
        if table not in MODELS:
            raise ValueError("unsupported table")
        if not isinstance(model, MODELS[table]):
            raise TypeError("record does not match storage table")
        key = model.user_id if isinstance(model, Agent) else model.id
        self.connection.execute(
            f"INSERT INTO {table}(id,data) VALUES (?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (str(key), model.model_dump_json()),
        )

    def blocked_pairs(self) -> set[frozenset[UUID]]:
        return {
            frozenset((UUID(a), UUID(b)))
            for a, b in self.connection.execute("SELECT blocker,blocked FROM local_blocks")
        }

    def event(self, actor: UUID, kind: str, at: datetime, proposal: UUID | None = None) -> None:
        self.connection.execute(
            "INSERT INTO events(actor,kind,at,proposal) VALUES (?,?,?,?)",
            (str(actor), kind, at.isoformat(), str(proposal) if proposal else None),
        )

    def last_event(self, actor: UUID, kind: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT at FROM events WHERE actor=? AND kind=? ORDER BY sequence DESC LIMIT 1",
            (str(actor), kind),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def authenticate(self, token: str) -> UUID | None:
        if not 32 <= len(token) <= 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.connection.execute(
            "SELECT id FROM local_users WHERE token_hash=?",
            (digest,),
        ).fetchone()
        return UUID(row[0]) if row else None

    def add_local_user(self, user_id: UUID, token: str) -> None:
        if len(token) < 32:
            raise ValueError("local token must contain at least 32 characters")
        self.connection.execute(
            "INSERT INTO local_users(id,token_hash) VALUES (?,?)",
            (str(user_id), hashlib.sha256(token.encode()).hexdigest()),
        )

    def replay(self, actor: UUID, operation: str, key: UUID, digest: str) -> UUID | None:
        row = self.connection.execute(
            "SELECT digest,result_id FROM idempotency WHERE actor=? AND operation=? AND key=?",
            (str(actor), operation, str(key)),
        ).fetchone()
        if row and row[0] != digest:
            raise ValueError("idempotency_payload_conflict")
        return UUID(row[1]) if row else None

    def remember(
        self,
        actor: UUID,
        operation: str,
        key: UUID,
        digest: str,
        result_id: UUID,
    ) -> None:
        self.connection.execute(
            "INSERT INTO idempotency(actor,operation,key,digest,result_id) VALUES (?,?,?,?,?)",
            (str(actor), operation, str(key), digest, str(result_id)),
        )

    def save_formation_records(
        self,
        formation: Formation,
        post: dict[str, Any],
        members: list[UUID],
    ) -> None:
        self.connection.execute(
            "INSERT INTO local_posts(id,data) VALUES (?,?)",
            (str(formation.post_id), json.dumps(post)),
        )
        self.connection.execute(
            "INSERT INTO local_conversations(id,post_id) VALUES (?,?)",
            (str(formation.conversation_id), str(formation.post_id)),
        )
        self.connection.executemany(
            "INSERT INTO local_conversation_members(conversation_id,user_id) VALUES (?,?)",
            [(str(formation.conversation_id), str(user_id)) for user_id in members],
        )


class LocalStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as unit:
            for table in MODELS:
                unit.connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY,data TEXT NOT NULL)"
                )
            unit.connection.executescript("""
                CREATE TABLE IF NOT EXISTS local_users(
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS local_blocks(
                    blocker TEXT NOT NULL, blocked TEXT NOT NULL,
                    PRIMARY KEY(blocker,blocked));
                CREATE TABLE IF NOT EXISTS idempotency(
                    actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
                    digest TEXT NOT NULL, result_id TEXT NOT NULL,
                    PRIMARY KEY(actor,operation,key));
                CREATE TABLE IF NOT EXISTS match_jobs(
                    actor TEXT PRIMARY KEY, requested_at TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1, next_attempt_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY, actor TEXT NOT NULL,
                    kind TEXT NOT NULL, at TEXT NOT NULL, proposal TEXT);
                CREATE INDEX IF NOT EXISTS ix_local_events_actor ON events(actor,kind,sequence);
                CREATE TABLE IF NOT EXISTS local_posts(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS local_conversations(
                    id TEXT PRIMARY KEY, post_id TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS local_conversation_members(
                    conversation_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    PRIMARY KEY(conversation_id,user_id));
                CREATE UNIQUE INDEX IF NOT EXISTS uq_local_proposal_fingerprint
                    ON proposals(json_extract(data,'$.fingerprint'));
                CREATE UNIQUE INDEX IF NOT EXISTS uq_local_formation_proposal
                    ON formations(json_extract(data,'$.proposal_id'));
            """)
            # Additive upgrade for earlier synthetic local prototypes only.
            columns = {row[1] for row in unit.connection.execute("PRAGMA table_info(match_jobs)")}
            if "next_attempt_at" not in columns:
                unit.connection.execute("ALTER TABLE match_jobs ADD COLUMN next_attempt_at TEXT")
                unit.connection.execute("UPDATE match_jobs SET next_attempt_at=requested_at")

    @contextmanager
    def transaction(self) -> Iterator[LocalUnitOfWork]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            yield LocalUnitOfWork(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
