"""Shared HTTP session."""

from __future__ import annotations

import threading

try:
    # Use the OS certificate store, so HTTPS works behind antivirus / corporate
    # TLS inspection (certifi alone rejects those certificates).
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

import requests
from requests.adapters import HTTPAdapter

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
TIMEOUT = 12

_local = threading.local()


def session() -> requests.Session:
    """One pooled session per thread (requests sessions aren't strictly thread-safe)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        s.mount("https://", HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=1))
        _local.session = s
    return s


def get(url: str, **kw) -> requests.Response:
    kw.setdefault("timeout", TIMEOUT)
    r = session().get(url, **kw)
    r.raise_for_status()
    return r
