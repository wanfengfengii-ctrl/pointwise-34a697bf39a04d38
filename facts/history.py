from collections.abc import Iterable
from sqlite3 import Connection

from facts.measurements import DayKey, Measurement, Rows, liters_from_storage, liters_to_storage


ChangeImages = tuple[DayKey, Measurement | None, Measurement | None]


def init_history(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS fact_history (
            revision INTEGER NOT NULL REFERENCES revisions(revision),
            meter TEXT NOT NULL,
            day TEXT NOT NULL,
            old_plant TEXT,
            old_liters TEXT,
            new_plant TEXT,
            new_liters TEXT,
            CHECK ((old_plant IS NULL) = (old_liters IS NULL)),
            CHECK ((new_plant IS NULL) = (new_liters IS NULL)),
            PRIMARY KEY (revision, meter, day)
        ) WITHOUT ROWID
    ''')


def _store_image(image: Measurement | None) -> tuple[str | None, str | None]:
    if image is None:
        return None, None
    return image.plant, liters_to_storage(image.liters)


def record_changes(db: Connection, revision: int, changes: Iterable[ChangeImages]) -> None:
    db.executemany(
        '''INSERT INTO fact_history
           (revision, meter, day, old_plant, old_liters, new_plant, new_liters)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (
            (revision, key.meter, key.day, *_store_image(before), *_store_image(after))
            for key, before, after in changes
        ),
    )


def snapshot_at(db: Connection, revision: int) -> Rows:
    current: dict[DayKey, Measurement] = {}
    changes = db.execute(
        '''SELECT meter, day, new_plant, new_liters FROM fact_history
           WHERE revision <= ? ORDER BY revision, meter, day''',
        (revision,),
    )
    for meter, day, plant, liters in changes:
        key = DayKey(meter, day)
        if plant is None:
            current.pop(key, None)
        else:
            current[key] = Measurement(plant, liters_from_storage(liters))
    return tuple(sorted(current.items()))
