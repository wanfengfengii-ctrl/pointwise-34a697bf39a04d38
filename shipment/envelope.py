import base64
import binascii
import hashlib

from facts.measurements import canonical_json, read_json, require_fields


V1_FULL_VERSION = 1
V2_INCREMENT_VERSION = 2


def _seal(version: int, kind: str, body: bytes, label: str) -> bytes:
    if not isinstance(body, bytes):
        raise ValueError(f'{label} body must be bytes')
    return canonical_json(dict(
        version=version,
        kind=kind,
        body=base64.b64encode(body).decode('ascii'),
        sha256=hashlib.sha256(body).hexdigest(),
    )).encode('utf-8')


def _open(blob: bytes, version: int, kind: str, label: str) -> bytes:
    if not isinstance(blob, bytes):
        raise ValueError(f'{label} envelope must be bytes')
    envelope = require_fields(read_json(blob), {'version', 'kind', 'body', 'sha256'})
    if type(envelope['version']) is not int or envelope['version'] != version or envelope['kind'] != kind:
        raise ValueError(f'unsupported {label} envelope version or kind')
    token = envelope['body']
    if not isinstance(token, str):
        raise ValueError(f'{label} body must be Base64 text')
    try:
        body = base64.b64decode(token, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f'invalid Base64 {label} body') from exc
    if envelope['sha256'] != hashlib.sha256(body).hexdigest():
        raise ValueError(f'{label} body checksum mismatch')
    return body


def seal_full(body: bytes) -> bytes:
    return _seal(V1_FULL_VERSION, 'full', body, 'full')


def open_full(blob: bytes) -> bytes:
    return _open(blob, V1_FULL_VERSION, 'full', 'full')


def seal_increment(body: bytes) -> bytes:
    return _seal(V2_INCREMENT_VERSION, 'increment', body, 'increment')


def open_increment(blob: bytes) -> bytes:
    return _open(blob, V2_INCREMENT_VERSION, 'increment', 'increment')
