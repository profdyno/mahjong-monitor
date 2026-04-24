"""HTTP client configured to look like a real browser.

Anti-fingerprinting strategy:
  - Rotate a small pool of real, current Chrome/Firefox/Safari UA strings.
    We don't randomize every request - real browsers don't change UA mid-
    session. One UA is pinned per site session.
  - Send the full set of headers a real browser sends, in the order it
    sends them, including Sec-Fetch-* and client hints that match the UA.
  - Persist cookies per site (each site gets its own requests.Session) so
    subsequent visits look like an ongoing browsing session.
  - Randomized think-time between the warmup hit and the product hit,
    because humans don't fetch two pages in the same millisecond.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import requests


# Real UA strings captured from recent stable browsers. Each entry pairs the
# UA with the client-hint headers it would normally send, so they stay
# consistent - a mismatched `sec-ch-ua` is a classic bot tell.
_BROWSER_PROFILES: list[dict] = [
    {
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "accept_language": "en-US,en;q=0.9",
    },
    {
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"macOS"',
        "accept_language": "en-US,en;q=0.9",
    },
    {
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        ),
        "sec_ch_ua": None,
        "sec_ch_ua_mobile": None,
        "sec_ch_ua_platform": None,
        "accept_language": "en-US,en;q=0.5",
    },
    {
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.6 Safari/605.1.15"
        ),
        "sec_ch_ua": None,
        "sec_ch_ua_mobile": None,
        "sec_ch_ua_platform": None,
        "accept_language": "en-US,en;q=0.9",
    },
]


def _headers_for(profile: dict, *, referer: str | None, is_navigation: bool) -> dict:
    headers = {
        "User-Agent": profile["ua"],
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": profile["accept_language"],
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
    }
    if is_navigation:
        # Real top-level navigations often include these; XHRs don't.
        headers["Cache-Control"] = "max-age=0"
    if profile["sec_ch_ua"]:
        headers["sec-ch-ua"] = profile["sec_ch_ua"]
        headers["sec-ch-ua-mobile"] = profile["sec_ch_ua_mobile"]
        headers["sec-ch-ua-platform"] = profile["sec_ch_ua_platform"]
    if referer:
        headers["Referer"] = referer
    return headers


@dataclass
class SiteSession:
    """One persistent session per site, with a pinned browser identity."""

    profile: dict = field(default_factory=lambda: random.choice(_BROWSER_PROFILES))
    session: requests.Session = field(default_factory=requests.Session)
    last_url: str | None = None

    def get(
        self,
        url: str,
        *,
        timeout: int,
        proxy: str | None = None,
    ) -> requests.Response:
        headers = _headers_for(
            self.profile,
            referer=self.last_url,
            is_navigation=True,
        )
        proxies = {"http": proxy, "https": proxy} if proxy else None
        response = self.session.get(
            url,
            headers=headers,
            timeout=timeout,
            proxies=proxies,
            allow_redirects=True,
        )
        self.last_url = response.url
        return response


def human_pause(min_seconds: float = 1.5, max_seconds: float = 6.0) -> None:
    """Sleep a realistic amount of time between in-session clicks."""
    time.sleep(random.uniform(min_seconds, max_seconds))


def jittered_interval(min_minutes: float, max_minutes: float) -> float:
    """Return seconds to sleep before the next poll of a site.

    Uses a log-uniform-ish distribution so most intervals cluster around the
    middle but occasionally we wait a long time - that irregularity matters
    more for avoiding detection than the average rate does.
    """
    base = random.uniform(min_minutes, max_minutes)
    # 10% chance of a "distracted human" long pause.
    if random.random() < 0.10:
        base *= random.uniform(1.5, 2.5)
    return base * 60.0
