"""fix_tracking_status_enum_case

Revision ID: 0ad017bc4f5a
Revises: 773bd996caac
Create Date: 2026-03-08 23:19:21.966926

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0ad017bc4f5a'
down_revision: Union[str, Sequence[str], None] = '773bd996caac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Convert tracking_status enum values to UPPERCASE to match SQLAlchemy naming.

    SQLAlchemy's SAEnum stores Python enum NAMES (uppercase: "APPLIED") in PostgreSQL,
    not the string VALUES (lowercase: "applied"). The previous migration created the
    enum type with lowercase values, so SQLAlchemy reads "applied" from the DB and
    fails to find it in its internal lookup table (which keys by name: "APPLIED").

    Fix: recreate the enum type with UPPERCASE values.
    """
    # Drop default — it references the old enum type and blocks DROP TYPE
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status DROP DEFAULT")

    # Cast to TEXT so we can UPPER() freely without enum constraints
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE TEXT USING tracking_status::TEXT")

    # Uppercase all existing data rows
    op.execute("UPDATE applications SET tracking_status = UPPER(tracking_status)")

    # Drop the old lowercase enum type (no column default depending on it now)
    op.execute("DROP TYPE IF EXISTS tracking_status")

    # Create new UPPERCASE enum — names match Python enum member names
    op.execute("CREATE TYPE tracking_status AS ENUM ('APPLIED', 'INTERVIEW', 'OFFER', 'REJECTED')")

    # Cast column back to the corrected enum type
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE tracking_status USING tracking_status::tracking_status")

    # Restore NOT NULL constraint and default (uppercase to match enum)
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET NOT NULL")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET DEFAULT 'APPLIED'")


def downgrade() -> None:
    """Revert to lowercase enum values."""
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status DROP DEFAULT")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE TEXT USING tracking_status::TEXT")
    op.execute("UPDATE applications SET tracking_status = LOWER(tracking_status)")
    op.execute("DROP TYPE IF EXISTS tracking_status")
    op.execute("CREATE TYPE tracking_status AS ENUM ('applied', 'interview', 'offer', 'rejected')")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status TYPE tracking_status USING tracking_status::tracking_status")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET NOT NULL")
    op.execute("ALTER TABLE applications ALTER COLUMN tracking_status SET DEFAULT 'applied'")
