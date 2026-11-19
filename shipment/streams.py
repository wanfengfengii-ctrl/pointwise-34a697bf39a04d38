from dataclasses import dataclass
from sqlite3 import Connection

from facts.ledger import source_identity, source_write
from facts.measurements import canonical_json, items, read_json, validate_identifier


@dataclass(frozen=True)
class StreamBinding:
    source_id: str
    stream_id: str
    plants: tuple[str, ...]


def init_streams(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS report_streams (
            stream_id TEXT PRIMARY KEY NOT NULL,
            plants TEXT NOT NULL
        )
    ''')


def normalize_plants(plants) -> tuple[str, ...]:
    values = {validate_identifier(plant, 'plant') for plant in items(plants, 'plants')}
    if not values:
        raise ValueError('plants must contain at least one plant')
    return tuple(sorted(values))


def _find_stream(db: Connection, stream_id: str) -> StreamBinding | None:
    row = db.execute('SELECT plants FROM report_streams WHERE stream_id = ?', (stream_id,)).fetchone()
    if row is None:
        return None
    return StreamBinding(source_identity(db), stream_id, tuple(read_json(row[0].encode('utf-8'))))


def register_stream(db: Connection, stream_id: str, plants) -> StreamBinding:
    stream_id = validate_identifier(stream_id, 'stream_id')
    scope = normalize_plants(plants)
    with source_write(db):
        existing = _find_stream(db, stream_id)
        if existing is not None:
            if existing.plants != scope:
                raise ValueError('stream_id already binds a different plant set')
            return existing
        binding = StreamBinding(source_identity(db), stream_id, scope)
        db.execute('INSERT INTO report_streams VALUES (?, ?)', (stream_id, canonical_json(scope)))
    return binding


def get_stream(db: Connection, stream_id: str) -> StreamBinding:
    stream_id = validate_identifier(stream_id, 'stream_id')
    binding = _find_stream(db, stream_id)
    if binding is None:
        raise KeyError(f'unknown stream {stream_id!r}')
    return binding
