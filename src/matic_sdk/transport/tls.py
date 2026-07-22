"""TLS context construction and post-handshake identity checks."""

from __future__ import annotations

import hashlib
import hmac
import ssl
from dataclasses import dataclass

from matic_sdk.config import SecurityMode, TlsConfig


class TlsVerificationError(ConnectionError):
    """The peer did not satisfy the configured TLS policy."""


@dataclass(frozen=True, slots=True)
class TlsConnectionInfo:
    version: str | None
    cipher: str | None
    alpn_protocol: str | None
    certificate_sha256: str
    verified: bool


def create_ssl_context(policy: TlsConfig) -> ssl.SSLContext:
    """Create a client context for Hermes' required HTTP/2 TLS transport."""

    if (
        policy.mode is SecurityMode.INSECURE_READ_ONLY
        or policy.certificate_sha256 is not None
    ):
        # Exact pin verification must happen after the handshake.  CERT_NONE is
        # safe here only because ``verify_peer`` fails closed on a pin mismatch.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=str(policy.ca_file) if policy.ca_file is not None else None,
        )
        context.check_hostname = policy.check_hostname
        context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["h2"])
    return context


def verify_peer(
    ssl_object: ssl.SSLObject | ssl.SSLSocket, policy: TlsConfig
) -> TlsConnectionInfo:
    """Verify ALPN and, when configured, the exact DER certificate pin."""

    alpn = ssl_object.selected_alpn_protocol()
    if alpn != "h2":
        raise TlsVerificationError(f"Hermes requires ALPN h2; peer selected {alpn!r}")
    certificate = ssl_object.getpeercert(binary_form=True)
    if not certificate:
        raise TlsVerificationError("TLS peer did not provide a certificate")
    fingerprint = hashlib.sha256(certificate).hexdigest()
    if policy.certificate_sha256 is not None and not hmac.compare_digest(
        fingerprint, policy.certificate_sha256
    ):
        raise TlsVerificationError(
            "robot certificate SHA-256 does not match the configured pin"
        )
    cipher_info = ssl_object.cipher()
    return TlsConnectionInfo(
        version=ssl_object.version(),
        cipher=cipher_info[0] if cipher_info else None,
        alpn_protocol=alpn,
        certificate_sha256=fingerprint,
        verified=policy.verified,
    )
