from collections.abc import Callable
from sqlite3 import Connection

from facts.history import record_changes
from facts.ledger import source_write, update_current
from facts.revisions import append_revision
from intake.corrections import validate_before_images
from intake.receipts import Receipt, find_receipt, save_receipt
from intake.request import normalize_request


def accept(db: Connection, request_id: str, entries, *, before_commit: Callable[[], None] | None = None) -> Receipt:
    batch = normalize_request(request_id, entries)
    with source_write(db):
        prior = find_receipt(db, batch.request_id)
        if prior is not None:
            receipt, content = prior
            if content != batch.content:
                raise ValueError('request_id already binds different content')
            return receipt

        validate_before_images(db, batch.changes)
        revision = append_revision(db)
        record_changes(db, revision, ((item.key, item.before, item.after) for item in batch.changes))
        update_current(db, {item.key: item.after for item in batch.changes})
        receipt = Receipt(batch.request_id, revision)
        save_receipt(db, receipt, batch.content)
        if before_commit is not None:
            before_commit()
    return receipt
