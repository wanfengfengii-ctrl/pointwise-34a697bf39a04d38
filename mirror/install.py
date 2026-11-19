from collections.abc import Callable
from sqlite3 import Connection

from mirror.bindings import read_installed_stream, save_confirmation
from mirror.ledger import mirror_write, read_details, replace_details
from shipment.payload import FullPayload, IncrementPayload


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


def apply_changes(rows, payload: IncrementPayload):
    """Return the details at ``payload.high`` given details already at ``payload.low``."""
    for change in payload.changes:
        if change.after is None:
            rows.pop(change.key, None)
        else:
            rows[change.key] = change.after
    return tuple(sorted(rows.items()))


def install_increment(
    db: Connection, payload: IncrementPayload, *, before_commit: Callable[[], None] | None = None,
) -> int:
    """Apply a fully validated increment from the confirmed revision ``low`` or above."""
    stream_id = payload.binding.stream_id
    with mirror_write(db):
        prior = read_installed_stream(db, stream_id)
        if prior is None:
            raise ValueError('increment requires an installed full delivery')
        if prior.binding != payload.binding:
            raise ValueError('increment delivery has a different stream binding')
        if prior.revision < payload.low:
            raise ValueError('increment does not cover the installed revision')
        if prior.revision >= payload.high:
            return prior.revision

        rows = dict(read_details(db, stream_id))
        applicable = IncrementPayload(
            payload.binding, prior.revision, payload.high,
            tuple(change for change in payload.changes if change.revision > prior.revision),
        )
        updated = apply_changes(rows, applicable)
        save_confirmation(db, payload.binding, payload.high)
        replace_details(db, stream_id, updated)
        if before_commit is not None:
            before_commit()
    return payload.high
