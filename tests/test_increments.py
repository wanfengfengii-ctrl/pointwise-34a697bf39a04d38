import json

from facts.ledger import source_identity
from facts.measurements import DayKey, Measurement
from intake.batches import accept
from mirror.bindings import read_installed_stream
from mirror.ledger import read_details
from mirror.readback import read_mirror
from mirror.receive import apply_increment, load_full
from shipment.export import export_increment, issue_full
from shipment.envelope import open_increment, seal_increment
from shipment.streams import register_stream
from tests.support import DAY, STREAM, WarehouseCase, change, fail_commit, image

SOURCE = 'increment-source'


def entry(meter, before, after):
    def value(pair):
        return None if pair is None else image(*pair)

    return change(meter, value(before), value(after))


BATCHES = (
    [entry('x', None, ('east', 5)), entry('v', None, ('east', 8)),
     entry('z', None, ('east', 0)), entry('m', None, ('west', 4)),
     entry('w', None, ('west', 1))],
    [entry('x', ('east', 5), ('east', 9)), entry('m', ('west', 4), ('east', 4))],
    [entry('x', ('east', 9), ('east', 5)), entry('m', ('east', 4), ('west', 4)),
     entry('v', ('east', 8), None)],
    [entry('z', ('east', 0), ('east', 0)), entry('w', ('west', 1), ('west', 2))],
    [entry('m', ('west', 4), ('east', 2))],
)

EAST = ({}, {'v': 8, 'x': 5, 'z': 0}, {'m': 4, 'v': 8, 'x': 9, 'z': 0},
        {'x': 5, 'z': 0}, {'x': 5, 'z': 0}, {'m': 2, 'x': 5, 'z': 0})


def rows_of(levels):
    return tuple((DayKey(meter, DAY), Measurement('east', liters)) for meter, liters in sorted(levels.items()))


def increment_body(blob):
    return json.loads(open_increment(blob))


def reseal(body):
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return seal_increment(encoded)


class IncrementSource(WarehouseCase):
    def accepted_source(self, last=5):
        db = self.source(source_id=SOURCE)
        register_stream(db, STREAM, ['east'])
        for revision, batch in enumerate(BATCHES[:last], start=1):
            self.assertEqual(accept(db, f'revision-{revision}', batch).revision, revision)
        return db

    def fulls(self, source, last=5):
        return [issue_full(source, f'full-{revision}', STREAM, revision).blob
                for revision in range(last + 1)]


class IncrementExportTests(IncrementSource):
    def test_v9_envelope_version_kind_and_change_records(self):
        source = self.accepted_source()
        blob = export_increment(source, STREAM, 1, 3)
        envelope = json.loads(blob)
        self.assertEqual((envelope['version'], envelope['kind']), (2, 'increment'))
        body = increment_body(blob)
        self.assertEqual((body['low'], body['high']), (1, 3))
        self.assertEqual(body['binding'], dict(source_id=SOURCE, stream_id=STREAM, plants=['east']))
        self.assertEqual(body['changes'], [
            dict(meter='m', day=DAY, revision=2, after=dict(plant='east', liters=4)),
            dict(meter='m', day=DAY, revision=3, after=None),
            dict(meter='v', day=DAY, revision=3, after=None),
            dict(meter='x', day=DAY, revision=2, after=dict(plant='east', liters=9)),
            dict(meter='x', day=DAY, revision=3, after=dict(plant='east', liters=5)),
        ])

    def test_v9_unchanged_and_out_of_scope_keys_are_omitted(self):
        source = self.accepted_source()
        # Revision 4 only re-reports z within scope and corrects w outside scope.
        self.assertEqual(export_increment(source, STREAM, 3, 4), seal_increment(
            json.dumps(dict(
                binding=dict(source_id=SOURCE, stream_id=STREAM, plants=['east']),
                low=3, high=4, changes=[],
            ), sort_keys=True, separators=(',', ':')).encode('utf-8'),
        ))
        self.assertEqual(increment_body(export_increment(source, STREAM, 3, 4))['changes'], [])
        self.assertEqual(increment_body(export_increment(source, STREAM, 0, 1))['changes'], [
            dict(meter='v', day=DAY, revision=1, after=dict(plant='east', liters=8)),
            dict(meter='x', day=DAY, revision=1, after=dict(plant='east', liters=5)),
            dict(meter='z', day=DAY, revision=1, after=dict(plant='east', liters=0)),
        ])

    def test_v9_bytes_are_stable_after_appends_and_reopen(self):
        source = self.accepted_source(last=3)
        overlap = export_increment(source, STREAM, 1, 3)
        source.close()
        source = self.source(source_id=SOURCE)
        self.assertEqual(source_identity(source), SOURCE)
        self.assertEqual(export_increment(source, STREAM, 1, 3), overlap)
        for revision, batch in enumerate(BATCHES[3:], start=4):
            self.assertEqual(accept(source, f'revision-{revision}', batch).revision, revision)
        source.close()
        source = self.source()
        self.assertEqual(export_increment(source, STREAM, 1, 3), overlap)

    def test_v9_invalid_arguments(self):
        source = self.accepted_source()
        for low, high in ((True, 2), (0, True), (-1, 2), (3, 3), (4, 3), (1.0, 2), (1, None)):
            with self.subTest(low=low, high=high), self.assertRaises(ValueError):
                export_increment(source, STREAM, low, high)
        with self.assertRaises(KeyError):
            export_increment(source, STREAM, 0, 6)
        with self.assertRaises(KeyError):
            export_increment(source, 'missing', 0, 1)


