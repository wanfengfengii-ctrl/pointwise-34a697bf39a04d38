from facts.ledger import source_identity
from intake.batches import accept
from shipment.catalog import get_full
from shipment.export import issue_full
from shipment.streams import get_stream, register_stream
from tests.support import DAY, SAMPLES, STREAM, WarehouseCase, change, decode_body, fail_commit, image


class ShipmentTests(WarehouseCase):
    def test_v3_original_bytes_survive_later_edits_and_reopen(self):
        db = self.source()
        accept(db, 'opening', [change('x', None, image('east', 5))])
        register_stream(db, STREAM, ['east'])
        first = issue_full(db, 'before-move', STREAM, 1)
        accept(db, 'move', [change('x', image('east', 5), image('west', 5))])
        db.close()
        db = self.source()
        self.assertEqual(get_full(db, 'before-move'), first.blob)
        self.assertEqual(issue_full(db, 'before-move', STREAM, 1), first)
        self.assertEqual(decode_body(first.blob)['rows'], [dict(meter='x', day=DAY, plant='east', liters=5)])
        self.assertEqual(decode_body(issue_full(db, 'after-move', STREAM, 2).blob)['rows'], [])

    def test_v3_historical_scope_when_both_publications_are_new(self):
        db = self.source()
        accept(db, 'opening', [change('x', None, image('east', 5))])
        accept(db, 'move', [change('x', image('east', 5), image('west', 5))])
        register_stream(db, STREAM, ['east'])
        db.close()
        db = self.source()
        issue_full(db, 'historical', STREAM, 1)
        issue_full(db, 'latest', STREAM, 2)
        historical = decode_body(get_full(db, 'historical'))
        latest = decode_body(get_full(db, 'latest'))
        self.assertEqual((historical['revision'], latest['revision']), (1, 2))
        self.assertEqual(historical['rows'], [dict(meter='x', day=DAY, plant='east', liters=5)])
        self.assertEqual(latest['rows'], [])

    def test_v3_fixed_binding_and_source_identity(self):
        db = self.source(source_id='persisted-source')
        binding = register_stream(db, STREAM, ['west', 'east', 'west'])
        self.assertEqual(binding.plants, ('east', 'west'))
        self.assertEqual(binding.source_id, 'persisted-source')
        self.assertEqual(register_stream(db, STREAM, {'east', 'west'}), binding)
        for plants in ([], ['  '], ['east', 7], 'east', ['east']):
            with self.subTest(plants=plants), self.assertRaises(ValueError):
                register_stream(db, STREAM, plants)
        db.close()
        db = self.source()
        self.assertEqual(source_identity(db), 'persisted-source')
        self.assertEqual(get_stream(db, STREAM), binding)
        self.assertEqual(register_stream(db, STREAM, ['east', 'west']), binding)
        with self.assertRaisesRegex(ValueError, 'source identity'):
            self.source(source_id='another-source')

    def test_v3_publication_identity_and_empty_revision(self):
        db = self.source()
        binding = register_stream(db, STREAM, ['east'])
        register_stream(db, 'west-daily', ['west'])
        accept(db, 'opening', [change('x', None, image('east', 5))])
        publication = issue_full(db, 'empty', STREAM, 0)
        self.assertEqual(publication.receipt.binding, binding)
        self.assertEqual(publication.receipt.row_count, 0)
        self.assertEqual(decode_body(publication.blob)['rows'], [])
        self.assertEqual(decode_body(publication.blob)['revision'], 0)
        self.assertEqual(issue_full(db, 'empty', STREAM, 0), publication)
        self.assertEqual(get_full(db, 'empty'), publication.blob)
        for stream_id, revision in ((STREAM, 1), ('west-daily', 0)):
            with self.subTest(stream_id=stream_id, revision=revision), self.assertRaisesRegex(ValueError, 'publication_id'):
                issue_full(db, 'empty', stream_id, revision)
        with self.assertRaisesRegex(KeyError, 'unknown stream'):
            issue_full(db, 'missing-stream', 'missing', 0)
        with self.assertRaisesRegex(KeyError, 'unknown revision'):
            issue_full(db, 'missing-revision', STREAM, 2)
        for publication_id in ('missing-stream', 'missing-revision', 'missing'):
            with self.subTest(publication_id=publication_id), self.assertRaises(KeyError):
                get_full(db, publication_id)
        self.assertEqual(get_full(db, 'empty'), publication.blob)

    def test_v3_frozen_v1_publication_bytes(self):
        db = self.sample_source()
        publication = issue_full(db, 'day-close', STREAM, 3)
        self.assertEqual(publication.receipt.row_count, 2)
        self.assertEqual(get_full(db, 'day-close'), (SAMPLES / 'full-v1.json').read_bytes())

    def test_v4_failed_commit_leaves_publication_available_for_retry(self):
        db = self.source()
        register_stream(db, STREAM, ['east'])
        accept(db, 'opening', [change('x', None, image('east', 5))])
        observed = []

        def interrupt_after_catalog_write():
            observed.append(get_full(db, 'delayed'))
            fail_commit()

        with self.assertRaisesRegex(RuntimeError, 'commit interrupted'):
            issue_full(db, 'delayed', STREAM, 1, before_commit=interrupt_after_catalog_write)
        self.assertEqual(len(observed), 1)
        db.close()
        db = self.source()
        with self.assertRaisesRegex(KeyError, 'unknown publication'):
            get_full(db, 'delayed')
        publication = issue_full(db, 'delayed', STREAM, 1)
        self.assertEqual(publication.receipt.publication_id, 'delayed')
        self.assertEqual(publication.receipt.revision, 1)
        self.assertEqual(publication.receipt.row_count, 1)
        self.assertEqual(get_full(db, 'delayed'), observed[0])
        self.assertEqual(issue_full(db, 'delayed', STREAM, 1), publication)
