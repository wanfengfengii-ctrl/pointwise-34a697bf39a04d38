from facts.ledger import read_current, source_identity
from facts.revisions import revision_head
from facts.selection import as_of
from intake.batches import accept
from intake.receipts import Receipt, find_receipt
from tests.support import WarehouseCase, change, fail_commit, image, row


class IntakeTests(WarehouseCase):
    def test_v1_replay_and_history(self):
        db = self.source()
        original = [change('x', None, image('east', 5))]
        self.assertEqual(accept(db, 'opening', original), Receipt('opening', 1))
        self.assertEqual(accept(db, 'opening', original), Receipt('opening', 1))
        self.assertEqual(accept(db, 'edit', [change('x', image('east', 5), image('east', 9))]).revision, 2)
        self.assertEqual(as_of(db, 1), (row('x', 'east', 5),))
        self.assertEqual(as_of(db, 2), (row('x', 'east', 9),))
        self.assertEqual(accept(db, 'return', [change('x', image('east', 9), image('east', 5))]).revision, 3)
        self.assertEqual(as_of(db, 3), (row('x', 'east', 5),))
        self.assertEqual(as_of(db, 2), (row('x', 'east', 9),))

    def test_v1_replay_after_edit_and_reopen(self):
        db = self.source()
        original = [change('y', None, image('east', 2)), change('x', None, image('east', 5))]
        first = accept(db, 'opening', original)
        accept(db, 'edit', [change('x', image('east', 5), image('east', 9))])
        identity = source_identity(db)
        db.close()
        db = self.source()
        self.assertEqual(source_identity(db), identity)
        self.assertEqual(accept(db, 'opening', reversed(original)), first)
        self.assertEqual(first.revision, 1)
        self.assertEqual(revision_head(db), 2)
        self.assertEqual(as_of(db, 2), (row('x', 'east', 9), row('y', 'east', 2)))

    def test_v1_request_conflict_includes_both_images(self):
        db = self.source()
        accept(db, 'opening', [change('x', None, image('east', 5))])
        edit = accept(db, 'edit', [change('x', image('east', 5), image('east', 9))])
        conflicts = [
            change('x', image('west', 5), image('east', 9)),
            change('x', image('east', 5), image('east', 10)),
        ]
        for entry in conflicts:
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(ValueError, 'request_id'):
                    accept(db, 'edit', [entry])
        self.assertEqual(find_receipt(db, 'edit')[0], edit)
        self.assertEqual(revision_head(db), 2)
        self.assertEqual(as_of(db, 2), (row('x', 'east', 9),))

    def test_v1_withdraw_and_readd(self):
        db = self.source()
        accept(db, 'register', [change('x', None, image('east', 5))])
        accept(db, 'withdraw', [change('x', image('east', 5), None)])
        accept(db, 'readd', [change('x', None, image('west', 9))])
        self.assertEqual(as_of(db, 1), (row('x', 'east', 5),))
        self.assertEqual(as_of(db, 2), ())
        self.assertEqual(as_of(db, 3), (row('x', 'west', 9),))

    def test_v1_zero_empty_and_same_value_batches(self):
        db = self.source()
        self.assertEqual(as_of(db, 0), ())
        self.assertEqual(accept(db, 'metered', [change('x', None, image('east', 0))]).revision, 1)
        self.assertEqual(accept(db, 'empty', []).revision, 2)
        self.assertEqual(accept(db, 'checked', [change('x', image('east', 0), image('east', 0))]).revision, 3)
        for revision in (1, 2, 3):
            self.assertEqual(as_of(db, revision), (row('x', 'east', 0),))
        self.assertEqual(as_of(db, 0), ())

    def test_v1_unknown_revision(self):
        db = self.source()
        for revision in (1, 2**70):
            with self.subTest(revision=revision), self.assertRaises(KeyError):
                as_of(db, revision)
        for revision in (-1, True, 0.0):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                as_of(db, revision)

    def test_v2_stale_value_rejects_whole_batch(self):
        db = self.source()
        accept(db, 'opening', [change('a', None, image('east', 3)), change('x', None, image('east', 5))])
        proposed = [change('a', image('east', 3), image('east', 8)), change('x', image('east', 4), None)]
        with self.assertRaisesRegex(ValueError, 'stale before image'):
            accept(db, 'stale', proposed)
        first, second = row('a', 'east', 3), row('x', 'east', 5)
        self.assertEqual(read_current(db, [first[0], second[0]]), dict((first, second)))
        self.assertEqual(as_of(db, 1), (first, second))
        self.assertEqual(revision_head(db), 1)
        self.assertIsNone(find_receipt(db, 'stale'))

    def test_v2_stale_plant_rejects_whole_batch(self):
        db = self.source()
        accept(db, 'opening', [change('a', None, image('east', 3)), change('x', None, image('east', 5))])
        accept(db, 'transfer', [change('x', image('east', 5), image('west', 5))])
        proposed = [change('a', image('east', 3), image('east', 8)),
                    change('x', image('east', 5), image('west', 7))]
        with self.assertRaisesRegex(ValueError, 'stale before image'):
            accept(db, 'stale', proposed)
        first, second = row('a', 'east', 3), row('x', 'west', 5)
        self.assertEqual(read_current(db, [first[0], second[0]]), dict((first, second)))
        self.assertEqual(as_of(db, 2), (first, second))
        self.assertEqual(revision_head(db), 2)
        self.assertIsNone(find_receipt(db, 'stale'))

    def test_v2_invalid_batches_leave_no_receipt_or_revision(self):
        db = self.source()
        valid = change('a', None, image('east', 3))
        invalid_entries = [
            valid,
            change('x', None, image('east', 7), day='2026-02-30'),
            change('x', None, image('east', 7), day='20260612'),
            change('x', None, image('east', True)),
            change('x', None, image('east', 1.0)),
            change('x', None, image('east', '1')),
            change('x', None, image(' ', 1)),
            change(' ', None, image('east', 1)),
            dict(meter='x', day=valid['day'], after=image('east', 1)),
            dict(meter='x', day=valid['day'], before=None),
            change('x', None, {'plant': 'east'}),
        ]
        for index, invalid in enumerate(invalid_entries):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    accept(db, 'invalid', [valid, invalid])
                self.assertEqual(revision_head(db), 0)
                self.assertIsNone(find_receipt(db, 'invalid'))
        key = row('a', 'east', 3)[0]
        self.assertEqual(read_current(db, [key]), {})
        self.assertEqual(accept(db, 'invalid', [valid]).revision, 1)

    def test_v2_commit_failure_rolls_back_then_allows_retry(self):
        db = self.source()
        accept(db, 'opening', [change('x', None, image('east', 5))])
        edit = [change('x', image('east', 5), image('east', 9))]
        with self.assertRaisesRegex(RuntimeError, 'commit interrupted'):
            accept(db, 'edit', edit, before_commit=fail_commit)
        db.close()
        db = self.source()
        before = row('x', 'east', 5)
        self.assertEqual(read_current(db, [before[0]]), dict([before]))
        self.assertEqual(revision_head(db), 1)
        self.assertIsNone(find_receipt(db, 'edit'))
        self.assertEqual(accept(db, 'edit', edit).revision, 2)
        self.assertEqual(as_of(db, 1), (before,))
        self.assertEqual(as_of(db, 2), (row('x', 'east', 9),))
