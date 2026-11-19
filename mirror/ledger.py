import sqlite3
from contextlib import contextmanager
from os import PathLike

from facts.measurements import DayKey, Measurement, Rows, liters_from_storage, liters_to_storage
from mirror.bindings import init_mirror_bindings


@contextmanager
def _transaction(db: sqlite3.Connection, begin: str):
    db.execute(begin)
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def mirror_write(db: sqlite3.Connection):
    return _transaction(db, 'BEGIN IMMEDIATE')


def mirror_snapshot(db: sqlite3.Connection):
    return _transaction(db, 'BEGIN')


def open_mirror(path: str | PathLike[str]) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with mirror_write(db):
            init_mirror_bindings(db)
            db.execute('''
                CREATE TABLE IF NOT EXISTS mirror_details (
                    stream_id TEXT NOT NULL REFERENCES mirror_streams(stream_id),
                    meter TEXT NOT NULL,
                    day TEXT NOT NULL,
                    plant TEXT NOT NULL,
                    liters TEXT NOT NULL,
                    PRIMARY KEY (stream_id, meter, day)
                ) WITHOUT ROWID
            ''')
    except BaseException:
        db.close()
        raise
    return db


def replace_details(db: sqlite3.Connection, stream_id: str, rows: Rows) -> None:
    db.execute('DELETE FROM mirror_details WHERE stream_id = ?', (stream_id,))
    db.executemany(
        'INSERT INTO mirror_details(stream_id, meter, day, plant, liters) VALUES (?, ?, ?, ?, ?)',
        ((stream_id, key.meter, key.day, image.plant, liters_to_storage(image.liters)) for key, image in rows),
    )


def read_details(db: sqlite3.Connection, stream_id: str) -> Rows:
    rows = db.execute(
        '''SELECT meter, day, plant, liters FROM mirror_details
           WHERE stream_id = ? ORDER BY meter, day''', (stream_id,),
    )
    return tuple((DayKey(meter, day), Measurement(plant, liters_from_storage(liters)))
                 for meter, day, plant, liters in rows)