class IncrementInstallTests(IncrementSource):
    def test_v9_overlap_continues_from_either_endpoint_of_the_gap(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        overlap = export_increment(source, STREAM, 1, 3)
        source.close()
        for start in (1, 2):
            with self.subTest(start=start):
                mirror = self.mirror(f'overlap-{start}.sqlite')
                self.assertEqual(load_full(mirror, fulls[start]), start)
                self.assertEqual(apply_increment(mirror, overlap), 3)
                snapshot = read_mirror(mirror, STREAM)
                self.assertEqual((snapshot.revision, snapshot.rows), (3, rows_of(EAST[3])))
                self.assertEqual(read_details(mirror, STREAM), rows_of(EAST[3]))

    def test_v9_long_increment_matches_every_full_tail(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        long_increment = export_increment(source, STREAM, 1, 5)
        from_zero = export_increment(source, STREAM, 0, 5)
        source.close()
        for start in range(5):
            with self.subTest(start=start):
                mirror = self.mirror(f'long-{start}.sqlite')
                load_full(mirror, fulls[start])
                payload_blob = from_zero if start == 0 else long_increment
                self.assertEqual(apply_increment(mirror, payload_blob), 5)
                self.assertEqual(read_mirror(mirror, STREAM).rows, rows_of(EAST[5]))
                self.assertEqual(read_mirror(mirror, STREAM).revision, 5)

    def test_v9_empty_increment_advances_without_touching_rows(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        fill = export_increment(source, STREAM, 2, 3)
        empty = export_increment(source, STREAM, 3, 4)
        source.close()
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, fulls[2]), 2)
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            apply_increment(mirror, empty)
        self.assertEqual(read_mirror(mirror, STREAM).revision, 2)
        self.assertEqual(apply_increment(mirror, fill), 3)
        before = read_details(mirror, STREAM)
        self.assertEqual(apply_increment(mirror, empty), 4)
        self.assertEqual(read_details(mirror, STREAM), before)
        self.assertEqual(read_mirror(mirror, STREAM).revision, 4)

    def test_v9_empty_full_then_increment(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        from_zero = export_increment(source, STREAM, 0, 2)
        source.close()
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, fulls[0]), 0)
        self.assertEqual(read_mirror(mirror, STREAM).rows, ())
        self.assertEqual(apply_increment(mirror, from_zero), 2)
        self.assertEqual(read_mirror(mirror, STREAM).rows, rows_of(EAST[2]))

    def test_v9_gap_is_rejected_until_filled(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        overlap = export_increment(source, STREAM, 1, 3)
        opening = export_increment(source, STREAM, 0, 1)
        source.close()
        mirror = self.mirror()
        load_full(mirror, fulls[0])
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            apply_increment(mirror, overlap)
        installed = read_mirror(mirror, STREAM)
        self.assertEqual((installed.revision, installed.rows), (0, ()))
        self.assertEqual(apply_increment(mirror, opening), 1)
        self.assertEqual(apply_increment(mirror, overlap), 3)
        self.assertEqual(read_mirror(mirror, STREAM).rows, rows_of(EAST[3]))

    def test_v9_valid_old_increment_does_not_advance(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        overlap = export_increment(source, STREAM, 1, 3)
        source.close()
        mirror = self.mirror()
        load_full(mirror, fulls[4])
        installed = read_mirror(mirror, STREAM)
        confirmation = read_installed_stream(mirror, STREAM)
        self.assertEqual(apply_increment(mirror, overlap), 4)
        self.assertEqual(read_mirror(mirror, STREAM), installed)
        self.assertEqual(read_installed_stream(mirror, STREAM), confirmation)

    def test_v9_bad_pieces_are_rejected_even_when_high_has_passed(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        overlap = export_increment(source, STREAM, 1, 3)
        body = increment_body(overlap)
        source.close()
        other = self.source('other.sqlite', source_id='other-source')
        register_stream(other, STREAM, ['east'])
        accept(other, 'one', [change('q', None, image('east', 1))])
        accept(other, 'two', [change('q', image('east', 1), image('east', 2))])
        accept(other, 'three', [change('q', image('east', 2), image('east', 3))])
        foreign = export_increment(other, STREAM, 1, 3)
        other.close()

        def mutate(edit):
            changed = json.loads(json.dumps(body))
            edit(changed)
            return reseal(changed)

        duplicate = mutate(lambda value: value['changes'].append(dict(value['changes'][0])))
        outside = mutate(lambda value: value['changes'][0].update(revision=1))
        beyond = mutate(lambda value: value['changes'][0].update(revision=4))
        scope = mutate(lambda value: value['changes'][0]['after'].update(plant='west'))
        unsorted = mutate(lambda value: value['changes'].reverse())
        boolean = mutate(lambda value: value['changes'][0]['after'].update(liters=True))
        interval = mutate(lambda value: value.update(low=3))
        bad_version = json.loads(overlap)
        bad_version['version'] = 1
        bad_version = json.dumps(bad_version).encode('utf-8')
        checksum = json.loads(overlap)
        checksum['sha256'] = ('1' if checksum['sha256'][0] == '0' else '0') + checksum['sha256'][1:]
        checksum = json.dumps(checksum).encode('utf-8')

        mirror = self.mirror()
        load_full(mirror, fulls[4])
        installed = read_mirror(mirror, STREAM)
        confirmation = read_installed_stream(mirror, STREAM)
        bad_pieces = [
            ('duplicate', duplicate), ('outside', outside), ('beyond', beyond),
            ('scope', scope), ('unsorted', unsorted), ('boolean', boolean),
            ('interval', interval), ('version', bad_version), ('checksum', checksum),
            ('foreign', foreign), ('garbage', b'not json'),
        ]
        for label, blob in bad_pieces:
            with self.subTest(label=label), self.assertRaises(ValueError):
                apply_increment(mirror, blob)
            self.assertEqual(read_mirror(mirror, STREAM), installed)
            self.assertEqual(read_installed_stream(mirror, STREAM), confirmation)

    def test_v9_uninitialized_stream_rejects_even_past_piece(self):
        source = self.accepted_source()
        from_zero = export_increment(source, STREAM, 0, 5)
        source.close()
        mirror = self.mirror()
        with self.assertRaises(ValueError):
            apply_increment(mirror, from_zero)
        with self.assertRaises(KeyError):
            read_mirror(mirror, STREAM)

    def test_v9_commit_failure_keeps_details_and_confirmation(self):
        source = self.accepted_source()
        fulls = self.fulls(source)
        fill = export_increment(source, STREAM, 2, 3)
        source.close()
        path = self.root / 'retry.sqlite'
        mirror = self.mirror('retry.sqlite')
        load_full(mirror, fulls[2])
        staged = []

        def interrupt():
            staged.append((read_installed_stream(mirror, STREAM).revision,
                           read_details(mirror, STREAM)))
            fail_commit()

        with self.assertRaisesRegex(RuntimeError, 'commit interrupted'):
            apply_increment(mirror, fill, before_commit=interrupt)
        self.assertEqual(staged, [(3, rows_of(EAST[3]))])
        mirror.close()
        mirror = self.mirror('retry.sqlite')
        restored = read_mirror(mirror, STREAM)
        self.assertEqual((restored.revision, restored.rows), (2, rows_of(EAST[2])))
        self.assertEqual(apply_increment(mirror, fill), 3)
        self.assertEqual(read_mirror(mirror, STREAM).rows, rows_of(EAST[3]))
