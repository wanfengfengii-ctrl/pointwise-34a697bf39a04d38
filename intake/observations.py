from dataclasses import dataclass

from facts.measurements import DayKey, Measurement, parse_day_key, require_fields, validate_identifier, validate_liters


@dataclass(frozen=True)
class ObservationChange:
    key: DayKey
    before: Measurement | None
    after: Measurement | None


def _parse_image(value: object) -> Measurement | None:
    if value is None:
        return None
    image = require_fields(value, {'plant', 'liters'})
    return Measurement(validate_identifier(image['plant'], 'plant'), validate_liters(image['liters']))


def parse_observation(value: object) -> ObservationChange:
    entry = require_fields(value, {'meter', 'day', 'before', 'after'})
    return ObservationChange(
        parse_day_key(entry['meter'], entry['day']),
        _parse_image(entry['before']),
        _parse_image(entry['after']),
    )
