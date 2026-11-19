from dataclasses import dataclass
from sqlite3 import Connection

from facts.measurements import canonical_json, liters_from_storage, liters_to_storage, read_json
from shipment.streams import StreamBinding


@dataclass(frozen=True)
class InstalledStream:
    binding: StreamBinding
    revision: int


def init_mirror_bindings(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS mirror_streams (
            stream_id TEXT PRIMARY KEY NOT NULL,
            source_id TEXT NOT NULL,
            plants TEXT NOT NULL,
            revision_hex TEXT NOT NULL
        )
    ''')


def read_installed_stream(db: Connection, stream_id: str) -> InstalledStream | None:
    row = db.execute(
        'SELECT source_id, plants, revision_hex FROM mirror_streams WHERE stream_id = ?', (stream_id,),
    ).fetchone()
    if row is None:
        return None
    source_id, plants, revision = row
    binding = StreamBinding(source_id, stream_id, tuple(read_json(plants.encode('utf-8'))))
    return InstalledStream(binding, liters_from_storage(revision))


def save_confirmation(db: Connection, binding: StreamBinding, revision: int) -> None:
    db.execute(
        '''INSERT INTO mirror_streams(stream_id, source_id, plants, revision_hex) VALUES (?, ?, ?, ?)
           ON CONFLICT (stream_id) DO UPDATE SET revision_hex = excluded.revision_hex''',
        (binding.stream_id, binding.source_id, canonical_json(binding.plants), liters_to_storage(revision)),
    )
