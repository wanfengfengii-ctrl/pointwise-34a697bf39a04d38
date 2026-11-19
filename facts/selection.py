from sqlite3 import Connection

from facts.history import snapshot_at
from facts.measurements import Rows
from facts.revisions import require_revision


def as_of(db: Connection, revision: int) -> Rows:
    """Return the complete effective facts at one immutable revision."""
    require_revision(db, revision)
    return snapshot_at(db, revision)
