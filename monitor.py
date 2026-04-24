"""Mahjong set stock monitor.

Polls each configured site on its own randomized schedule, looks like a real
browser doing it, and fires a notification when a product transitions from
out-of-stock to in-stock.

Usage:
    python monitor.py --config config.yaml [--once]
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import threading
import time
from dataclasses import dataclass

import requests
import yaml

from src import notifier, robots, state
from src.detector import is_in_stock
from src.stealth import SiteSession, human_pause, jittered_interval


log = logging.getLogger("mahjong-monitor")


@dataclass
class Site:
    name: str
    url: str
    min_interval_minutes: float
    max_interval_minutes: float
    warmup_url: str | None
    stock_checks: list[dict]


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_sites(config: dict) -> list[Site]:
    sites = []
    for raw in config.get("sites", []):
        sites.append(
            Site(
                name=raw["name"],
                url=raw["url"],
                min_interval_minutes=float(raw.get("min_interval_minutes", 15)),
                max_interval_minutes=float(raw.get("max_interval_minutes", 30)),
                warmup_url=raw.get("warmup_url"),
                stock_checks=raw.get("stock_checks", []),
            )
        )
    return sites


def check_site(site: Site, config: dict, session: SiteSession) -> tuple[bool | None, str]:
    """Fetch the site and return (in_stock, reason). in_stock=None means
    we couldn't get a clean read (network error, blocked, etc.)."""
    timeout = config["global"]["request_timeout"]
    proxy = config["global"].get("proxy")
    ua = session.profile["ua"]

    if not robots.allowed(site.url, ua):
        return None, "blocked by robots.txt"

    try:
        if site.warmup_url and session.last_url is None:
            resp = session.get(site.warmup_url, timeout=timeout, proxy=proxy)
            log.debug("[%s] warmup %s -> %s", site.name, site.warmup_url, resp.status_code)
            human_pause()

        resp = session.get(site.url, timeout=timeout, proxy=proxy)
    except requests.RequestException as e:
        return None, f"request failed: {e}"

    if resp.status_code in (429, 503):
        return None, f"rate-limited: {resp.status_code}"
    if resp.status_code >= 400:
        return None, f"http {resp.status_code}"

    # Basic anti-bot landing page sniff - Cloudflare / PerimeterX / etc.
    body = resp.text
    lowered = body.lower()
    tells = ("just a moment", "cf-browser-verification", "attention required",
             "access denied", "px-captcha")
    if any(t in lowered for t in tells):
        return None, "anti-bot interstitial detected"

    in_stock, reason = is_in_stock(body, site.stock_checks)
    return in_stock, reason


def run_site_loop(site: Site, config: dict, st: dict, st_lock: threading.Lock,
                  stop: threading.Event, once: bool) -> None:
    # Each site gets its own pinned browser identity + cookie jar.
    session = SiteSession()
    # Random initial delay so all the loops don't fire at startup.
    first_delay = random.uniform(0, 60) if not once else 0
    if first_delay and stop.wait(first_delay):
        return

    while not stop.is_set():
        in_stock, reason = check_site(site, config, session)
        log.info("[%s] in_stock=%s reason=%s", site.name, in_stock, reason)

        if in_stock is not None:
            with st_lock:
                prev = st.get(site.url, {}).get("in_stock")
                st[site.url] = {"in_stock": in_stock, "reason": reason, "ts": time.time()}
                state.save(config["global"]["state_file"], st)

            # Only notify on the OOS -> in-stock transition. First-ever
            # sighting of in-stock also notifies.
            if in_stock and not prev:
                notifier.notify(
                    config["notifications"],
                    title="Mahjong set IN STOCK",
                    message=f"{site.name} ({reason})",
                    url=site.url,
                )

        if once:
            return

        if in_stock is None and "rate-limited" in (reason or ""):
            # Back off hard on 429/503.
            backoff = config["global"]["backoff_minutes"] * 60
            backoff += random.uniform(0, backoff * 0.25)
            log.info("[%s] backing off %.0fs", site.name, backoff)
            if stop.wait(backoff):
                return
            continue

        sleep_for = jittered_interval(site.min_interval_minutes, site.max_interval_minutes)
        log.debug("[%s] next poll in %.0fs", site.name, sleep_for)
        if stop.wait(sleep_for):
            return


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true",
                        help="Check every site once and exit.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = load_config(args.config)
    sites = build_sites(config)
    if not sites:
        log.error("no sites configured")
        return 2

    st = state.load(config["global"]["state_file"])
    st_lock = threading.Lock()
    stop = threading.Event()

    threads = []
    for site in sites:
        t = threading.Thread(
            target=run_site_loop,
            args=(site, config, st, st_lock, stop, args.once),
            name=f"site:{site.name}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    try:
        if args.once:
            for t in threads:
                t.join()
        else:
            while any(t.is_alive() for t in threads):
                time.sleep(1)
    except KeyboardInterrupt:
        log.info("shutting down")
        stop.set()
        for t in threads:
            t.join(timeout=10)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
