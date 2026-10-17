import re

from sqlalchemy.dialects import postgresql

from app.stealth.postgres import candidate_query
from tests.test_stealth import NOW
from tests.test_stealth_matching_matrix import make_signal


def test_candidate_sql_filters_authorization_before_limit():
    signal = make_signal()
    statement = candidate_query(signal.owner_id, signal, NOW)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "JOIN stealth_agents" in sql
    assert "stealth_agents.enabled IS true" in sql
    assert "NOT (EXISTS" in sql
    assert "blocks.blocker_id" in sql and "blocks.blocked_id" in sql
    assert sql.index("stealth_signals.category IN") < sql.index("LIMIT")
    limit_parameter = re.search(r"LIMIT %\(([^)]+)\)s", sql).group(1)
    assert compiled.params[limit_parameter] == 60
    # User input is bound as parameters, not interpolated into SQL.
    assert str(signal.owner_id) not in sql
