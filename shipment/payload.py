from dataclasses import dataclass

from facts.measurements import (
    DayKey, Measurement, Rows, canonical_json, parse_day_key, read_json, require_fields,
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


@dataclass(frozen=True)
class IncrementChange:
    key: DayKey
    revision: int
    after: Measurement | None


@dataclass(frozen=True)
class IncrementPayload:
    binding: StreamBinding
    low: int
    high: int
    changes: tuple[IncrementChange, ...]


def validate_interval(low: object, high: object) -> tuple[int, int]:
    if type(low) is not int or type(high) is not int:
        raise ValueError('interval endpoints must be integers')
    if not 0 <= low < high:
        raise ValueError('interval must satisfy 0 <= low < high')
    return low, high


def _encode_binding(scope: StreamBinding) -> dict:
    return dict(source_id=scope.source_id, stream_id=scope.stream_id, plants=scope.plants)


def _encode_image(image: Measurement | None) -> dict | None:
    if image is None:
        return None
    return dict(plant=image.plant, liters=image.liters)


def encode_increment_payload(payload: IncrementPayload) -> bytes:
    return canonical_json(dict(
        binding=_encode_binding(payload.binding),
        low=payload.low,
        high=payload.high,
        changes=[
            dict(meter=change.key.meter, day=change.key.day,
                 revision=change.revision, after=_encode_image(change.after))
            for change in payload.changes
        ],
    )).encode('utf-8')


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


def _decode_increment_change(value: object) -> IncrementChange:
    change = require_fields(value, {'meter', 'day', 'revision', 'after'})
    after = change['after']
    image = None
    if after is not None:
        image_fields = require_fields(after, {'plant', 'liters'})
        image = Measurement(
            validate_identifier(image_fields['plant'], 'plant'),
            validate_liters(image_fields['liters']),
        )
    return IncrementChange(
        parse_day_key(change['meter'], change['day']),
        validate_revision_number(change['revision']),
        image,
    )


def decode_increment_payload(body: bytes) -> IncrementPayload:
    value = require_fields(read_json(body), {'binding', 'low', 'high', 'changes'})
    if not isinstance(value['changes'], list):
        raise ValueError('increment changes must be an array')
    low, high = validate_interval(value['low'], value['high'])
    return IncrementPayload(
        _decode_binding(value['binding']),
        low,
        high,
        tuple(map(_decode_increment_change, value['changes'])),
    )
