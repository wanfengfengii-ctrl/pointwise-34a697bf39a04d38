from dataclasses import dataclass

from facts.measurements import DayKey, Measurement, canonical_json, items, validate_identifier
from intake.observations import ObservationChange, parse_observation


@dataclass(frozen=True)
class BatchRequest:
    request_id: str
    changes: tuple[ObservationChange, ...]
    content: str


def _image_fields(image: Measurement | None) -> dict | None:
    if image is None:
        return None
    return dict(plant=image.plant, liters=image.liters)


def normalize_request(request_id: str, entries) -> BatchRequest:
    request_id = validate_identifier(request_id, 'request_id')
    by_key: dict[DayKey, ObservationChange] = {}
    for entry in items(entries, 'entries'):
        change = parse_observation(entry)
        if change.key in by_key:
            raise ValueError(f'duplicate day key {change.key}')
        by_key[change.key] = change
    changes = tuple(by_key[key] for key in sorted(by_key))
    content = canonical_json([
        dict(meter=change.key.meter, day=change.key.day,
             before=_image_fields(change.before), after=_image_fields(change.after))
        for change in changes
    ])
    return BatchRequest(request_id, changes, content)
