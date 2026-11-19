import hashlib
import json
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from facts.ledger import open_source
from intake.batches import accept
from mirror.bindings import read_installed_stream
from mirror.ledger import open_mirror, read_details
from mirror.readback import read_mirror
from mirror.receive import load_full
from report.reconcile import reconcile_stream
from report.render import render_reconciliation
from shipment.export import issue_full
from shipment.streams import register_stream

DAY = '2026-06-12'
STREAM = 'east-daily'
SOURCE = 'qingting-increment-example'


def entry(meter, before, after):
    def image(pair):
        return None if pair is None else dict(plant=pair[0], liters=pair[1])

    return dict(meter=meter, day=DAY, before=image(before), after=image(after))


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


def equal(actual, expected):
    if actual != expected:
        raise AssertionError(f'expected {expected!r}, got {actual!r}')


def rejects(error, call):
    try:
        call()
    except error:
        return
    raise AssertionError(f'expected {error.__name__}')


def accept_range(db, first, last):
    for revision in range(first, last + 1):
        equal(accept(db, f'revision-{revision}', BATCHES[revision - 1]).revision, revision)


def check_report(db, revision):
    rows = EAST[revision]
    totals = [dict(plant='east', count=len(rows), liters=sum(rows.values()))] if rows else []
    expected = dict(revision=revision, totals=totals, rows=[
        dict(meter=meter, day=DAY, plant='east', liters=liters)
        for meter, liters in sorted(rows.items())
    ])
    actual = json.loads(render_reconciliation(reconcile_stream(db, STREAM)))
    equal(actual, expected)
    return actual


def check_fixture():
    with closing(open_source(':memory:', source_id=SOURCE)) as source:
        register_stream(source, STREAM, ['east'])
        accept_range(source, 1, 5)
        full = [issue_full(source, f'full-{r}', STREAM, r).blob for r in range(6)]
    with closing(open_mirror(':memory:')) as mirror:
        results = []
        for revision, blob in enumerate(full):
            equal(load_full(mirror, blob), revision)
            results.append(check_report(mirror, revision))
    return results


def exercise(root):
    from mirror.receive import apply_increment
    from shipment.export import export_increment

    source_path = root / 'source.sqlite'
    with closing(open_source(source_path, source_id=SOURCE)) as source:
        register_stream(source, STREAM, ['east'])
        accept_range(source, 1, 3)
        overlap = export_increment(source, STREAM, 1, 3)
        frame = json.loads(overlap)
        equal((frame['version'], frame['kind']), (2, 'increment'))
        accept_range(source, 4, 5)
    with closing(open_source(source_path)) as source:
        equal(export_increment(source, STREAM, 1, 3), overlap)
        full = [issue_full(source, f'full-{r}', STREAM, r).blob for r in range(6)]
        long_increment = export_increment(source, STREAM, 1, 5)
        from_zero = export_increment(source, STREAM, 0, 5)
        fill = export_increment(source, STREAM, 2, 3)
        empty = export_increment(source, STREAM, 3, 4)
        for low, high in ((True, 2), (-1, 2), (3, 3), (4, 3)):
            rejects(ValueError, lambda: export_increment(source, STREAM, low, high))
        rejects(KeyError, lambda: export_increment(source, STREAM, 0, 6))
        rejects(KeyError, lambda: export_increment(source, 'unknown', 0, 1))
    source_path.unlink()
    with closing(open_source(':memory:', source_id='another-source')) as source:
        register_stream(source, STREAM, ['east'])
        accept_range(source, 1, 3)
        foreign = export_increment(source, STREAM, 1, 3)

    malformed = json.dumps(dict(frame, body='', sha256=hashlib.sha256(b'').hexdigest())).encode()
    checksum = frame['sha256']
    wrong_digit = '1' if checksum[0] == '0' else '0'
    damaged = json.dumps(dict(frame, sha256=wrong_digit + checksum[1:])).encode()
    with closing(open_mirror(root / 'uninitialized.sqlite')) as mirror:
        rejects(ValueError, lambda: apply_increment(mirror, from_zero))
        rejects(KeyError, lambda: read_mirror(mirror, STREAM))

    overlaps = []
    for start in (1, 2):
        path = root / f'overlap-{start}.sqlite'
        with closing(open_mirror(path)) as mirror:
            load_full(mirror, full[start])
            equal(apply_increment(mirror, overlap), 3)
        with closing(open_mirror(path)) as mirror:
            overlaps.append(dict(start=start, report=check_report(mirror, 3)))
            equal(apply_increment(mirror, overlap), 3)
            equal(load_full(mirror, full[3]), 3)
    for start in range(5):
        with closing(open_mirror(root / f'long-{start}.sqlite')) as mirror:
            load_full(mirror, full[start])
            equal(apply_increment(mirror, from_zero if start == 0 else long_increment), 5)
            check_report(mirror, 5)
            if start == 4:
                equal(apply_increment(mirror, overlap), 5)
                rejects(ValueError, lambda: apply_increment(mirror, malformed))
                check_report(mirror, 5)

    path = root / 'retry.sqlite'
    staged = []
    with closing(open_mirror(path)) as mirror:
        load_full(mirror, full[2])
        for bad in (empty, foreign, damaged, full[3]):
            rejects(ValueError, lambda: apply_increment(mirror, bad))
            check_report(mirror, 2)

        def interrupt():
            confirmation = read_installed_stream(mirror, STREAM)
            details = read_details(mirror, STREAM)
            staged.append((confirmation.revision, {key.meter: row.liters for key, row in details}))
            raise RuntimeError('interrupted before commit')

        rejects(RuntimeError, lambda: apply_increment(mirror, fill, before_commit=interrupt))
        equal(staged, [(3, EAST[3])])
    with closing(open_mirror(path)) as mirror:
        restored = check_report(mirror, 2)
        equal(apply_increment(mirror, fill), 3)
        equal(apply_increment(mirror, empty), 4)
        completed = check_report(mirror, 4)
    return dict(overlap=overlaps, reopened_after_failure=restored, after_retry=completed)


def main(mode):
    if mode == 'fixture':
        print(json.dumps(check_fixture(), sort_keys=True))
        return 0
    if mode not in ('verify', 'demo'):
        raise SystemExit('usage: increment_examples.py fixture|verify|demo')
    if mode == 'verify':
        from check_project import main as check_original

        status = check_original()
        if status:
            return status
    with TemporaryDirectory(prefix='qingting-increments-') as directory:
        print(json.dumps(exercise(Path(directory)), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv) == 2 else ''))
