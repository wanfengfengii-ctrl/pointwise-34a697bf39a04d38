from facts.measurements import canonical_json, row_fields
from report.reconcile import Reconciliation


def render_reconciliation(report: Reconciliation) -> str:
    return canonical_json(dict(
        revision=report.snapshot.revision,
        rows=[row_fields(key, image) for key, image in report.snapshot.rows],
        totals=[dict(plant=total.plant, count=total.count, liters=total.liters) for total in report.totals],
    ))
