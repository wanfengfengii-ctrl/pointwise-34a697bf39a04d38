from dataclasses import dataclass
from sqlite3 import Connection

from facts.measurements import validate_identifier
from shipment.streams import StreamBinding, get_stream


@dataclass(frozen=True)
class PublicationReceipt:
    publication_id: str
    binding: StreamBinding
    revision: int
    row_count: int


@dataclass(frozen=True)
class PublishedFull:
    receipt: PublicationReceipt
    blob: bytes


def init_catalog(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS full_publications (
            publication_id TEXT PRIMARY KEY NOT NULL,
            stream_id TEXT NOT NULL REFERENCES report_streams(stream_id),
            revision INTEGER NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            blob BLOB NOT NULL
        )
    ''')


def find_publication(db: Connection, publication_id: str) -> PublishedFull | None:
    row = db.execute(
        '''SELECT stream_id, revision, row_count, blob FROM full_publications
           WHERE publication_id = ?''', (publication_id,),
    ).fetchone()
    if row is None:
        return None
    stream_id, revision, row_count, blob = row
    receipt = PublicationReceipt(publication_id, get_stream(db, stream_id), revision, row_count)
    return PublishedFull(receipt, blob)


def save_publication(db: Connection, publication: PublishedFull) -> None:
    receipt = publication.receipt
    db.execute(
        '''INSERT INTO full_publications(publication_id, stream_id, revision, row_count, blob)
           VALUES (?, ?, ?, ?, ?)''',
        (receipt.publication_id, receipt.binding.stream_id, receipt.revision, receipt.row_count, publication.blob),
    )


def get_full(db: Connection, publication_id: str) -> bytes:
    publication_id = validate_identifier(publication_id, 'publication_id')
    row = db.execute(
        'SELECT blob FROM full_publications WHERE publication_id = ?', (publication_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f'unknown publication {publication_id!r}')
    return row[0]
