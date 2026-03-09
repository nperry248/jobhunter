"""add_tracking_status_to_applications

Revision ID: 7c911f13efd0
Revises: 6cf6377d8abb
Create Date: 2026-03-08 22:54:38.994277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c911f13efd0'
down_revision: Union[str, Sequence[str], None] = '6cf6377d8abb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create the enum type first, then add the column.
    # server_default='APPLIED' is required: if existing rows are in the table,
    # PostgreSQL can't add a NOT NULL column without a default to fill those rows.
    tracking_status_enum = sa.Enum(
        'APPLIED', 'PHONE_SCREEN', 'TECHNICAL_INTERVIEW', 'FINAL_ROUND',
        'OFFER', 'REJECTED', 'GHOSTED', 'WITHDRAWN',
        name='tracking_status'
    )
    tracking_status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('applications', sa.Column(
        'tracking_status',
        tracking_status_enum,
        nullable=False,
        server_default='APPLIED',
    ))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('applications', 'tracking_status')
    sa.Enum(name='tracking_status').drop(op.get_bind(), checkfirst=True)
