"""What went wrong with a provider, in a few kinds every caller can act on.

Provider code raises whatever its library raises (yt-dlp, requests, an HTTP status). ``classify``
turns that into a :class:`ProviderFailure`: its kind, whether trying again can help, how long a
provider asked to be left alone, and a short message fit for the interface. Technical details
stay in the log — and never a signed stream address (``redact``).

``Cooldowns`` remembers the providers that asked for a pause (HTTP 429 / Retry-After): background
work (prefetch, recommendations) leaves them alone until then; music the listener asks for is
still tried. ``retry`` runs an idempotent call again a bounded number of times, with backoff and
jitter, and only for failures that can pass (network trouble, timeouts, 5xx).
"""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class ErrorKind(str, Enum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_REQUIRED = "auth_required"
    GEO_RESTRICTED = "geo_restricted"
    NOT_FOUND = "not_found"
    EXTRACTOR_OUTDATED = "extractor_outdated"
    URL_EXPIRED = "url_expired"
    UNPLAYABLE = "unplayable"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


MESSAGES = {
    ErrorKind.NETWORK: "Couldn’t connect",
    ErrorKind.TIMEOUT: "The connection timed out",
    ErrorKind.RATE_LIMITED: "Too many requests — trying again shortly",
    ErrorKind.AUTH_REQUIRED: "This song needs a sign-in",
    ErrorKind.GEO_RESTRICTED: "Not available in your region",
    ErrorKind.NOT_FOUND: "This song is no longer available",
    ErrorKind.EXTRACTOR_OUTDATED: "Couldn’t read the page — try “update” in the Console",
    ErrorKind.URL_EXPIRED: "The stream expired",
    ErrorKind.UNPLAYABLE: "This song can’t be played",
    ErrorKind.CANCELLED: "Cancelled",
    ErrorKind.UNKNOWN: "Couldn’t play",
}
RETRYABLE = {ErrorKind.NETWORK, ErrorKind.TIMEOUT, ErrorKind.URL_EXPIRED}


@dataclass(frozen=True)
class ProviderFailure:
    kind: ErrorKind
    provider: str = ""
    retryable: bool = False
    retry_after_seconds: float | None = None
    safe_message: str = ""


@dataclass(frozen=True)
class ProviderCapabilities:
    search: bool = False
    metadata: bool = False
    streaming: bool = False
    download: bool = False
    albums: bool = False
    playlists: bool = False


class ProviderError(Exception):
    """A failure already classified (raised where the provider knows exactly what happened)."""

    def __init__(self, failure: ProviderFailure, detail: str = ""):
        super().__init__(detail or failure.safe_message)
        self.failure = failure


# ---- classification -----------------------------------------------------------------

_PATTERNS: list[tuple[re.Pattern, ErrorKind]] = [(re.compile(p, re.IGNORECASE), k) for p, k in (
    (r"\b429\b|too many requests|rate.?limit", ErrorKind.RATE_LIMITED),
    (r"in your country|geo.?restrict|in your region", ErrorKind.GEO_RESTRICTED),
    (r"sign in to confirm|login required|requires authentication|members.only|private video|\b401\b",
     ErrorKind.AUTH_REQUIRED),
    (r"video unavailable|has been removed|does not exist|no longer available|\b404\b|not found|"
     r"this video is not available|account associated with this video has been terminated", ErrorKind.NOT_FOUND),
    (r"\b403\b|forbidden|expired|signature", ErrorKind.URL_EXPIRED),
    (r"unable to extract|failed to parse|nsig|player response|please report this issue|unsupported url",
     ErrorKind.EXTRACTOR_OUTDATED),
    (r"timed? ?out|timeout", ErrorKind.TIMEOUT),
    (r"\b5\d\d\b|connection|network|resolve host|getaddrinfo|ssl|remote end closed|reset by peer|"
     r"temporarily|unreachable|max retries", ErrorKind.NETWORK),
    (r"no playable audio|requested format is not available|drm", ErrorKind.UNPLAYABLE),
)]
_RETRY_AFTER = re.compile(r"retry.?after[\"'=:\s]*(\d+)", re.IGNORECASE)


def classify(exc: BaseException, provider: str = "") -> ProviderFailure:
    """The kind of failure behind ``exc`` (from yt-dlp, requests, or our own code)."""
    if isinstance(exc, ProviderError):
        return exc.failure
    retry_after = None
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        headers = getattr(exc.response, "headers", {}) or {}
        try:
            retry_after = float(headers.get("Retry-After")) if headers.get("Retry-After") else None
        except (TypeError, ValueError):
            retry_after = None
        kind = (ErrorKind.RATE_LIMITED if status == 429 else ErrorKind.AUTH_REQUIRED if status == 401
                else ErrorKind.URL_EXPIRED if status == 403 else ErrorKind.NOT_FOUND if status in (404, 410)
                else ErrorKind.NETWORK if status >= 500 else ErrorKind.UNKNOWN)
    elif isinstance(exc, TimeoutError) or type(exc).__name__ in ("Timeout", "ReadTimeout", "ConnectTimeout"):
        kind = ErrorKind.TIMEOUT
    elif type(exc).__name__ in ("ConnectionError", "ConnectionResetError", "ConnectionRefusedError"):
        kind = ErrorKind.NETWORK
    elif isinstance(exc, FileNotFoundError):
        kind = ErrorKind.NOT_FOUND
    else:
        text = str(exc)
        kind = next((k for rx, k in _PATTERNS if rx.search(text)), ErrorKind.UNKNOWN)
        m = _RETRY_AFTER.search(text)
        retry_after = float(m.group(1)) if m else None
    if kind == ErrorKind.RATE_LIMITED and retry_after is None:
        retry_after = 60.0
    return ProviderFailure(kind, provider, kind in RETRYABLE, retry_after, MESSAGES[kind])


_SIGNED = re.compile(r"https?://\S+")


def redact(text: str) -> str:
    """A message safe for the log: addresses lose their query (signatures, tokens)."""
    return _SIGNED.sub(lambda m: m.group(0).split("?", 1)[0] + ("?…" if "?" in m.group(0) else ""), str(text))


# ---- per-provider cooldowns -----------------------------------------------------------

class Cooldowns:
    """Providers that asked for a pause, until when. Thread-safe."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def note(self, failure: ProviderFailure) -> None:
        if failure.kind == ErrorKind.RATE_LIMITED and failure.provider:
            wait = min(600.0, failure.retry_after_seconds or 60.0)
            with self._lock:
                self._until[failure.provider] = max(self._until.get(failure.provider, 0.0), self._clock() + wait)

    def cooling(self, provider: str) -> float:
        """Seconds left before ``provider`` may be asked again for background work (0 = now)."""
        with self._lock:
            left = self._until.get(provider, 0.0) - self._clock()
            if left <= 0:
                self._until.pop(provider, None)
                return 0.0
            return left


cooldowns = Cooldowns()


# ---- bounded retry ----------------------------------------------------------------------

def retry(fn: Callable[[], T], provider: str = "", attempts: int = 3, base: float = 0.6, cap: float = 4.0,
          deadline: float = 40.0, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> T:
    """``fn()``, tried again (at most ``attempts`` in all, within ``deadline`` seconds) only for
    failures that can pass; exponential backoff with full jitter. Anything else, or the last
    failure, is raised as it is (a :class:`ProviderError` once it's classified)."""
    start = clock()
    for n in range(attempts):
        try:
            return fn()
        except Exception as exc:
            failure = classify(exc, provider)
            cooldowns.note(failure)
            out_of_time = clock() - start >= deadline
            if not failure.retryable or failure.kind == ErrorKind.URL_EXPIRED or n == attempts - 1 or out_of_time:
                raise ProviderError(failure, redact(str(exc))) from exc
            sleep(random.uniform(0, min(cap, base * (2 ** n))))
    raise AssertionError("unreachable")
