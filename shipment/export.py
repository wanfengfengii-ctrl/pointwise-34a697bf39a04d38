from collections.abc import Callable
from sqlite3 import Connection

from facts.ledger import source_write
from facts.measurements import validate_identifier
from facts.revisions import validate_revision_number
from facts.selection import as_of
from shipment.catalog import PublicationReceipt, PublishedFull, find_publication, save_publication
from shipment.envelope import seal_full
from shipment.payload import FullPayload, encode_full_payload
from shipment.streams import get_stream


def issue_full(
    db: Connection, publication_id: str, stream_id: str, revision: int,
    *, before_commit: Callable[[], None] | None = None,
) -> PublishedFull:
    publication_id = validate_identifier(publication_id, 'publication_id')
    stream_id = validate_identifier(stream_id, 'stream_id')
    validate_revision_number(revision)
    with source_write(db):
        prior = find_publication(db, publication_id)
        if prior is not None:
            receipt = prior.receipt
            if (receipt.binding.stream_id, receipt.revision) != (stream_id, revision):
                raise ValueError('publication_id already binds different parameters')
            return prior

        binding = get_stream(db, stream_id)
        plants = set(binding.plants)
        rows = tuple((key, image) for key, image in as_of(db, revision) if image.plant in plants)
        payload = FullPayload(binding, revision, publication_id, rows)
        receipt = PublicationReceipt(publication_id, binding, revision, len(rows))
        publication = PublishedFull(receipt, seal_full(encode_full_payload(payload)))
        save_publication(db, publication)
        if before_commit is not None:
            before_commit()
    return publication
