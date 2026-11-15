"""merge 库存追溯列与 SA-01 报价单号规则

Revision ID: ff36274a4c73
Revises: a7b3d9e1f5c4, e2f4a6b8c0d2
Create Date: 2026-11-07 10:05:57.614219

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ff36274a4c73'
down_revision: str | None = ('a7b3d9e1f5c4', 'e2f4a6b8c0d2')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
