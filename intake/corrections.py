from sqlite3 import Connection

from facts.ledger import read_current
from intake.observations import ObservationChange


def validate_before_images(db: Connection, changes: tuple[ObservationChange, ...]) -> None:
    before_batch = read_current(db, (change.key for change in changes))
    for change in changes:
        if before_batch.get(change.key) != change.before:
            raise ValueError(f'stale before image for {change.key.meter!r} on {change.key.day}')
