from dataclasses import dataclass
from sqlite3 import Connection

from mirror.readback import MirrorSnapshot, read_mirror
from report.grouping import group_by_plant
from report.volumes import PlantTotal, total_liters, total_plant


@dataclass(frozen=True)
class Reconciliation:
    snapshot: MirrorSnapshot
    totals: tuple[PlantTotal, ...]


def reconcile_stream(db: Connection, stream_id: str) -> Reconciliation:
    snapshot = read_mirror(db, stream_id)
    totals = tuple(total_plant(plant, images) for plant, images in group_by_plant(snapshot.rows))
    counts_match = sum(total.count for total in totals) == len(snapshot.rows)
    volumes_match = sum(total.liters for total in totals) == total_liters(snapshot.rows)
    if not counts_match or not volumes_match:
        raise ValueError('plant totals do not reconcile with installed details')
    return Reconciliation(snapshot, totals)


def daily_report(db: Connection, stream_id: str) -> tuple[int, tuple[PlantTotal, ...]]:
    report = reconcile_stream(db, stream_id)
    return report.snapshot.revision, report.totals
