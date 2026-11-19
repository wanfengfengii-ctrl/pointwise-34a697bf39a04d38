from collections.abc import Callable
from sqlite3 import Connection

from mirror.bindings import read_installed_stream, save_confirmation
from mirror.ledger import mirror_write, read_details, replace_details
from shipment.payload import FullPayload


def install_full(
    db: Connection, payload: FullPayload, *, before_commit: Callable[[], None] | None = None,
) -> int:
    """Install a fully validated, sorted payload in the mirror."""
    stream_id = payload.binding.stream_id
    with mirror_write(db):
        prior = read_installed_stream(db, stream_id)
        if prior is not None:
            if prior.binding != payload.binding:
                raise ValueError('full delivery has a different stream binding')
            if payload.revision < prior.revision:
                raise ValueError('full delivery is older than the installed revision')
            if payload.revision == prior.revision:
                if read_details(db, stream_id) != payload.rows:
                    raise ValueError('full delivery conflicts at the installed revision')
                return prior.revision

        save_confirmation(db, payload.binding, payload.revision)
        replace_details(db, stream_id, payload.rows)
        if before_commit is not None:
            before_commit()
    return payload.revision
