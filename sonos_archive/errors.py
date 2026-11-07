"""Typed errors with stable process exit codes."""

from __future__ import annotations

from typing import Optional


class ArchiveError(Exception):
    """Base class for expected, clean failures."""

    exit_code = 1

    def __init__(self, message: str, *, url: Optional[str] = None):
        super().__init__(message)
        self.url = url


class ConfigError(ArchiveError):
    exit_code = 2


class PolicyError(ArchiveError):
    """A request violated an explicit scope or do-not-crawl rule."""

    exit_code = 2


class DenialError(ArchiveError):
    """The remote host denied access or presented a challenge."""

    exit_code = 3


class RobotsDenied(PolicyError):
    """robots.txt disallows this request."""

    exit_code = 0


class SchemaError(ArchiveError):
    """An API response did not match a known, safe schema."""

    exit_code = 4


class ProtocolError(ArchiveError):
    """Unexpected HTTP behaviour that is not an access denial."""

    exit_code = 4


class NetworkError(ArchiveError):
    """A transport-level failure. The task remains pending for a later run."""

    exit_code = 4


class BudgetExhausted(ArchiveError):
    """The bounded run budget was reached; this is not a failure."""

    exit_code = 0


class PublishError(ArchiveError):
    exit_code = 5


class RestoreError(ArchiveError):
    exit_code = 4


class SnapshotError(ArchiveError):
    """Snapshot build/publish/verify layer failures."""

    exit_code = 5
