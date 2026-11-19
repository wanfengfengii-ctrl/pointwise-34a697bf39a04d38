from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class Receipt:
    request_id: str
    revision: int


def init_receipts(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS intake_receipts (
            request_id TEXT PRIMARY KEY NOT NULL,
            content TEXT NOT NULL,
            revision INTEGER NOT NULL UNIQUE REFERENCES revisions(revision)
        )
    ''')


def find_receipt(db: Connection, request_id: str) -> tuple[Receipt, str] | None:
    row = db.execute(
        'SELECT revision, content FROM intake_receipts WHERE request_id = ?', (request_id,),
    ).fetchone()
    if row is None:
        return None
    revision, content = row
    return Receipt(request_id, revision), content


def save_receipt(db: Connection, receipt: Receipt, content: str) -> None:
    db.execute(
        'INSERT INTO intake_receipts(request_id, content, revision) VALUES (?, ?, ?)',
        (receipt.request_id, content, receipt.revision),
    )
