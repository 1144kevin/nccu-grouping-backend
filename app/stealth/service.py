from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.stealth.errors import StealthError
from app.stealth.matching import find_candidates
from app.stealth.presentation import invitation_copy
from app.stealth.rules import payload_digest, snapshot_is_valid, validate_formation_terms
from app.stealth.schemas import (
    Agent,
    AgentInput,
    Formation,
    FormationInput,
    Proposal,
    ProposalStatus,
    ProposalView,
    Signal,
    SignalInput,
    SignalStatus,
)
from app.stealth.storage import LocalStore, LocalUnitOfWork


def utc_now() -> datetime:
    return datetime.now(UTC)


class StealthService:
    def __init__(self, store: LocalStore):
        self.store = store

    def get_agent(self, actor: UUID) -> Agent | None:
        with self.store.transaction() as unit:
            return unit.get("agents", actor)

    def block_user(self, actor: UUID, target: UUID, now: datetime) -> None:
        if actor == target:
            raise StealthError(422, "cannot_block_self")
        with self.store.transaction() as unit:
            unit.connection.execute(
                "INSERT OR IGNORE INTO local_blocks(blocker,blocked) VALUES (?,?)",
                (str(actor), str(target)),
            )
            for proposal in unit.all("proposals"):
                owners = {member.user_id for member in proposal.members}
                if {actor, target} <= owners and proposal.status in (
                    ProposalStatus.PENDING,
                    ProposalStatus.READY,
                ):
                    proposal.status = ProposalStatus.INVALIDATED
                    unit.put("proposals", proposal)
            unit.event(actor, "user_blocked", now)

    def unblock_user(self, actor: UUID, target: UUID, now: datetime) -> None:
        with self.store.transaction() as unit:
            unit.connection.execute(
                "DELETE FROM local_blocks WHERE blocker=? AND blocked=?",
                (str(actor), str(target)),
            )
            unit.event(actor, "user_unblocked", now)

    def put_agent(self, actor: UUID, value: AgentInput, now: datetime) -> Agent:
        with self.store.transaction() as unit:
            previous = unit.get("agents", actor)
            version = previous.version if previous else 0
            if value.expected_version != version:
                raise StealthError(409, "agent_version_conflict")
            policy_changed = previous is None or (
                previous.enabled != value.enabled or previous.exploration != value.exploration
            )
            policy_version = (previous.policy_version if previous else 0) + int(policy_changed)
            agent = Agent(
                **value.model_dump(exclude={"expected_version"}),
                user_id=actor,
                version=version + 1,
                policy_version=policy_version,
                updated_at=now,
            )
            unit.put("agents", agent)
            if policy_changed:
                self._invalidate_for_user(unit, actor)
            unit.event(actor, "agent_updated", now)
            return agent

    @staticmethod
    def _invalidate_for_user(unit: LocalUnitOfWork, actor: UUID) -> None:
        for proposal in unit.all("proposals"):
            if proposal.status in (ProposalStatus.PENDING, ProposalStatus.READY) and any(
                member.user_id == actor for member in proposal.members
            ):
                proposal.status = ProposalStatus.INVALIDATED
                unit.put("proposals", proposal)

    def create_signal(
        self,
        actor: UUID,
        value: SignalInput,
        now: datetime,
        request_id: UUID | None = None,
    ) -> Signal:
        with self.store.transaction() as unit:
            digest = payload_digest(value)
            if request_id:
                try:
                    replay = unit.replay(actor, "create_signal", request_id, digest)
                except ValueError as exc:
                    raise StealthError(409, "idempotency_payload_conflict") from exc
                if replay:
                    return unit.get("signals", replay)
            if value.expires_at <= now or value.ends_at <= now:
                raise StealthError(422, "signal_already_expired")
            if value.ends_at > now + timedelta(days=7):
                raise StealthError(422, "signal_too_far_in_future")
            agent = unit.get("agents", actor)
            if not agent:
                raise StealthError(409, "create_agent_first")
            active = [s for s in unit.all("signals") if s.owner_id == actor and s.usable_at(now)]
            if len(active) >= 10:
                raise StealthError(429, "active_signal_limit")
            signal = Signal(**value.model_dump(), id=uuid4(), owner_id=actor, created_at=now)
            unit.put("signals", signal)
            unit.connection.execute(
                "INSERT INTO match_jobs(actor,requested_at,next_attempt_at) VALUES (?,?,?) "
                "ON CONFLICT(actor) DO UPDATE SET requested_at=excluded.requested_at, "
                "generation=match_jobs.generation+1, next_attempt_at=excluded.next_attempt_at",
                (str(actor), now.isoformat(), now.isoformat()),
            )
            if request_id:
                unit.remember(actor, "create_signal", request_id, digest, signal.id)
            unit.event(actor, "signal_created", now)
            return signal

    def get_signal(self, actor: UUID, signal_id: UUID) -> Signal:
        with self.store.transaction() as unit:
            signal = unit.get("signals", signal_id)
            if not signal or signal.owner_id != actor:
                raise StealthError(404, "not_found")
            return signal

    def revoke_signal(self, actor: UUID, signal_id: UUID, now: datetime) -> Signal:
        with self.store.transaction() as unit:
            signal = unit.get("signals", signal_id)
            if not signal or signal.owner_id != actor:
                raise StealthError(404, "not_found")
            if signal.status == SignalStatus.CONSUMED:
                raise StealthError(409, "signal_already_used")
            if signal.status != SignalStatus.REVOKED:
                signal.status = SignalStatus.REVOKED
                signal.version += 1
                unit.put("signals", signal)
                for proposal in unit.all("proposals"):
                    if proposal.status in (ProposalStatus.PENDING, ProposalStatus.READY) and any(
                        m.signal_id == signal_id for m in proposal.members
                    ):
                        proposal.status = ProposalStatus.INVALIDATED
                        unit.put("proposals", proposal)
                unit.event(actor, "signal_revoked", now)
            return signal

    def run_matches(self, actor: UUID, maximum: int, now: datetime) -> list[ProposalView]:
        if not 1 <= maximum <= 5:
            raise StealthError(422, "invalid_match_limit")
        with self.store.transaction() as unit:
            agents = {a.user_id: a for a in unit.all("agents")}
            agent = agents.get(actor)
            if not agent or not agent.enabled:
                raise StealthError(409, "agent_not_enabled")
            if agent.quiet_at(now):
                raise StealthError(409, "agent_quiet_hours")
            last_run = unit.last_event(actor, "match_run")
            if last_run and now - last_run < timedelta(seconds=30):
                raise StealthError(429, "match_cooldown")
            unit.event(actor, "match_run", now)
            proposals = unit.all("proposals")
            seen = {p.fingerprint for p in proposals}
            counts = {user: 0 for user in agents}
            for proposal in proposals:
                for member in proposal.members:
                    zone = ZoneInfo(agents[member.user_id].timezone)
                    if proposal.created_at.astimezone(zone).date() == now.astimezone(zone).date():
                        counts[member.user_id] += 1
            candidates = find_candidates(
                actor,
                unit.all("signals"),
                agents,
                unit.blocked_pairs(),
                now,
            )
            created = []
            for candidate in candidates:
                if candidate.fingerprint in seen or any(
                    counts[m.user_id] >= agents[m.user_id].daily_invite_limit
                    for m in candidate.members
                ):
                    continue
                unit.put("proposals", candidate)
                seen.add(candidate.fingerprint)
                for member in candidate.members:
                    counts[member.user_id] += 1
                created.append(self._view(candidate, actor, now, unit))
                if len(created) >= maximum:
                    break
            return created

    def attempt_passive_matches(self, actor: UUID) -> None:
        """Attempt one persisted local job; explicit CLI dispatch can retry after restart."""
        now = utc_now()
        with self.store.transaction() as unit:
            row = unit.connection.execute(
                "SELECT generation,next_attempt_at FROM match_jobs WHERE actor=?",
                (str(actor),),
            ).fetchone()
            has_signal = any(s.owner_id == actor and s.usable_at(now) for s in unit.all("signals"))
            if row and not has_signal:
                unit.connection.execute("DELETE FROM match_jobs WHERE actor=?", (str(actor),))
                return
        if not row or datetime.fromisoformat(row[1]) > now:
            return
        retry_seconds = 60
        try:
            created = self.run_matches(actor, 3, now)
        except StealthError as exc:
            if exc.status in (409, 429):
                created = []
                retry_seconds = 30 if exc.status == 429 else 900
            else:
                raise
        if not created:
            with self.store.transaction() as unit:
                unit.connection.execute(
                    "UPDATE match_jobs SET next_attempt_at=? WHERE actor=? AND generation=?",
                    ((now + timedelta(seconds=retry_seconds)).isoformat(), str(actor), row[0]),
                )
            return
        with self.store.transaction() as unit:
            # Never delete a newer signal's work if it was queued while matching.
            unit.connection.execute(
                "DELETE FROM match_jobs WHERE actor=? AND generation=?",
                (str(actor), row[0]),
            )

    def dispatch_local_jobs(self, maximum: int = 20) -> int:
        if not 1 <= maximum <= 20:
            raise StealthError(422, "invalid_dispatch_limit")
        with self.store.transaction() as unit:
            actors = [
                UUID(row[0])
                for row in unit.connection.execute(
                    "SELECT actor FROM match_jobs WHERE next_attempt_at<=? "
                    "ORDER BY next_attempt_at,actor LIMIT ?",
                    (utc_now().isoformat(), maximum),
                )
            ]
        for actor in actors:
            self.attempt_passive_matches(actor)
        return len(actors)

    @staticmethod
    def _validate(proposal: Proposal, unit: LocalUnitOfWork, now: datetime) -> bool:
        return snapshot_is_valid(
            proposal,
            {m.signal_id: unit.get("signals", m.signal_id) for m in proposal.members},
            {m.user_id: unit.get("agents", m.user_id) for m in proposal.members},
            unit.blocked_pairs(),
            now,
        )

    @classmethod
    def _view(
        cls,
        proposal: Proposal,
        actor: UUID,
        now: datetime,
        unit: LocalUnitOfWork,
    ) -> ProposalView:
        member = next((m for m in proposal.members if m.user_id == actor), None)
        if member is None:
            raise StealthError(404, "not_found")
        status = proposal.status.value
        if status not in ("pending", "ready", "formed"):
            status = "unavailable"
        if status in ("pending", "ready") and not cls._validate(proposal, unit, now):
            status = "unavailable"
        signal = unit.get("signals", member.signal_id)
        agent = unit.get("agents", actor)
        invitation_text, why_me = invitation_copy(proposal, agent, signal)
        if status == "unavailable":
            invitation_text = "這個合作方案已失效，不會再替你安排。"
        elif status == "formed":
            invitation_text = "這個合作方案已成立，可以前往共享聊天室確認細節。"
        return ProposalView(
            **proposal.model_dump(
                exclude={
                    "members",
                    "fingerprint",
                    "created_at",
                    "status",
                    "terms_author_id",
                }
            ),
            member_count=len(proposal.members),
            my_decision=member.decision,
            my_decline_reason=member.decline_reason,
            my_roles=signal.roles,
            invitation_text=invitation_text,
            why_me=why_me,
            status=status,
            my_accepted_terms_revision=member.accepted_terms_revision,
            can_form=status == "ready" and proposal.terms_author_id in (None, actor),
        )

    def get_proposal(self, actor: UUID, proposal_id: UUID, now: datetime) -> ProposalView:
        with self.store.transaction() as unit:
            proposal = unit.get("proposals", proposal_id)
            if not proposal:
                raise StealthError(404, "not_found")
            return self._view(proposal, actor, now, unit)

    def decide(
        self,
        actor: UUID,
        proposal_id: UUID,
        decision: str,
        now: datetime,
        decline_reason: str | None = None,
    ) -> ProposalView:
        if decision not in ("accepted", "declined"):
            raise StealthError(422, "invalid_decision")
        if decline_reason is not None and (
            decision != "declined"
            or decline_reason not in ("not_now", "time", "budget", "food", "privacy", "other")
        ):
            raise StealthError(422, "invalid_decline_reason")
        with self.store.transaction() as unit:
            proposal = unit.get("proposals", proposal_id)
            if not proposal:
                raise StealthError(404, "not_found")
            self._view(proposal, actor, now, unit)  # membership before status disclosure
            member = next(m for m in proposal.members if m.user_id == actor)
            if member.decision == decision and member.decline_reason != decline_reason:
                raise StealthError(409, "decision_payload_conflict")
            if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.READY):
                if member.decision == decision and proposal.status == ProposalStatus.DECLINED:
                    return self._view(proposal, actor, now, unit)
                raise StealthError(409, "proposal_unavailable")
            if not self._validate(proposal, unit, now):
                raise StealthError(409, "proposal_unavailable")
            if member.decision == decision:
                return self._view(proposal, actor, now, unit)
            if member.decision == "declined":
                raise StealthError(409, "decision_final")
            member.decision, member.decided_at = decision, now
            member.accepted_terms_revision = (
                proposal.terms_revision if decision == "accepted" else None
            )
            member.decline_reason = decline_reason
            if decision == "declined":
                proposal.status = ProposalStatus.DECLINED
            elif all(m.decision == "accepted" for m in proposal.members):
                proposal.status = ProposalStatus.READY
            unit.put("proposals", proposal)
            unit.event(actor, f"decision_{decision}", now, proposal.id)
            return self._view(proposal, actor, now, unit)

    def form(
        self,
        actor: UUID,
        proposal_id: UUID,
        value: FormationInput,
        now: datetime,
    ) -> Formation:
        with self.store.transaction() as unit:
            proposal = unit.get("proposals", proposal_id)
            if not proposal:
                raise StealthError(404, "not_found")
            self._view(proposal, actor, now, unit)
            existing = next(
                (f for f in unit.all("formations") if f.proposal_id == proposal_id), None
            )
            if existing:
                if existing.host_id != actor:
                    raise StealthError(409, "already_formed_by_other_host")
                digest = payload_digest(value)
                try:
                    unit.replay(actor, "form", proposal.id, digest)
                except ValueError as exc:
                    raise StealthError(409, "idempotency_payload_conflict") from exc
                return existing
            if proposal.status != ProposalStatus.READY or not self._validate(proposal, unit, now):
                raise StealthError(409, "proposal_not_ready")
            validate_formation_terms(
                proposal,
                [unit.get("signals", m.signal_id) for m in proposal.members],
                value,
                now,
                actor,
            )
            formation = Formation(
                id=uuid4(),
                proposal_id=proposal_id,
                host_id=actor,
                post_id=uuid4(),
                conversation_id=uuid4(),
                created_at=now,
            )
            members = [m.user_id for m in proposal.members]
            unit.save_formation_records(
                formation,
                {
                    "id": str(formation.post_id),
                    "author_id": str(actor),
                    "content": value.content,
                    "location_id": proposal.location_id,
                    "deadline_at": value.deadline_at.isoformat(),
                    "pickup_starts_at": proposal.starts_at.isoformat(),
                    "pickup_ends_at": proposal.ends_at.isoformat(),
                    "capacity": len(members),
                    "status": "full",
                    "confirmed_cost_twd": value.confirmed_cost_twd,
                    "host_roles": value.host_roles,
                    "member_ids": [str(user) for user in members],
                },
                members,
            )
            for member in proposal.members:
                signal = unit.get("signals", member.signal_id)
                signal.status = SignalStatus.CONSUMED
                signal.version += 1
                unit.put("signals", signal)
            proposal.status = ProposalStatus.FORMED
            unit.put("proposals", proposal)
            unit.put("formations", formation)
            unit.remember(
                actor,
                "form",
                proposal.id,
                payload_digest(value),
                formation.id,
            )
            unit.event(actor, "formed", now, proposal.id)
            return formation

    def get_formation(self, actor: UUID, proposal_id: UUID) -> Formation:
        with self.store.transaction() as unit:
            proposal = unit.get("proposals", proposal_id)
            if not proposal or not any(m.user_id == actor for m in proposal.members):
                raise StealthError(404, "not_found")
            formation = next(
                (f for f in unit.all("formations") if f.proposal_id == proposal_id),
                None,
            )
            if not formation or proposal.status != ProposalStatus.FORMED:
                raise StealthError(404, "not_found")
            return formation

    def list_items(
        self,
        actor: UUID,
        table: str,
        limit: int,
        cursor: str | None,
        now: datetime,
    ) -> tuple[list, str | None]:
        if table not in ("signals", "proposals") or not 1 <= limit <= 50:
            raise StealthError(422, "invalid_page")
        after = None
        if cursor:
            try:
                if len(cursor) > 512:
                    raise ValueError
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if decoded["table"] != table or decoded["actor"] != str(actor):
                    raise ValueError
                after = (datetime.fromisoformat(decoded["at"]), UUID(decoded["id"]))
                if after[0].tzinfo is None:
                    raise ValueError
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise StealthError(422, "invalid_cursor") from exc
        with self.store.transaction() as unit:
            values = [
                item
                for item in unit.all(table)
                if (
                    item.owner_id == actor
                    if table == "signals"
                    else any(m.user_id == actor for m in item.members)
                )
            ]
            values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
            if after:
                values = [item for item in values if (item.created_at, item.id) < after]
            page = values[:limit]
            next_cursor = None
            if len(values) > limit:
                last = page[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "table": table,
                            "actor": str(actor),
                            "at": last.created_at.isoformat(),
                            "id": str(last.id),
                        }
                    ).encode()
                ).decode()
            if table == "proposals":
                page = [self._view(p, actor, now, unit) for p in page]
            return page, next_cursor
