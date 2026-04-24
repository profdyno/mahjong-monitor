"""Notification sinks. Everything is best-effort - a failed notifier should
never crash the monitor loop."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys

import requests


log = logging.getLogger(__name__)


def notify(config: dict, title: str, message: str, url: str) -> None:
    body = f"{message}\n{url}"

    if config.get("desktop"):
        _desktop(title, body)

    webhook = config.get("webhook_url")
    if webhook:
        _webhook(webhook, f"{title}: {message} {url}")

    topic = config.get("ntfy_topic")
    if topic:
        _ntfy(topic, title, body, url)

    print(f"[notify] {title} - {message} - {url}", file=sys.stdout, flush=True)


def _desktop(title: str, body: str) -> None:
    try:
        if shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "--urgency=critical", title, body],
                check=False,
                timeout=5,
            )
        elif shutil.which("osascript"):
            script = f'display notification "{body}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception as e:
        log.warning("desktop notify failed: %s", e)


def _webhook(url: str, text: str) -> None:
    try:
        requests.post(url, json={"text": text, "content": text}, timeout=10)
    except Exception as e:
        log.warning("webhook notify failed: %s", e)


def _ntfy(topic: str, title: str, body: str, click_url: str) -> None:
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high",
                "Tags": "shopping_cart",
                "Click": click_url,
            },
            timeout=10,
        )
    except Exception as e:
        log.warning("ntfy notify failed: %s", e)
