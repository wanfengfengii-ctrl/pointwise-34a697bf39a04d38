import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from os import PathLike
from uuid import uuid4

from facts.history import init_history
from facts.measurements import (
    DayKey, Measurement, liters_from_storage, liters_to_storage, validate_identifier,
)
from facts.revisions import init_revisions


@contextmanager
def source_write(db: sqlite3.Connection):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _init_facts(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS source_meta (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            source_id TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS current_facts (
            meter TEXT NOT NULL,
            day TEXT NOT NULL,
            plant TEXT NOT NULL,
            liters TEXT NOT NULL,
            PRIMARY KEY (meter, day)
        ) WITHOUT ROWID
    ''')
    init_revisions(db)
    init_history(db)


def open_source(path: str | PathLike[str], *, source_id: str | None = None) -> sqlite3.Connection:
    # These schemas are owned by the participants in the source transaction.
    from intake.receipts import init_receipts
    from shipment.catalog import init_catalog
    from shipment.streams import init_streams

    if source_id is not None:
        validate_identifier(source_id, 'source_id')
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with source_write(db):
            _init_facts(db)
            init_receipts(db)
            init_streams(db)
            init_catalog(db)
            stored = db.execute('SELECT source_id FROM source_meta WHERE singleton = 1').fetchone()
            if stored is None:
                identity = source_id if source_id is not None else uuid4().hex
                db.execute('INSERT INTO source_meta VALUES (1, ?)', (identity,))
            elif source_id is not None and stored[0] != source_id:
                raise ValueError('source identity conflicts with the existing warehouse')
    except BaseException:
        db.close()
        raise
    return db


def source_identity(db: sqlite3.Connection) -> str:
    row = db.execute('SELECT source_id FROM source_meta WHERE singleton = 1').fetchone()
    if row is None:
        raise ValueError('source warehouse has no identity')
    return row[0]


def read_current(db: sqlite3.Connection, keys: Iterable[DayKey]) -> dict[DayKey, Measurement]:
    result = {}
    for key in keys:
        row = db.execute(
            'SELECT plant, liters FROM current_facts WHERE meter = ? AND day = ?',
            (key.meter, key.day),
        ).fetchone()
        if row is not None:
            result[key] = Measurement(row[0], liters_from_storage(row[1]))
    return result


def update_current(db: sqlite3.Connection, updates: Mapping[DayKey, Measurement | None]) -> None:
    db.executemany(
        'DELETE FROM current_facts WHERE meter = ? AND day = ?',
        ((key.meter, key.day) for key, image in updates.items() if image is None),
    )
    db.executemany(
        '''INSERT INTO current_facts (meter, day, plant, liters) VALUES (?, ?, ?, ?)
           ON CONFLICT (meter, day) DO UPDATE SET plant = excluded.plant, liters = excluded.liters''',
        (
            (key.meter, key.day, image.plant, liters_to_storage(image.liters))
            for key, image in updates.items() if image is not None
        ),
    )
