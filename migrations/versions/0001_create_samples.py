"""create samples table

Initial PostgreSQL schema for the narrow, labeled metric model: one row per
measurement, with dimensions carried in a JSONB ``labels`` column.

The DDL is written as explicit SQL rather than via Alembic's ``op`` helpers,
for two reasons. First, it keeps this project's convention that all SQL is
visible and reviewable rather than generated. Second, the schema uses
PostgreSQL-specific features (JSONB, GIN and BRIN indexes) that have no
portable equivalent, so a dialect-agnostic builder would buy nothing.

Alembic owns the PostgreSQL schema only. SQLite creates its own tables in
SqliteRepository.init_schema(); tests/test_schema_parity.py guards the two
definitions against drifting apart.

Revision ID: 0001_create_samples
Revises:
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_create_samples"
down_revision = None
branch_labels = None
depends_on = None


CREATE_TABLE = """
CREATE TABLE samples (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp  TIMESTAMPTZ      NOT NULL,
    name       TEXT             NOT NULL,
    value      DOUBLE PRECISION NOT NULL,
    kind       TEXT             NOT NULL,
    unit       TEXT             NOT NULL DEFAULT '',
    labels     JSONB            NOT NULL DEFAULT '{}'::jsonb
);
"""

# Index rationale:
#   name_ts  - the read path is "recent points for this metric", so a
#              composite B-tree on (name, timestamp DESC) serves both the
#              filter and the ordering without a sort.
#   labels   - GIN makes the JSONB containment operator (@>) indexable, e.g.
#              labels @> '{"interface":"eth0"}'. This is the capability the
#              labeled model exists for and that SQLite could not provide.
#   ts_brin  - BRIN is tiny (a few pages) and ideal for append-only,
#              naturally time-ordered data; it accelerates range scans over
#              time at a fraction of a B-tree's storage cost.
CREATE_INDEXES = (
    "CREATE INDEX idx_samples_name_ts ON samples (name, timestamp DESC);",
    "CREATE INDEX idx_samples_labels  ON samples USING GIN (labels);",
    "CREATE INDEX idx_samples_ts_brin ON samples USING BRIN (timestamp);",
)


def upgrade() -> None:
    op.execute(CREATE_TABLE)
    for statement in CREATE_INDEXES:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the table removes its indexes with it; being explicit about
    # the table alone keeps the rollback a single atomic statement.
    op.execute("DROP TABLE IF EXISTS samples;")
