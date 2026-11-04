"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Created (UTC): ${create_date}
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.helpers import create_updated_at_trigger, enable_business_rls  # noqa: F401

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
