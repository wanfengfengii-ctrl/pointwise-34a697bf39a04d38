import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from facts.ledger import open_source
from intake.batches import accept
from mirror.ledger import open_mirror
from mirror.receive import load_full
from mirror.readback import read_mirror
from report.reconcile import daily_report, reconcile_stream
from report.render import render_reconciliation
from shipment.catalog import get_full
from shipment.export import issue_full
from shipment.streams import register_stream


SAMPLES = Path(__file__).resolve().parent / 'samples'
STREAM = 'east-daily'


def require_equal(label, actual, expected):
    if actual != expected:
        raise RuntimeError(f'{label}: expected {expected!r}, got {actual!r}')


def main() -> None:
    batches = json.loads((SAMPLES / 'batches.json').read_text(encoding='utf-8'))
    expected = json.loads((SAMPLES / 'report.json').read_text(encoding='utf-8'))
    frozen = (SAMPLES / 'full-v1.json').read_bytes()
    with TemporaryDirectory(prefix='qingting-') as directory:
        source_path = Path(directory) / 'source.sqlite'
        mirror_path = Path(directory) / 'regulator.sqlite'
        with closing(open_source(source_path, source_id='qingting-demo')) as source:
            first = accept(source, **batches[0])
            require_equal('first revision', first.revision, 1)
            require_equal('first replay', accept(source, **batches[0]), first)
            for revision, batch in enumerate(batches[1:], start=2):
                require_equal('accepted revision', accept(source, **batch).revision, revision)
            binding = register_stream(source, STREAM, ['east'])
            published = issue_full(source, 'day-close', STREAM, 3)
            require_equal('publication row count', published.receipt.row_count, 2)
            blob = get_full(source, 'day-close')
            require_equal('issued bytes', blob, published.blob)
            require_equal('frozen V1 bytes', blob, frozen)
        with closing(open_mirror(mirror_path)) as mirror:
            require_equal('installed revision', load_full(mirror, blob), 3)
            installed = read_mirror(mirror, STREAM)
            require_equal('installed binding', installed.binding, binding)
            rendered = render_reconciliation(reconcile_stream(mirror, STREAM))
            actual = json.loads(rendered)
            require_equal('independent closing report', actual, expected)
        with closing(open_mirror(mirror_path)) as mirror:
            require_equal('reopened snapshot', read_mirror(mirror, STREAM), installed)
            require_equal('replayed revision', load_full(mirror, blob), 3)
            revision, totals = daily_report(mirror, STREAM)
            actual_totals = [dict(plant=t.plant, count=t.count, liters=t.liters) for t in totals]
            require_equal('reopened report revision', revision, 3)
            require_equal('reopened report totals', actual_totals, expected['totals'])
            require_equal('reopened report JSON', render_reconciliation(reconcile_stream(mirror, STREAM)), rendered)

    print(rendered)
    event = dict(
        case_id='v8',
        input=dict(batches=batches, stream=STREAM, plants=['east'], publication='day-close'),
        expected=dict(revision=expected['revision'], rows=expected['rows']),
        actual=dict(revision=actual['revision'], rows=actual['rows']),
        downstream_expected=dict(revision=expected['revision'], totals=expected['totals']),
        downstream_actual=dict(revision=revision, totals=actual_totals),
        code_paths=['intake/batches.py', 'facts/history.py', 'shipment/export.py',
                    'mirror/install.py', 'mirror/readback.py', 'report/reconcile.py', 'report/render.py'],
        oracle='Follow the three accepted batches by hand. After the transfer and withdrawal, east contains y=7 and z=0, yielding 2 rows and 7 liters.',
    )
    print('POINTWISE_SCENARIO ' + json.dumps(event, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
