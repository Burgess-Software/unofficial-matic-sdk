"""Unofficial SDK for owner-controlled Matic robots."""

from __future__ import annotations

from matic_sdk.client import MaticClient
from matic_sdk.commands import CommandExecutor, JsonlAuditLog
from matic_sdk.config import MaticConfig, SecurityMode, TlsConfig
from matic_sdk.credentials import BotToken, CredentialStore
from matic_sdk.models.control import (
    ControlCommand,
    JoystickCommand,
    UserAction,
    UserCommand,
)
from matic_sdk.protocol.collections import KNOWN_TARGETS, RawCollectionEvent
from matic_sdk.protocol.commands import COMMAND_REGISTRY, COMMAND_SPECS
from matic_sdk.safety import MotionControls, TeleopSession, UnsafeControls

__version__ = "0.1.0a1"

__all__ = [
    "COMMAND_REGISTRY",
    "COMMAND_SPECS",
    "KNOWN_TARGETS",
    "BotToken",
    "CommandExecutor",
    "ControlCommand",
    "CredentialStore",
    "JoystickCommand",
    "JsonlAuditLog",
    "MaticClient",
    "MaticConfig",
    "MotionControls",
    "RawCollectionEvent",
    "SecurityMode",
    "TeleopSession",
    "TlsConfig",
    "UnsafeControls",
    "UserAction",
    "UserCommand",
    "__version__",
]
