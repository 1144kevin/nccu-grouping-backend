import app.models  # noqa: F401
from app.db.base import Base


def test_expected_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "blocks",
        "conversation_members",
        "conversations",
        "device_tokens",
        "join_requests",
        "messages",
        "notifications",
        "post_embeddings",
        "post_media",
        "posts",
        "profiles",
        "recommendation_events",
        "reports",
        "user_preferences",
        "stealth_agents",
        "stealth_events",
        "stealth_formations",
        "stealth_match_jobs",
        "stealth_proposals",
        "stealth_proposal_members",
        "stealth_signals",
    }
