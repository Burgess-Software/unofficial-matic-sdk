"""Owner-only storage for Matic enrollment credentials."""

from __future__ import annotations

import base64
import os
import re
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from matic_sdk.protocol.wire import (
    ProtoWireError,
    bytes_values,
    encode_bytes_field,
    first_bytes,
    parse_fields,
)

_DEVICE_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PRIVATE_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
_PRIVATE_DIR_MODE = stat.S_IRWXU


class CredentialError(RuntimeError):
    """Invalid, absent, or unsafe credentials."""


class CredentialPermissionError(CredentialError):
    """A credential path is accessible by another user."""


@dataclass(frozen=True, slots=True)
class BotToken:
    """The serialized local Hermes credential.

    ``repr`` deliberately excludes all serialized and secret bytes.
    """

    user_id: str | None
    _secret: bytes = field(repr=False)
    _serialized: bytes = field(repr=False)

    @property
    def serialized(self) -> bytes:
        return self._serialized

    @property
    def secret_length(self) -> int:
        return len(self._secret)

    @classmethod
    def decode(cls, serialized: bytes) -> BotToken:
        if not serialized:
            raise CredentialError("BotToken is empty")
        try:
            fields = parse_fields(serialized, max_fields=32)
        except ProtoWireError as exc:
            raise CredentialError(f"BotToken is not valid protobuf: {exc}") from exc
        secret_values = bytes_values(fields, 2)
        if len(secret_values) != 1 or not secret_values[0]:
            raise CredentialError("BotToken must contain one non-empty secret field")

        user_id: str | None = None
        user_messages = bytes_values(fields, 1)
        if len(user_messages) > 1:
            raise CredentialError("BotToken contains multiple token requests")
        if user_messages:
            try:
                request_fields = parse_fields(user_messages[0], max_fields=8)
                raw_user_id = first_bytes(request_fields, 1)
                if raw_user_id is None:
                    raise CredentialError("BotToken request has no client UUID")
                user_id = str(uuid.UUID(raw_user_id.decode("utf-8")))
            except (ProtoWireError, UnicodeDecodeError, ValueError) as exc:
                raise CredentialError(
                    "BotToken contains an invalid client UUID"
                ) from exc
        return cls(user_id, secret_values[0], bytes(serialized))

    def authorization_header(self) -> str:
        encoded = base64.b64encode(self._serialized).decode("ascii")
        return f"Bearer: {encoded}"


def serialize_token_request(user_id: str) -> bytes:
    try:
        canonical = str(uuid.UUID(user_id))
    except ValueError as exc:
        raise CredentialError("client identity must be a UUID") from exc
    return encode_bytes_field(1, canonical.encode("utf-8"))


def default_data_root() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return Path(configured).expanduser() / "matic-sdk"
    return Path.home() / ".local" / "share" / "matic-sdk"


@dataclass(frozen=True, slots=True)
class CredentialPaths:
    directory: Path
    token: Path
    client_id: Path


def _validate_device_alias(device_alias: str) -> str:
    if not _DEVICE_ALIAS_RE.fullmatch(device_alias):
        raise ValueError(
            "device_alias must contain only letters, digits, dot, underscore, and dash"
        )
    return device_alias


def _assert_private_directory(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise CredentialPermissionError(
            f"credential directory is not a real directory: {path}"
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CredentialPermissionError(
            f"credential directory must be owner-only (mode {mode:04o}): {path}"
        )
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise CredentialPermissionError(
            f"credential directory has a different owner: {path}"
        )


def _assert_private_file(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CredentialPermissionError(
            f"credential path is not a regular file: {path}"
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CredentialPermissionError(
            f"credential file must be owner-only (mode {mode:04o}): {path}"
        )
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise CredentialPermissionError(
            f"credential file has a different owner: {path}"
        )


def _write_new_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, _PRIVATE_FILE_MODE)
    except FileExistsError as exc:
        raise CredentialError(
            f"refusing to overwrite existing credential: {path}"
        ) from exc
    try:
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class CredentialStore:
    """Per-alias credential storage below the XDG data directory."""

    def __init__(self, device_alias: str, *, root: Path | None = None) -> None:
        self.device_alias = _validate_device_alias(device_alias)
        self.root = (root or default_data_root()).expanduser()
        directory = self.root / "devices" / self.device_alias
        self.paths = CredentialPaths(
            directory=directory,
            token=directory / "bot-token.pb",
            client_id=directory / "client-id",
        )

    def ensure_directory(self) -> Path:
        # This tree is SDK-owned; every newly created component is private.
        self.root.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        devices = self.root / "devices"
        devices.mkdir(mode=_PRIVATE_DIR_MODE, exist_ok=True)
        self.paths.directory.mkdir(mode=_PRIVATE_DIR_MODE, exist_ok=True)
        for path in (self.root, devices, self.paths.directory):
            _assert_private_directory(path)
        return self.paths.directory

    def load_or_create_client_id(self) -> str:
        self.ensure_directory()
        if self.paths.client_id.exists():
            _assert_private_file(self.paths.client_id)
            try:
                return str(uuid.UUID(self.paths.client_id.read_text("utf-8").strip()))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise CredentialError("stored client identity is invalid") from exc
        client_id = str(uuid.uuid4())
        _write_new_private(self.paths.client_id, f"{client_id}\n".encode())
        return client_id

    def save_token(self, serialized: bytes) -> BotToken:
        token = BotToken.decode(serialized)
        self.ensure_directory()
        _write_new_private(self.paths.token, token.serialized)
        return token

    def load_token(self) -> BotToken:
        if not self.paths.token.exists():
            raise CredentialError(
                f"no enrolled BotToken for alias {self.device_alias!r}"
            )
        _assert_private_file(self.paths.token)
        try:
            return BotToken.decode(self.paths.token.read_bytes())
        except OSError as exc:
            raise CredentialError(f"could not read BotToken: {exc}") from exc

    def import_token(self, source: Path) -> BotToken:
        source = Path(source).expanduser()
        _assert_private_file(source)
        return self.save_token(source.read_bytes())

    @property
    def enrolled(self) -> bool:
        return self.paths.token.is_file()
