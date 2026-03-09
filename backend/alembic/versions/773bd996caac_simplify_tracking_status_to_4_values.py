"""simplify_tracking_status_to_4_values

Revision ID: 773bd996caac
Revises: 7c911f13efd0
Create Date: 2026-03-08 23:09:52.979836

WHY MANUAL:
  Alembic can't autogenerate enum value changes — it only detects column additions/
  removals, not changes to the set of values in an existing enum type.

  PostgreSQL also can't remove values from an existing enum type (only add them).
  The standard pattern is: create a new type → migrate data → swap the column → drop old type.

OLD VALUES (8):  applied, phone_screen, technical_interview, final_round, offer, rejected, ghosted, withdrawn
NEW VALUES (4):  applied, interview, offer, rejected

DATA MAPPING:
  applied              → applied
  phone_screen         → interview
  technical_interview  → interview
  final_round          → interview
  offer                → offer
  rejected             → rejected
  ghosted              → rejected
  withdrawn            → rejected
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '773bd996caac'
down_revision: Union[str, Sequence[str], None] = '7c911f13efd0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Collapse 8 tracking statuses into 4."""
    # Step 1: Drop the column default — it casts 'applied' using the old enum type.
    # PostgreSQL won't let us drop the enum type while any default references it.
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status DROP DEFAULT")

    # Step 2: Cast column to plain TEXT so we can UPDATE freely without enum constraints.
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE TEXT USING tracking_status::TEXT")

    # Step 3: Normalize to lowercase first.
    # The previous migration used server_default='APPLIED' (uppercase), so existing rows
    # may have uppercase values. The new enum only has lowercase — normalize before remapping.
    op.execute("UPDATE applications SET tracking_status = LOWER(tracking_status)")

    # Step 4: Remap old values to new simplified values.
    op.execute("UPDATE applications SET tracking_status = 'interview' WHERE tracking_status IN ('phone_screen', 'technical_interview', 'final_round')")
    op.execute("UPDATE applications SET tracking_status = 'rejected'  WHERE tracking_status IN ('ghosted', 'withdrawn')")
    # applied, offer, rejected stay as-is

    # Step 5: Drop the old 8-value enum type (no dependents remain — default was dropped above).
    op.execute("DROP TYPE IF EXISTS tracking_status")

    # Step 6: Create the new 4-value enum type.
    op.execute("CREATE TYPE tracking_status AS ENUM ('applied', 'interview', 'offer', 'rejected')")

    # Step 7: Cast the column back to the new enum type.
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE tracking_status USING tracking_status::tracking_status")

    # Step 8: Restore the NOT NULL constraint and default.
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET NOT NULL")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET DEFAULT 'applied'")


def downgrade() -> None:
    """Expand 4 tracking statuses back to 8 (best-effort — interview maps to phone_screen)."""
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE TEXT USING tracking_status::TEXT")
    op.execute("UPDATE applications SET tracking_status = 'phone_screen' WHERE tracking_status = 'interview'")
    op.execute("UPDATE applications SET tracking_status = 'ghosted'      WHERE tracking_status = 'rejected'")
    op.execute("DROP TYPE IF EXISTS tracking_status")
    op.execute("""
        CREATE TYPE tracking_status AS ENUM (
            'applied', 'phone_screen', 'technical_interview', 'final_round',
            'offer', 'rejected', 'ghosted', 'withdrawn'
        )
    """)
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE tracking_status USING tracking_status::tracking_status")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET NOT NULL")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET DEFAULT 'applied'")
