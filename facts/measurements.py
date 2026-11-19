import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, order=True)
class DayKey:
    meter: str
    day: str


@dataclass(frozen=True)
class Measurement:
    plant: str
    liters: int


Rows = tuple[tuple[DayKey, Measurement], ...]


def validate_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonblank text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError(f'{label} must be valid UTF-8 text') from exc
    return value


def parse_day_key(meter: object, day: object) -> DayKey:
    meter = validate_identifier(meter, 'meter')
    if not isinstance(day, str):
        raise ValueError('day must be YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError('day must be a real YYYY-MM-DD date') from exc
    if parsed.isoformat() != day:
        raise ValueError('day must be YYYY-MM-DD')
    return DayKey(meter, day)


def validate_liters(value: object) -> int:
    if type(value) is not int:
        raise ValueError('liters must be an integer')
    return value


def liters_to_storage(value: int) -> str:
    # Hex text avoids SQLite INTEGER bounds and preserves every Python int.
    return format(validate_liters(value), 'x')


def liters_from_storage(value: str) -> int:
    return int(value, 16)


def require_fields(value: object, names: set[str]) -> dict:
    if not isinstance(value, dict) or value.keys() != names:
        raise ValueError(f'expected fields {", ".join(sorted(names))}')
    return value


def items(value: object, label: str):
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ValueError(f'{label} must be a collection')
    try:
        return iter(value)
    except TypeError as exc:
        raise ValueError(f'{label} must be a collection') from exc


def row_fields(key: DayKey, image: Measurement) -> dict:
    return dict(meter=key.meter, day=key.day, plant=image.plant, liters=image.liters)


def _decimal_integer(value: int) -> str:
    try:
        return str(value)
    except ValueError:
        # Python 3.11 limits a single decimal conversion by default.
        sign = '-' if value < 0 else ''
        value = abs(value)
        chunks = []
        while value >= 10**9:
            value, chunk = divmod(value, 10**9)
            chunks.append(f'{chunk:09d}')
        return sign + str(value) + ''.join(reversed(chunks))


def _parse_integer(token: str) -> int:
    try:
        return int(token)
    except ValueError:
        digits = token.removeprefix('-')
        value = 0
        for start in range(0, len(digits), 9):
            chunk = digits[start:start + 9]
            value = value * 10**len(chunk) + int(chunk)
        return -value if token.startswith('-') else value


def canonical_json(value: object) -> str:
    if type(value) is int:
        return _decimal_integer(value)
    if isinstance(value, dict):
        fields = (
            json.dumps(key) + ':' + canonical_json(value[key])
            for key in sorted(value)
        )
        return '{' + ','.join(fields) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(map(canonical_json, value)) + ']'
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field {key!r}')
        result[key] = value
    return result


def _reject_constant(token: str):
    raise ValueError(f'invalid JSON number {token}')


def read_json(blob: bytes):
    try:
        return json.loads(
            blob.decode('utf-8'),
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid UTF-8 JSON') from exc
