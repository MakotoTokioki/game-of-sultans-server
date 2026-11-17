"""P-256 ECDH and RC4 primitives matching the Game of Sultans client.

Client side (libopenssl.so):
  getPublicKey()            -> PEM SubjectPublicKeyInfo, P-256 (NID 415)
  getSharedKey(serverPem)   -> base64(ECDH_compute_key(...)) = 32-byte X coord

The resulting base64 string is used as the RC4 key for request/response data.
"""

from __future__ import annotations

import base64
import json
import gzip
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


@dataclass
class ServerKeyPair:
    private_key: ec.EllipticCurvePrivateKey
    public_pem: str

    def shared_key(self, client_pub_pem: str) -> str:
        try:
            peer = serialization.load_pem_public_key(client_pub_pem.encode("utf-8"))
        except ValueError:
            # The Java/OpenSSL client emits a SubjectPublicKeyInfo PEM with
            # the P-256 curve written out as explicit parameters.  PyCA
            # rejects those, but the BIT STRING still contains the standard
            # uncompressed point: 0x04 || X(32) || Y(32).
            der = base64.b64decode(
                "".join(line for line in client_pub_pem.splitlines() if "-----" not in line)
            )
            if len(der) < 65:
                raise
            point = der[-65:]
            if point[0] != 0x04:
                raise
            peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        if not isinstance(peer, ec.EllipticCurvePublicKey):
            raise ValueError("client key is not an EC public key")
        secret = self.private_key.exchange(ec.ECDH(), peer)
        return base64.b64encode(secret).decode("ascii")


def generate_keypair() -> ServerKeyPair:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return ServerKeyPair(key, pem)


def rc4(key: bytes | str, data: bytes) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not key:
        raise ValueError("empty RC4 key")
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for n, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] = byte ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text)


def decode_request_data(sk: str, data_b64: str) -> dict:
    """base64 -> RC4 -> JSON (client request pipeline)."""
    plain = rc4(sk, b64d(data_b64))
    return json.loads(plain.decode("utf-8"))


def encode_response_data(sk: str, obj: dict) -> str:
    """JSON -> gzip -> RC4 -> base64 (client response pipeline).

    The client does: base64-decode -> RC4 -> mime.b64 -> gzipDecompress,
    where gzipDecompress base64-decodes again before inflating.  Therefore
    the wire value is RC4 of the *raw* gzip bytes, not base64-of-gzip.
    """
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = gzip.compress(raw, mtime=0)
    return base64.b64encode(rc4(sk, packed)).decode("ascii")


def decode_response_data(sk: str, data_b64: str) -> dict:
    """Server-side inverse used by the simulator/tests."""
    packed = rc4(sk, b64d(data_b64))
    raw = gzip.decompress(packed)
    return json.loads(raw.decode("utf-8"))
