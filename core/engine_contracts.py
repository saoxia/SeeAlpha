"""Lightweight shared API/Engine result and error contracts.

These definitions deliberately have no NautilusTrader, connector or Engine
runtime dependencies. They can become a separately versioned contract package
when SeeAlpha Engine moves into its own repository.
"""

from dataclasses import dataclass

# Contract version is independent of the Engine release version.
# Breaking path, identity, error or result changes increment MAJOR.
ENGINE_PROTOCOL_MAJOR = 1
ENGINE_PROTOCOL_MINOR = 0
ENGINE_PROTOCOL_HEADER = "X-SeeAlpha-Engine-Protocol"
ENGINE_PROTOCOL_VERSION = f"{ENGINE_PROTOCOL_MAJOR}.{ENGINE_PROTOCOL_MINOR}"


class QueryError(RuntimeError):
    """A sanitized connector/Engine query error safe for an API caller."""


class EngineConflict(Exception):
    """A rejected Engine business operation (HTTP 409)."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


@dataclass
class AccountSnapshot:
    balances: dict[str, str]
    info: dict
    positions: list[dict]
    observed_at: int
