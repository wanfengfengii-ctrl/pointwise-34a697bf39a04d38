from collections.abc import Callable
from dataclasses import replace
from sqlite3 import Connection

from mirror.install import install_full
from shipment.envelope import open_full
from shipment.payload import FullPayload, decode_full_payload


def validate_full_payload(payload: FullPayload) -> FullPayload:
    if payload.revision == 0 and payload.rows:
        raise ValueError('revision 0 must have empty rows')
    plants = set(payload.binding.plants)
    keys = set()
    for key, image in payload.rows:
        if key in keys:
            raise ValueError(f'duplicate full day key {key}')
        if image.plant not in plants:
            raise ValueError(f'row for {key} is outside the fixed plant scope')
        keys.add(key)
    return replace(payload, rows=tuple(sorted(payload.rows, key=lambda row: row[0])))


def load_full(db: Connection, blob: bytes, *, before_commit: Callable[[], None] | None = None) -> int:
    payload = validate_full_payload(decode_full_payload(open_full(blob)))
    return install_full(db, payload, before_commit=before_commit)
