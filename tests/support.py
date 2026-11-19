import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from facts.ledger import open_source
from facts.measurements import DayKey, Measurement
from intake.batches import accept
from mirror.ledger import open_mirror
from shipment.envelope import open_full, seal_full
from shipment.streams import register_stream


DAY = '2026-06-12'
STREAM = 'east-daily'
SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


def image(plant, liters):
    return dict(plant=plant, liters=liters)


def change(meter, before, after, day=DAY):
    return dict(meter=meter, day=day, before=before, after=after)


def row(meter, plant, liters, day=DAY):
    return DayKey(meter, day), Measurement(plant, liters)


def decode_body(blob):
    return json.loads(open_full(blob))


def replace_body(blob, edit, *, refresh_digest=True):
    body = decode_body(blob)
    edit(body)
    body_bytes = json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if refresh_digest:
        return seal_full(body_bytes)
    envelope = json.loads(blob)
    envelope['body'] = base64.b64encode(body_bytes).decode('ascii')
    return json.dumps(envelope).encode('utf-8')


def fail_commit():
    raise RuntimeError('commit interrupted')


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='qingting-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def source(self, filename='source.sqlite', *, source_id=None):
        db = open_source(self.root / filename, source_id=source_id)
        self.addCleanup(db.close)
        return db

    def mirror(self, filename='mirror.sqlite'):
        db = open_mirror(self.root / filename)
        self.addCleanup(db.close)
        return db

    def sample_source(self):
        db = self.source(source_id='qingting-demo')
        batches = json.loads((SAMPLES / 'batches.json').read_text(encoding='utf-8'))
        for revision, batch in enumerate(batches, start=1):
            self.assertEqual(accept(db, **batch).revision, revision)
        register_stream(db, STREAM, ['east'])
        return db
