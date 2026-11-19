import base64
import binascii
import hashlib

from facts.measurements import canonical_json, read_json, require_fields


V1_FULL_VERSION = 1


def seal_full(body: bytes) -> bytes:
    if not isinstance(body, bytes):
        raise ValueError('full body must be bytes')
    return canonical_json(dict(
        version=V1_FULL_VERSION,
        kind='full',
        body=base64.b64encode(body).decode('ascii'),
        sha256=hashlib.sha256(body).hexdigest(),
    )).encode('utf-8')


def open_full(blob: bytes) -> bytes:
    if not isinstance(blob, bytes):
        raise ValueError('full envelope must be bytes')
    envelope = require_fields(read_json(blob), {'version', 'kind', 'body', 'sha256'})
    version = envelope['version']
    if type(version) is not int or version != V1_FULL_VERSION or envelope['kind'] != 'full':
        raise ValueError('unsupported full envelope version or kind')
    token = envelope['body']
    if not isinstance(token, str):
        raise ValueError('full body must be Base64 text')
    try:
        body = base64.b64decode(token, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('invalid Base64 full body') from exc
    if envelope['sha256'] != hashlib.sha256(body).hexdigest():
        raise ValueError('full body checksum mismatch')
    return body
