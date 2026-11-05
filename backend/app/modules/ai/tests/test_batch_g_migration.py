"""Upgrade seeds global ceilings from existing daily usage; tenant counters retain FORCE RLS."""

import importlib
import uuid
from datetime import date

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine, text


def test_budget_migration_preserves_previous_spending(owner_engine: Engine, business: uuid.UUID) -> None:
    migration = importlib.import_module("migrations.versions.20261104_0600_ai_hard_budgets")
    with owner_engine.begin() as db:
        for day, cost in ((date(2026, 11, 1), 120), (date(2026, 11, 2), 230)):
            db.execute(
                text("insert into ai_usage_daily (id,business_id,day,cost_micros) values (:id,:b,:day,:cost)"),
                {"id": uuid.uuid4(), "b": business, "day": day, "cost": cost},
            )
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
            migration.upgrade()
        costs = db.execute(text("select kind, period, cost_micros from ai_cost_periods order by kind, period")).all()
        assert costs == [
            ("day", date(2026, 11, 1), 120),
            ("day", date(2026, 11, 2), 230),
            ("month", date(2026, 11, 1), 350),
        ]
        for table in ("ai_public_allowances", "ai_budget_notices"):
            assert db.execute(
                text("select relrowsecurity, relforcerowsecurity from pg_class where relname=:t"), {"t": table}
            ).one() == (True, True)
