"""Typed failures. Exit codes live in cli.py."""


class ArchiveError(Exception):
    pass


class Denied(ArchiveError):
    """The site denied access (HTTP status or anti-bot challenge). Never retried."""

    def __init__(self, reason, url="", status=0, snippet=""):
        super().__init__(f"{reason} at {url}")
        self.reason, self.url, self.status, self.snippet = reason, url, status, snippet


class HttpError(ArchiveError):
    """A non-2xx response that is not a denial (e.g. a genuine 404)."""

    def __init__(self, status, url):
        super().__init__(f"HTTP {status} at {url}")
        self.status, self.url = status, url


class NetworkError(ArchiveError):
    """Transport-level failure (DNS, TCP, TLS, timeout)."""


class ShapeError(ArchiveError):
    """A response did not match the known schema. Fails closed."""
