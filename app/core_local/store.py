"""SQLite development storage. All state changes serialize through BEGIN IMMEDIATE."""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.core_local.schemas import (
    JoinView,
    LoginInput,
    PostEditInput,
    PostInput,
    PostPage,
    PostView,
    ProfileInput,
    ProfileView,
    PublicProfileView,
    RegisterInput,
    ServerClockView,
    SessionView,
)

PASSWORD_ROUNDS = 600_000


def utc_now() -> datetime:
    return datetime.now(UTC)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS
    ).hex()


class CoreStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            for statement in (
                """CREATE TABLE IF NOT EXISTS core_users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS core_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES core_users(id),
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS core_posts (
                    id TEXT PRIMARY KEY, author_id TEXT NOT NULL REFERENCES core_users(id),
                    payload TEXT NOT NULL, deadline_at TEXT NOT NULL,
                    capacity INTEGER NOT NULL CHECK(capacity BETWEEN 2 AND 20),
                    cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
                    created_at TEXT NOT NULL, request_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, UNIQUE(author_id, request_key))""",
                """CREATE TABLE IF NOT EXISTS core_joins (
                    id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES core_posts(id),
                    requester_id TEXT NOT NULL REFERENCES core_users(id),
                    status TEXT NOT NULL
                    CHECK(status IN ('pending','accepted','rejected','cancelled')),
                    created_at TEXT NOT NULL, decided_at TEXT,
                    UNIQUE(post_id, requester_id))""",
                "CREATE INDEX IF NOT EXISTS ix_core_posts_feed ON core_posts(created_at, id)",
                "CREATE INDEX IF NOT EXISTS ix_core_joins_post ON core_joins(post_id, status)",
                "CREATE INDEX IF NOT EXISTS ix_core_sessions_user ON core_sessions(user_id)",
            ):
                db.execute(statement)
            # Additive local schema upgrade; existing accounts and password hashes stay intact.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(core_users)")}
            for name, declaration in (
                ("bio", "TEXT NOT NULL DEFAULT ''"),
                ("badge", "TEXT NOT NULL DEFAULT '🍱'"),
                ("version", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE core_users ADD COLUMN {name} {declaration}")
            post_columns = {row["name"] for row in db.execute("PRAGMA table_info(core_posts)")}
            if "version" not in post_columns:
                db.execute("ALTER TABLE core_posts ADD COLUMN version INTEGER NOT NULL DEFAULT 1")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def profile(row: sqlite3.Row) -> ProfileView:
        return ProfileView.model_validate(dict(row))

    def register(self, value: RegisterInput) -> SessionView:
        salt = secrets.token_hex(16)
        digest = password_hash(value.password.get_secret_value(), salt)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM core_users WHERE email=?", (value.email,)).fetchone():
                raise HTTPException(409, "email_already_registered")
            user_id = str(uuid4())
            db.execute(
                """INSERT INTO core_users
                (id,email,display_name,salt,password_hash,created_at) VALUES (?,?,?,?,?,?)""",
                (user_id, value.email, value.display_name, salt, digest, utc_now().isoformat()),
            )
            return self._session(db, user_id)

    def login(self, value: LoginInput) -> SessionView:
        # Password work happens outside the write lock. Missing accounts take equal hash work.
        with self.transaction() as db:
            row = db.execute("SELECT * FROM core_users WHERE email=?", (value.email,)).fetchone()
        digest = password_hash(value.password.get_secret_value(), row["salt"] if row else "00" * 16)
        if not row or not hmac.compare_digest(digest, row["password_hash"]):
            raise HTTPException(401, "invalid_email_or_password")
        with self.transaction() as db:
            return self._session(db, row["id"])

    def _session(self, db: sqlite3.Connection, user_id: str) -> SessionView:
        now = utc_now()
        expires = now + timedelta(hours=24)
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM core_sessions WHERE expires_at<=?", (now.isoformat(),))
        # Keep at most five active sessions per account.
        db.execute(
            """DELETE FROM core_sessions WHERE token_hash IN (
                SELECT token_hash FROM core_sessions WHERE user_id=?
                ORDER BY created_at DESC LIMIT -1 OFFSET 4)""",
            (user_id,),
        )
        db.execute(
            "INSERT INTO core_sessions VALUES (?,?,?,?)",
            (
                token_hash(token),
                user_id,
                expires.isoformat(),
                now.isoformat(),
            ),
        )
        row = db.execute("SELECT * FROM core_users WHERE id=?", (user_id,)).fetchone()
        return SessionView(access_token=token, expires_at=expires, user=self.profile(row))

    def authenticate(self, token: str) -> ProfileView:
        if not 20 <= len(token) <= 200:
            raise HTTPException(401, "invalid_or_expired_session")
        with self.transaction() as db:
            row = db.execute(
                """SELECT u.* FROM core_users u JOIN core_sessions s ON s.user_id=u.id
                WHERE s.token_hash=? AND s.expires_at>?""",
                (token_hash(token), utc_now().isoformat()),
            ).fetchone()
        if row is None:
            raise HTTPException(
                401, "invalid_or_expired_session", headers={"WWW-Authenticate": "Bearer"}
            )
        return self.profile(row)

    def logout(self, token: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM core_sessions WHERE token_hash=?", (token_hash(token),))

    def update_profile(self, actor: UUID, value: ProfileInput) -> ProfileView:
        with self.transaction() as db:
            result = db.execute(
                """UPDATE core_users SET display_name=?,bio=?,badge=?,version=version+1
                WHERE id=? AND version=?""",
                (value.display_name, value.bio, value.badge, str(actor), value.expected_version),
            )
            if result.rowcount != 1:
                raise HTTPException(409, "profile_changed_reload")
            row = db.execute("SELECT * FROM core_users WHERE id=?", (str(actor),)).fetchone()
            return self.profile(row)

    @staticmethod
    def _row(db: sqlite3.Connection, post_id: UUID | str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM core_posts WHERE id=?", (str(post_id),)).fetchone()
        if row is None:
            raise HTTPException(404, "post_not_found")
        return row

    @staticmethod
    def _open(row: sqlite3.Row) -> None:
        if row["cancelled"]:
            raise HTTPException(409, "post_cancelled")
        if datetime.fromisoformat(row["deadline_at"]) <= utc_now():
            raise HTTPException(409, "post_deadline_passed")

    def _view(self, db: sqlite3.Connection, row: sqlite3.Row, actor: UUID) -> PostView:
        author = db.execute(
            "SELECT display_name,badge FROM core_users WHERE id=?", (row["author_id"],)
        ).fetchone()
        joined = (
            1
            + db.execute(
                "SELECT count(*) FROM core_joins WHERE post_id=? AND status='accepted'",
                (row["id"],),
            ).fetchone()[0]
        )
        membership = db.execute(
            "SELECT status FROM core_joins WHERE post_id=? AND requester_id=?",
            (row["id"], str(actor)),
        ).fetchone()
        status = (
            "cancelled"
            if row["cancelled"]
            else "expired"
            if datetime.fromisoformat(row["deadline_at"]) <= utc_now()
            else "full"
            if joined >= row["capacity"]
            else "open"
        )
        return PostView(
            **json.loads(row["payload"]),
            id=row["id"],
            author_id=row["author_id"],
            author_name=author["display_name"],
            author_badge=author["badge"],
            joined=joined,
            status=status,
            my_status="host"
            if row["author_id"] == str(actor)
            else membership["status"]
            if membership
            else "none",
            created_at=row["created_at"],
            version=row["version"],
            can_edit=(
                row["author_id"] == str(actor)
                and status == "open"
                and not db.execute(
                    "SELECT 1 FROM core_joins WHERE post_id=? "
                    "AND status IN ('pending','accepted') LIMIT 1",
                    (row["id"],),
                ).fetchone()
            ),
        )

    def create_post(self, actor: UUID, value: PostInput, request_key: str) -> PostView:
        payload = json.dumps(value.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.transaction() as db:
            existing = db.execute(
                "SELECT * FROM core_posts WHERE author_id=? AND request_key=?",
                (str(actor), request_key),
            ).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    raise HTTPException(409, "idempotency_key_payload_conflict")
                return self._view(db, existing, actor)
            if not utc_now() < value.deadline_at <= utc_now() + timedelta(days=14):
                raise HTTPException(422, "deadline_must_be_within_14_days")
            post_id = str(uuid4())
            db.execute(
                """INSERT INTO core_posts
                (id,author_id,payload,deadline_at,capacity,cancelled,created_at,request_key,payload_hash)
                VALUES (?,?,?,?,?,0,?,?,?)""",
                (
                    post_id,
                    str(actor),
                    payload,
                    value.deadline_at.isoformat(),
                    value.capacity,
                    utc_now().isoformat(),
                    request_key,
                    digest,
                ),
            )
            return self._view(db, self._row(db, post_id), actor)

    def post(self, actor: UUID, post_id: UUID) -> PostView:
        with self.transaction() as db:
            return self._view(db, self._row(db, post_id), actor)

    def edit_post(self, actor: UUID, post_id: UUID, value: PostEditInput) -> PostView:
        with self.transaction() as db:
            row = self._row(db, post_id)
            if row["author_id"] != str(actor):
                raise HTTPException(403, "host_only")
            self._open(row)
            if row["version"] != value.expected_version:
                raise HTTPException(409, "post_changed_reload")
            if not self._view(db, row, actor).can_edit:
                raise HTTPException(409, "post_has_active_requests")
            if not utc_now() < value.deadline_at <= utc_now() + timedelta(days=14):
                raise HTTPException(422, "deadline_must_be_within_14_days")
            payload = json.dumps(
                value.model_dump(mode="json", exclude={"expected_version"}),
                sort_keys=True,
                ensure_ascii=False,
            )
            db.execute(
                """UPDATE core_posts SET payload=?,deadline_at=?,capacity=?,version=version+1
                WHERE id=? AND version=?""",
                (
                    payload,
                    value.deadline_at.isoformat(),
                    value.capacity,
                    str(post_id),
                    value.expected_version,
                ),
            )
            return self._view(db, self._row(db, post_id), actor)

    def posts(
        self,
        actor: UUID,
        scope: str,
        limit: int,
        cursor: str | None,
        q: str = "",
        active_only: bool = False,
        author_id: UUID | None = None,
    ) -> PostPage:
        condition, params = "1=1", []
        if scope == "hosted":
            condition, params = "p.author_id=?", [str(actor)]
        elif scope == "participating":
            condition = (
                "p.id IN (SELECT post_id FROM core_joins WHERE requester_id=? "
                "AND status IN ('pending','accepted'))"
            )
            params = [str(actor)]
        elif scope == "history":
            condition = (
                "(p.author_id=? OR p.id IN (SELECT post_id FROM core_joins WHERE requester_id=?)) "
                "AND (p.cancelled=1 OR p.deadline_at<=? OR p.id IN "
                "(SELECT post_id FROM core_joins WHERE requester_id=? "
                "AND status IN ('cancelled','rejected')))"
            )
            params = [str(actor), str(actor), utc_now().isoformat(), str(actor)]
        if author_id is not None:
            condition += " AND p.author_id=?"
            params.append(str(author_id))
        if q.strip():
            # Escape SQL LIKE wildcards: a user's '%' is text, not an unbounded match.
            term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{term}%"
            condition += (
                " AND (json_extract(p.payload,'$.content') LIKE ? ESCAPE '\\'"
                " OR json_extract(p.payload,'$.tags') LIKE ? ESCAPE '\\'"
                " OR p.author_id IN (SELECT id FROM core_users "
                "WHERE display_name LIKE ? ESCAPE '\\'))"
            )
            params.extend((pattern, pattern, pattern))
        if active_only:
            condition += " AND p.cancelled=0 AND p.deadline_at>?"
            params.append(utc_now().isoformat())
        if cursor:
            try:
                created_at, post_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                datetime.fromisoformat(created_at)
                UUID(post_id)
            except (ValueError, TypeError, json.JSONDecodeError):
                raise HTTPException(422, "invalid_cursor") from None
            condition += " AND (p.created_at, p.id)<(?,?)"
            params.extend((created_at, post_id))
        with self.transaction() as db:
            rows = db.execute(
                f"SELECT p.* FROM core_posts p WHERE {condition} "
                "ORDER BY p.created_at DESC,p.id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps([last["created_at"], last["id"]]).encode()
                ).decode()
            return PostPage(
                items=[self._view(db, row, actor) for row in rows[:limit]], next_cursor=next_cursor
            )

    @staticmethod
    def _join_view(db: sqlite3.Connection, join_id: str) -> JoinView:
        row = db.execute(
            """SELECT j.*,u.display_name AS requester_name FROM core_joins j
            JOIN core_users u ON u.id=j.requester_id WHERE j.id=?""",
            (join_id,),
        ).fetchone()
        return JoinView.model_validate(dict(row))

    def request_join(self, actor: UUID, post_id: UUID, expected_version: int) -> JoinView:
        with self.transaction() as db:
            row = self._row(db, post_id)
            self._open(row)
            if row["version"] != expected_version:
                raise HTTPException(409, "post_changed_reload")
            if row["author_id"] == str(actor):
                raise HTTPException(409, "host_already_joined")
            existing = db.execute(
                "SELECT * FROM core_joins WHERE post_id=? AND requester_id=?",
                (str(post_id), str(actor)),
            ).fetchone()
            if existing and existing["status"] in ("pending", "accepted"):
                return self._join_view(db, existing["id"])
            if self._view(db, row, actor).joined >= row["capacity"]:
                raise HTTPException(409, "post_full")
            join_id = existing["id"] if existing else str(uuid4())
            db.execute(
                """INSERT INTO core_joins VALUES (?,?,?,'pending',?,NULL)
                ON CONFLICT(post_id,requester_id) DO UPDATE SET
                status='pending',created_at=excluded.created_at,decided_at=NULL""",
                (join_id, str(post_id), str(actor), utc_now().isoformat()),
            )
            return self._join_view(db, join_id)

    def join_requests(self, actor: UUID, post_id: UUID) -> list[JoinView]:
        with self.transaction() as db:
            row = self._row(db, post_id)
            if row["author_id"] != str(actor):
                raise HTTPException(403, "host_only")
            ids = db.execute(
                "SELECT id FROM core_joins WHERE post_id=? ORDER BY created_at,id", (str(post_id),)
            ).fetchall()
            return [self._join_view(db, item["id"]) for item in ids]

    def decide(self, actor: UUID, join_id: UUID, decision: str) -> JoinView:
        with self.transaction() as db:
            request = db.execute("SELECT * FROM core_joins WHERE id=?", (str(join_id),)).fetchone()
            if request is None:
                raise HTTPException(404, "join_request_not_found")
            row = self._row(db, request["post_id"])
            if row["author_id"] != str(actor):
                raise HTTPException(403, "host_only")
            if request["status"] == decision:
                return self._join_view(db, str(join_id))
            self._open(row)
            if request["status"] != "pending":
                raise HTTPException(409, "join_request_not_pending")
            if decision == "accepted" and self._view(db, row, actor).joined >= row["capacity"]:
                raise HTTPException(409, "post_full")
            db.execute(
                "UPDATE core_joins SET status=?,decided_at=? WHERE id=?",
                (
                    decision,
                    utc_now().isoformat(),
                    str(join_id),
                ),
            )
            return self._join_view(db, str(join_id))

    def withdraw(self, actor: UUID, post_id: UUID) -> PostView:
        with self.transaction() as db:
            row = self._row(db, post_id)
            if row["author_id"] == str(actor):
                raise HTTPException(409, "host_must_cancel_post")
            request = db.execute(
                "SELECT * FROM core_joins WHERE post_id=? AND requester_id=?",
                (str(post_id), str(actor)),
            ).fetchone()
            if not request or request["status"] in ("cancelled", "rejected"):
                return self._view(db, row, actor)
            self._open(row)
            db.execute(
                "UPDATE core_joins SET status='cancelled',decided_at=? WHERE id=?",
                (
                    utc_now().isoformat(),
                    request["id"],
                ),
            )
            return self._view(db, row, actor)

    def cancel(self, actor: UUID, post_id: UUID) -> PostView:
        with self.transaction() as db:
            row = self._row(db, post_id)
            if row["author_id"] != str(actor):
                raise HTTPException(403, "host_only")
            if not row["cancelled"]:
                self._open(row)
                db.execute("UPDATE core_posts SET cancelled=1 WHERE id=?", (str(post_id),))
            return self._view(db, self._row(db, post_id), actor)

    def public_profile(self, user_id: UUID) -> PublicProfileView:
        with self.transaction() as db:
            row = db.execute(
                "SELECT id,display_name,bio,badge,created_at FROM core_users WHERE id=?",
                (str(user_id),),
            ).fetchone()
            if not row:
                raise HTTPException(404, "public_profile_not_found")
            count = db.execute(
                "SELECT count(*) FROM core_posts "
                "WHERE author_id=? AND cancelled=0 AND deadline_at>?",
                (str(user_id), utc_now().isoformat()),
            ).fetchone()[0]
            return PublicProfileView.model_validate({**dict(row), "active_posts": count})

    def clock(self) -> ServerClockView:
        return ServerClockView(server_time=utc_now())

    def user_posts(self, actor: UUID, user_id: UUID, limit: int, cursor: str | None) -> PostPage:
        self.public_profile(user_id)
        return self.posts(actor, "feed", limit, cursor, author_id=user_id)
