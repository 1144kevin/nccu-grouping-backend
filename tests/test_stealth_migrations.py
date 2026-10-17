import importlib.util
import io
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401
from app.db.base import Base


def test_frozen_migrations_render_all_tables_without_database():
    output = io.StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    created = set(re.findall(r"CREATE TABLE (\w+)", sql)) - {"alembic_version"}
    assert created == set(Base.metadata.tables)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "UNIQUE (fingerprint)" in sql
    assert "known_cost_twd INTEGER" in sql
    assert "DROP TABLE" not in sql
    assert "ALTER TABLE stealth_signals ENABLE ROW LEVEL SECURITY" in sql
    protected = set(re.findall(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY", sql))
    assert protected == {name for name in Base.metadata.tables if name.startswith("stealth_")}
    assert "fk_stealth_member_signal_owner" in sql


def test_migrations_do_not_depend_on_mutable_application_metadata():
    for path in Path("migrations/versions").glob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert "Base.metadata" not in content
        assert "create_all" not in content
        assert "from app.models" not in content


def test_frozen_columns_match_current_orm_without_connecting(monkeypatch):
    """Capture the tables Alembic actually creates, rather than only checking names."""
    from alembic import op

    captured = {}
    original = op.create_table

    def capture(name, *elements, **kwargs):
        table = original(name, *elements, **kwargs)
        captured[name] = table
        return table

    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={
            "as_sql": True,
            "output_buffer": io.StringIO(),
        },
    )
    with Operations.context(context):
        monkeypatch.setattr(op, "create_table", capture)
        for path in sorted(Path("migrations/versions").glob("*.py")):
            spec = importlib.util.spec_from_file_location("captured_revision", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    dialect = postgresql.dialect()
    assert set(captured) == set(Base.metadata.tables)
    for name, model in Base.metadata.tables.items():
        frozen = captured[name]
        assert set(frozen.columns.keys()) == set(model.columns.keys()), name
        for column in model.columns:
            actual = frozen.columns[column.name]
            assert actual.type.compile(dialect=dialect) == column.type.compile(dialect=dialect)
            assert actual.nullable == column.nullable, (name, column.name)
            assert actual.primary_key == column.primary_key, (name, column.name)
