"""Thin wrapper around urllib.robotparser with per-host caching."""

from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser


_cache: dict[str, RobotFileParser] = {}


def allowed(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return True
    host = f"{parsed.scheme}://{parsed.netloc}"
    rp = _cache.get(host)
    if rp is None:
        rp = RobotFileParser()
        rp.set_url(f"{host}/robots.txt")
        try:
            rp.read()
        except Exception:
            # If robots.txt is unreachable, default to allowing - most sites
            # without one expect to be crawled.
            _cache[host] = _AllowAll()
            return True
        _cache[host] = rp
    return rp.can_fetch(user_agent, url)


class _AllowAll:
    def can_fetch(self, *_args, **_kwargs) -> bool:
        return True
