"""Connection and transport-security configuration.

The SDK intentionally has no device-specific defaults.  A caller must provide
the robot's address and must either use normal CA/hostname validation or pin the
exact server certificate.  The explicitly insecure mode exists only to aid
read-only protocol diagnostics; the transport refuses mutating streams in that
mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

DEFAULT_HERMES_PORT = 16_320
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class SecurityMode(StrEnum):
    """TLS identity-validation policy."""

    VERIFIED = "verified"
    INSECURE_READ_ONLY = "insecure-read-only"


class InsecureTransportError(RuntimeError):
    """Raised when a write is attempted over diagnostic-only TLS."""


def normalize_sha256(value: str) -> str:
    """Normalize a colon-delimited SHA-256 fingerprint."""

    normalized = value.strip().lower().replace(":", "")
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("certificate SHA-256 fingerprint must contain 64 hex digits")
    return normalized


@dataclass(frozen=True, slots=True)
class TlsConfig:
    """TLS policy for the robot's Hermes endpoint.

    In verified mode, an exact certificate fingerprint can be used when the
    private Matician CA is not available.  Without a pin, Python's normal trust
    store (or ``ca_file``) and hostname validation are used.
    """

    mode: SecurityMode = SecurityMode.VERIFIED
    ca_file: Path | None = None
    certificate_sha256: str | None = None
    check_hostname: bool = True

    def __post_init__(self) -> None:
        if self.ca_file is not None:
            object.__setattr__(self, "ca_file", Path(self.ca_file).expanduser())
        if self.certificate_sha256 is not None:
            object.__setattr__(
                self,
                "certificate_sha256",
                normalize_sha256(self.certificate_sha256),
            )
        if self.mode is SecurityMode.INSECURE_READ_ONLY:
            if self.ca_file is not None or self.certificate_sha256 is not None:
                raise ValueError(
                    "insecure read-only TLS cannot be combined with a CA or pin"
                )
            if self.check_hostname:
                object.__setattr__(self, "check_hostname", False)
        elif not self.check_hostname and self.certificate_sha256 is None:
            raise ValueError(
                "verified TLS requires hostname validation or a certificate pin"
            )

    @classmethod
    def pinned(cls, fingerprint: str) -> TlsConfig:
        """Use exact certificate pinning without hostname matching."""

        return cls(certificate_sha256=fingerprint, check_hostname=False)

    @classmethod
    def insecure_diagnostics(cls) -> TlsConfig:
        """Create an encrypted but unauthenticated read-only policy."""

        return cls(mode=SecurityMode.INSECURE_READ_ONLY, check_hostname=False)

    @property
    def verified(self) -> bool:
        return self.mode is SecurityMode.VERIFIED

    def assert_mutating_allowed(self) -> None:
        if not self.verified:
            raise InsecureTransportError(
                "mutating Hermes calls require verified TLS; "
                "insecure diagnostic mode is read-only"
            )


@dataclass(frozen=True, slots=True)
class MaticConfig:
    """A single local Matic Hermes endpoint."""

    host: str
    port: int = DEFAULT_HERMES_PORT
    authority: str | None = None
    sni: str | None = None
    connect_timeout: float = 10.0
    operation_timeout: float = 10.0
    max_message_bytes: int = 64 * 1024 * 1024
    command_protocol_version: int | None = None
    tls: TlsConfig = field(default_factory=TlsConfig)

    def __post_init__(self) -> None:
        host = self.host.strip()
        if not host or any(character.isspace() for character in host):
            raise ValueError("host must be a non-empty address or hostname")
        object.__setattr__(self, "host", host)
        if not 1 <= self.port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        if self.connect_timeout <= 0 or self.operation_timeout <= 0:
            raise ValueError("timeouts must be positive")
        if self.max_message_bytes < 1:
            raise ValueError("max_message_bytes must be positive")
        if self.command_protocol_version is not None and (
            isinstance(self.command_protocol_version, bool)
            or not isinstance(self.command_protocol_version, int)
            or self.command_protocol_version < 1
        ):
            raise ValueError("command_protocol_version must be a positive integer")
        if self.authority is not None and not self.authority.strip():
            raise ValueError("authority cannot be empty")
        if self.sni is not None and not self.sni.strip():
            raise ValueError("sni cannot be empty")

    @property
    def effective_authority(self) -> str:
        if self.authority:
            return self.authority
        authority_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{authority_host}:{self.port}"

    @property
    def effective_sni(self) -> str:
        return self.sni or self.host
