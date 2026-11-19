from sqlite3 import Connection


def init_revisions(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS revisions (
            revision INTEGER PRIMARY KEY CHECK (revision > 0)
        )
    ''')


def validate_revision_number(revision: object) -> int:
    if type(revision) is not int or revision < 0:
        raise ValueError('revision must be a nonnegative integer')
    return revision


def revision_head(db: Connection) -> int:
    return db.execute('SELECT COALESCE(MAX(revision), 0) FROM revisions').fetchone()[0]


def require_revision(db: Connection, revision: int) -> None:
    validate_revision_number(revision)
    # Confirmed revisions are contiguous and never removed.
    if revision > revision_head(db):
        raise KeyError(f'unknown revision {revision!r}')


def append_revision(db: Connection) -> int:
    revision = revision_head(db) + 1
    db.execute('INSERT INTO revisions(revision) VALUES (?)', (revision,))
    return revision
