from dataclasses import dataclass

from facts.measurements import (
    Measurement, Rows, canonical_json, parse_day_key, read_json, require_fields,
    row_fields, validate_identifier, validate_liters,
)
from facts.revisions import validate_revision_number
from shipment.streams import StreamBinding, normalize_plants


@dataclass(frozen=True)
class FullPayload:
    binding: StreamBinding
    revision: int
    publication_id: str
    rows: Rows


def encode_full_payload(payload: FullPayload) -> bytes:
    scope = payload.binding
    return canonical_json(dict(
        binding=dict(source_id=scope.source_id, stream_id=scope.stream_id, plants=scope.plants),
        revision=payload.revision,
        publication_id=payload.publication_id,
        rows=[row_fields(key, image) for key, image in payload.rows],
    )).encode('utf-8')


def _decode_binding(value: object) -> StreamBinding:
    binding = require_fields(value, {'source_id', 'stream_id', 'plants'})
    plants = binding['plants']
    if not isinstance(plants, list):
        raise ValueError('binding plants must be an array')
    normalized = normalize_plants(plants)
    if tuple(plants) != normalized:
        raise ValueError('binding plants must be sorted and unique')
    return StreamBinding(
        validate_identifier(binding['source_id'], 'source_id'),
        validate_identifier(binding['stream_id'], 'stream_id'),
        normalized,
    )


def _decode_row(value: object):
    row = require_fields(value, {'meter', 'day', 'plant', 'liters'})
    key = parse_day_key(row['meter'], row['day'])
    image = Measurement(validate_identifier(row['plant'], 'plant'), validate_liters(row['liters']))
    return key, image


def decode_full_payload(body: bytes) -> FullPayload:
    value = require_fields(read_json(body), {'binding', 'revision', 'publication_id', 'rows'})
    if not isinstance(value['rows'], list):
        raise ValueError('full rows must be an array')
    return FullPayload(
        _decode_binding(value['binding']),
        validate_revision_number(value['revision']),
        validate_identifier(value['publication_id'], 'publication_id'),
        tuple(map(_decode_row, value['rows'])),
    )
