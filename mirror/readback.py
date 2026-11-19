from dataclasses import dataclass
from sqlite3 import Connection

from facts.measurements import Rows, validate_identifier
from mirror.bindings import read_installed_stream
from mirror.ledger import mirror_snapshot, read_details
from shipment.streams import StreamBinding


@dataclass(frozen=True)
class MirrorSnapshot:
    binding: StreamBinding
    revision: int
    rows: Rows


def read_mirror(db: Connection, stream_id: str) -> MirrorSnapshot:
    stream_id = validate_identifier(stream_id, 'stream_id')
    with mirror_snapshot(db):
        installed = read_installed_stream(db, stream_id)
        if installed is None:
            raise KeyError(f'uninstalled stream {stream_id!r}')
        rows = read_details(db, stream_id)
        return MirrorSnapshot(installed.binding, installed.revision, rows)
