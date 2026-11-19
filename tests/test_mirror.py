import json

from intake.batches import accept
from mirror.bindings import read_installed_stream
from mirror.ledger import read_details
from mirror.readback import read_mirror
from mirror.receive import load_full
from shipment.catalog import get_full
from shipment.export import issue_full
from shipment.streams import register_stream
from tests.support import SAMPLES, STREAM, WarehouseCase, change, fail_commit, image, replace_body, row


class MirrorTests(WarehouseCase):
    def test_v5_offline_install_reopen_and_replay(self):
        source = self.sample_source()
        issued = issue_full(source, 'day-close', STREAM, 3)
        blob = get_full(source, 'day-close')
        source.close()
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, blob), 3)
        installed = read_mirror(mirror, STREAM)
        self.assertEqual(installed.binding, issued.receipt.binding)
        self.assertEqual(installed.revision, 3)
        self.assertEqual(installed.rows, (row('y', 'east', 7), row('z', 'east', 0)))
        mirror.close()
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, blob), 3)
        self.assertEqual(read_mirror(mirror, STREAM), installed)

        frozen_only = self.mirror('frozen-only.sqlite')
        self.assertEqual(load_full(frozen_only, (SAMPLES / 'full-v1.json').read_bytes()), 3)
        self.assertEqual(read_mirror(frozen_only, STREAM), installed)

    def test_v5_replace_missing_rows_including_empty_update(self):
        source = self.source()
        register_stream(source, STREAM, ['east'])
        accept(source, 'first', [change('x', None, image('east', 5)), change('z', None, image('east', 0))])
        accept(source, 'remove-x', [change('x', image('east', 5), None)])
        accept(source, 'remove-z', [change('z', image('east', 0), None)])
        blobs = [issue_full(source, f'revision-{r}', STREAM, r).blob for r in (1, 2, 3)]
        source.close()
        mirror = self.mirror()
        expected = [(row('x', 'east', 5), row('z', 'east', 0)), (row('z', 'east', 0),), ()]
        for revision, (blob, rows) in enumerate(zip(blobs, expected), start=1):
            self.assertEqual(load_full(mirror, blob), revision)
            snapshot = read_mirror(mirror, STREAM)
            self.assertEqual((snapshot.revision, snapshot.rows), (revision, rows))
        mirror.close()
        snapshot = read_mirror(self.mirror(), STREAM)
        self.assertEqual((snapshot.revision, snapshot.rows), (3, ()))

    def test_v5_same_revision_conflict_uses_rows_not_publication(self):
        source = self.sample_source()
        current = issue_full(source, 'current', STREAM, 1).blob
        duplicate = issue_full(source, 'same-content', STREAM, 1).blob
        source.close()
        conflict = replace_body(duplicate, lambda body: body['rows'][0].update(liters=77))
        reordered = replace_body(duplicate, lambda body: body['rows'].reverse())
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, current), 1)
        installed = read_mirror(mirror, STREAM)
        self.assertEqual(load_full(mirror, duplicate), 1)
        self.assertEqual(load_full(mirror, reordered), 1)
        with self.assertRaisesRegex(ValueError, 'conflicts at the installed revision'):
            load_full(mirror, conflict)
        self.assertEqual(read_mirror(mirror, STREAM), installed)

    def test_v5_install_commit_failure_and_retry(self):
        source = self.sample_source()
        current = issue_full(source, 'current', STREAM, 2).blob
        later = issue_full(source, 'later', STREAM, 3).blob
        source.close()
        mirror = self.mirror()
        load_full(mirror, current)
        installed = read_mirror(mirror, STREAM)
        staged = []

        def interrupt_after_install_write():
            staged.append((read_installed_stream(mirror, STREAM).revision, read_details(mirror, STREAM)))
            fail_commit()

        with self.assertRaisesRegex(RuntimeError, 'commit interrupted'):
            load_full(mirror, later, before_commit=interrupt_after_install_write)
        rows = (row('y', 'east', 7), row('z', 'east', 0))
        self.assertEqual(staged, [(3, rows)])
        mirror.close()
        mirror = self.mirror()
        self.assertEqual(read_mirror(mirror, STREAM), installed)
        self.assertEqual(load_full(mirror, later), 3)
        new = read_mirror(mirror, STREAM)
        self.assertEqual((new.revision, new.rows), (3, rows))

    def test_v5_failed_first_install_leaves_stream_uninitialized(self):
        source = self.sample_source()
        blob = issue_full(source, 'first', STREAM, 1).blob
        source.close()
        mirror = self.mirror()
        with self.assertRaisesRegex(RuntimeError, 'commit interrupted'):
            load_full(mirror, blob, before_commit=fail_commit)
        mirror.close()
        mirror = self.mirror()
        with self.assertRaisesRegex(KeyError, 'uninstalled stream'):
            read_mirror(mirror, STREAM)
        self.assertEqual(read_details(mirror, STREAM), ())
        self.assertEqual(load_full(mirror, blob), 1)
        self.assertEqual(read_mirror(mirror, STREAM).rows,
                         (row('x', 'east', 5), row('y', 'east', 7), row('z', 'east', 0)))

    def test_v6_changed_body_with_original_digest_is_rejected(self):
        source = self.sample_source()
        first = issue_full(source, 'first', STREAM, 1).blob
        later = issue_full(source, 'later', STREAM, 2).blob
        source.close()
        damaged = replace_body(later, lambda body: body['rows'][0].update(liters=81), refresh_digest=False)
        mirror = self.mirror()
        load_full(mirror, first)
        installed = read_mirror(mirror, STREAM)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            load_full(mirror, damaged)
        mirror.close()
        mirror = self.mirror()
        self.assertEqual(read_mirror(mirror, STREAM), installed)
        self.assertEqual(load_full(mirror, later), 2)
        self.assertEqual(read_mirror(mirror, STREAM).rows[0], row('x', 'east', 9))

    def test_v6_foreign_source_and_changed_scope_keep_original_binding(self):
        source = self.sample_source()
        first = issue_full(source, 'first', STREAM, 1).blob
        later = issue_full(source, 'later', STREAM, 2).blob
        source.close()
        other = self.source('other-source.sqlite', source_id='other')
        register_stream(other, STREAM, ['east'])
        accept(other, 'opening', [change('other', None, image('east', 99))])
        accept(other, 'confirmed', [])
        foreign = issue_full(other, 'foreign', STREAM, 2).blob
        other.close()
        changed_scope = replace_body(later, lambda body: body['binding'].update(plants=['east', 'west']))
        mirror = self.mirror()
        load_full(mirror, first)
        installed = read_mirror(mirror, STREAM)
        for label, blob in (('source', foreign), ('scope', changed_scope)):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'different stream binding'):
                load_full(mirror, blob)
            self.assertEqual(read_mirror(mirror, STREAM), installed)
        mirror.close()
        mirror = self.mirror()
        self.assertEqual(read_mirror(mirror, STREAM), installed)
        self.assertEqual(load_full(mirror, later), 2)
        self.assertEqual(read_mirror(mirror, STREAM).binding, installed.binding)

    def test_v6_invalid_rows_and_version_are_rejected(self):
        source = self.sample_source()
        first = issue_full(source, 'first', STREAM, 1).blob
        later = issue_full(source, 'later', STREAM, 2).blob
        source.close()
        unsupported = json.loads(later)
        unsupported['version'] = 2
        invalid = [
            ('duplicate', replace_body(later, lambda body: body['rows'].append(body['rows'][0])), 'duplicate'),
            ('scope', replace_body(later, lambda body: body['rows'][0].update(plant='west')), 'outside'),
            ('liters', replace_body(later, lambda body: body['rows'][0].update(liters=True)), 'integer'),
            ('date', replace_body(later, lambda body: body['rows'][0].update(day='2026-02-30')), 'date'),
            ('zero', replace_body(later, lambda body: body.update(revision=0)), 'revision 0'),
            ('version', json.dumps(unsupported).encode('utf-8'), 'version'),
        ]
        mirror = self.mirror()
        load_full(mirror, first)
        installed = read_mirror(mirror, STREAM)
        for label, blob, error in invalid:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, error):
                    load_full(mirror, blob)
                self.assertEqual(read_mirror(mirror, STREAM), installed)

    def test_v7_another_publication_of_old_revision_is_rejected(self):
        source = self.sample_source()
        r2 = issue_full(source, 'midday', STREAM, 2).blob
        r3 = issue_full(source, 'closing', STREAM, 3).blob
        late_r2 = issue_full(source, 'late-midday', STREAM, 2).blob
        source.close()
        self.assertNotEqual(r2, late_r2)
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, r2), 2)
        self.assertEqual(load_full(mirror, r3), 3)
        with self.assertRaisesRegex(ValueError, 'older than the installed revision'):
            load_full(mirror, late_r2)
        mirror.close()
        end = read_mirror(self.mirror(), STREAM)
        self.assertEqual(end.revision, 3)
        self.assertEqual(end.rows, (row('y', 'east', 7), row('z', 'east', 0)))
