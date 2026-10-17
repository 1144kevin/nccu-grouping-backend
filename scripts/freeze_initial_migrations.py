"""Developer utility: render fixed migration files without connecting to a database.

Refuses to overwrite an existing revision. Not called by the application.
"""

from pathlib import Path

from alembic.autogenerate import render_python_code
from alembic.operations import ops
from sqlalchemy import Enum

import app.models  # noqa: F401
from app.db.base import Base


def freeze(path: Path, revision: str, parent: str | None, tables: list, initial: bool) -> None:
    upgrade_ops = []
    if initial:
        upgrade_ops.append(ops.ExecuteSQLOp("CREATE EXTENSION IF NOT EXISTS vector"))
    for table in tables:
        upgrade_ops.append(ops.CreateTableOp.from_table(table))
        upgrade_ops.extend(
            ops.CreateIndexOp.from_index(index)
            for index in sorted(table.indexes, key=lambda index: index.name)
        )
    downgrade_ops = [ops.DropTableOp.from_table(table) for table in reversed(tables)]
    enums = sorted(
        {
            column.type.name
            for table in tables
            for column in table.columns
            if isinstance(column.type, Enum)
        }
    )
    downgrade_ops.extend(ops.ExecuteSQLOp(f"DROP TYPE IF EXISTS {name}") for name in enums)
    # Preserve the shared vector extension during downgrade.
    upgrade = render_python_code(ops.UpgradeOps(upgrade_ops)).replace(
        "astext_type=Text()", "astext_type=sa.Text()"
    )
    downgrade = render_python_code(ops.DowngradeOps(downgrade_ops)).replace(
        "astext_type=Text()", "astext_type=sa.Text()"
    )
    content = (
        '"""Frozen schema; generated offline, not applied to a database."""\n\n'
        "from alembic import op\nimport sqlalchemy as sa\n"
        "from sqlalchemy.dialects import postgresql\nimport pgvector.sqlalchemy.vector\n\n"
        f"revision = {revision!r}\ndown_revision = {parent!r}\n"
        "branch_labels = None\ndepends_on = None\n\n"
        f"def upgrade() -> None:\n{upgrade}\n\n"
        f"def downgrade() -> None:\n{downgrade}\n"
    )
    with path.open("x", encoding="utf-8") as output:
        output.write(content)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    tables = Base.metadata.sorted_tables
    freeze(
        root / "0001_initial.py",
        "0001_initial",
        None,
        [t for t in tables if not t.name.startswith("stealth_")],
        True,
    )
    freeze(
        root / "0002_stealth.py",
        "0002_stealth",
        "0001_initial",
        [t for t in tables if t.name.startswith("stealth_")],
        False,
    )
