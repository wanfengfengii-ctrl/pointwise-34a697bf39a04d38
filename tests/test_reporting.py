import json
import sqlite3

from facts.ledger import read_current
from facts.measurements import read_json
from facts.selection import as_of
from intake.batches import accept
from mirror.readback import read_mirror
from mirror.receive import load_full
from report.reconcile import daily_report, reconcile_stream
from report.render import render_reconciliation
from report.volumes import PlantTotal
from shipment.export import issue_full
from shipment.streams import register_stream
from tests.support import DAY, SAMPLES, STREAM, WarehouseCase, change, image, row


class ReportingTests(WarehouseCase):
    def test_v8_zero_counts_and_withdrawn_plant_is_absent(self):
        source = self.sample_source()
        register_stream(source, 'all-plants', ['west', 'east'])
        frozen_scope = issue_full(source, 'day-close', STREAM, 3).blob
        accept(source, 'west-withdrawal', [change('x', image('west', 5), None)])
        both_plants = issue_full(source, 'after-withdrawal', 'all-plants', 4).blob
        source.close()
        mirror = self.mirror()
        load_full(mirror, frozen_scope)
        load_full(mirror, both_plants)
        self.assertEqual(daily_report(mirror, STREAM), (3, (PlantTotal('east', 2, 7),)))
        self.assertEqual(daily_report(mirror, 'all-plants'), (4, (PlantTotal('east', 2, 7),)))
        rendered = json.loads(render_reconciliation(reconcile_stream(mirror, STREAM)))
        self.assertEqual(rendered, json.loads((SAMPLES / 'report.json').read_text(encoding='utf-8')))
        after = read_mirror(mirror, 'all-plants')
        self.assertEqual(after.rows, (row('y', 'east', 7), row('z', 'east', 0)))

    def test_v8_exact_rollup_and_sorted_output(self):
        source = self.source()
        register_stream(source, STREAM, ['west', 'east'])
        volume = 2**62 + 12
        entries = [change('z', None, image('west', 4)), change('m', None, image('east', volume)),
                   change('a', None, image('east', volume)), change('b', None, image('west', 0))]
        accept(source, 'unordered', entries)
        blob = issue_full(source, 'both', STREAM, 1).blob
        source.close()
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, blob), 1)
        self.assertEqual(daily_report(mirror, STREAM), (1, (PlantTotal('east', 2, 2**63 + 24), PlantTotal('west', 2, 4))))
        actual = json.loads(render_reconciliation(reconcile_stream(mirror, STREAM)))
        self.assertEqual(actual, dict(
            revision=1,
            rows=[dict(meter=meter, day=DAY, plant=plant, liters=liters) for meter, plant, liters in
                  [('a', 'east', volume), ('b', 'west', 0), ('m', 'east', volume), ('z', 'west', 4)]],
            totals=[dict(plant='east', count=2, liters=2**63 + 24), dict(plant='west', count=2, liters=4)],
        ))

    def test_v8_large_individual_integer_survives_all_boundaries(self):
        source = self.source()
        register_stream(source, STREAM, ['east'])
        huge = 10**4400 + 41
        original = [change('p', None, image('east', huge)), change('n', None, image('east', -huge + 3))]
        receipt = accept(source, 'large', original)
        self.assertEqual(accept(source, 'large', reversed(original)), receipt)
        expected = (row('n', 'east', -huge + 3), row('p', 'east', huge))
        self.assertEqual(read_current(source, [key for key, _ in expected]), dict(expected))
        self.assertEqual(as_of(source, 1), expected)
        blob = issue_full(source, 'large', STREAM, 1).blob
        source.close()
        mirror = self.mirror()
        self.assertEqual(load_full(mirror, blob), 1)
        self.assertEqual(read_mirror(mirror, STREAM).rows, expected)
        self.assertEqual(daily_report(mirror, STREAM), (1, (PlantTotal('east', 2, 3),)))
        report = read_json(render_reconciliation(reconcile_stream(mirror, STREAM)).encode('utf-8'))
        self.assertEqual(report['rows'][1]['liters'], huge)
        self.assertEqual(report['totals'], [dict(plant='east', count=2, liters=3)])

    def test_v8_empty_and_readback_errors(self):
        source = self.source()
        register_stream(source, STREAM, ['east'])
        accept(source, 'empty', [])
        blob = issue_full(source, 'empty', STREAM, 1).blob
        source.close()
        mirror = self.mirror()
        with self.assertRaisesRegex(KeyError, 'uninstalled stream'):
            daily_report(mirror, STREAM)
        load_full(mirror, blob)
        self.assertEqual(daily_report(mirror, STREAM), (1, ()))
        self.assertEqual(json.loads(render_reconciliation(reconcile_stream(mirror, STREAM))),
                         dict(revision=1, rows=[], totals=[]))
        mirror.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            daily_report(mirror, STREAM)

    def test_v8_single_snapshot_survives_next_install(self):
        source = self.sample_source()
        initial = issue_full(source, 'first', STREAM, 1).blob
        later = issue_full(source, 'second', STREAM, 2).blob
        source.close()
        writer = self.mirror()
        self.assertEqual(writer.execute('PRAGMA journal_mode = WAL').fetchone()[0], 'wal')
        load_full(writer, initial)
        reader = self.mirror()
        commits = []
        updated = []
        errors = []

        def install_at_read_commit(statement):
            if statement.strip().upper() != 'COMMIT':
                return
            commits.append(statement)
            if len(commits) == 1:
                try:
                    updated.append(load_full(writer, later))
                except Exception as exc:
                    errors.append(exc)

        reader.set_trace_callback(install_at_read_commit)
        try:
            report = reconcile_stream(reader, STREAM)
        finally:
            reader.set_trace_callback(None)
        self.assertEqual(errors, [])
        self.assertEqual(updated, [2])
        self.assertEqual(len(commits), 1)
        self.assertEqual(report.snapshot.revision, 1)
        self.assertEqual(report.snapshot.rows, (row('x', 'east', 5), row('y', 'east', 7), row('z', 'east', 0)))
        self.assertEqual(report.totals, (PlantTotal('east', 3, 12),))
        self.assertEqual(daily_report(reader, STREAM), (2, (PlantTotal('east', 3, 16),)))
