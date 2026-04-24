# mahjong-monitor

Polls a handful of product pages for out-of-stock → in-stock transitions on a
Mac Studio. Sends a desktop notification (and optionally phone push via
ntfy.sh) when a set comes back.

## What this is, in one paragraph

`monitor.py` starts one thread per configured site. Each thread has its own
`requests.Session` with a pinned real-browser identity (UA + matching
`sec-ch-ua` client hints + full navigation headers + its own cookie jar),
polls on a jittered per-site interval, and only runs inside a configured
daily time window. Stock state persists in `.state.json`; notifications only
fire on the OOS → in-stock *transition*, so restarts don't spam. No headless
browser — everything is plain HTTP + HTML parsing, which keeps CPU near zero.

## Setup on the Mac (one-time)

```bash
brew install python@3.12
cd ~/mahjong-monitor
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`config.yaml` is checked in and already points at the three Crisloid URLs
with a 10:00–13:00 America/New_York poll window. Don't commit edits to
`config.yaml` that contain secrets (webhook URLs, ntfy topics) — treat those
as local-only.

## Running

Foreground, for testing:

```bash
source .venv/bin/activate
python monitor.py --config config.yaml -v
```

One-shot poll of every site (useful after editing detectors):

```bash
python monitor.py --config config.yaml --once -v
```

Background via `launchd` (see README section "Keep it running" in chat
history, or create `~/Library/LaunchAgents/com.you.mahjong-monitor.plist`
with `KeepAlive=true` and `RunAtLoad=true`, pointing at
`.venv/bin/python monitor.py --config config.yaml`). Logs go to
`/tmp/mahjong-monitor.log`.

## What a healthy run looks like

Inside the poll window:

```
INFO [Crisloid - Mother Nature Mahjong Set] in_stock=False reason=selector 'p.stock.out-of-stock, .out-of-stock' matched
INFO [Crisloid - 9 Florence Mahjong (Creamy White)] in_stock=False reason=wc variation tile-color=Creamy White -> False
```

Outside the window:

```
INFO [Crisloid - ...] outside poll window, sleeping 68400s
```

`in_stock=None` means no clean read (network error, 403, anti-bot
interstitial, robots.txt). One-off `None`s are fine; a streak of them means
something upstream changed — investigate before assuming the site is just
down.

## Key design rules (do not break these casually)

- **One browser identity per site, pinned.** Real browsers don't reshuffle
  UA per request. `SiteSession` in `src/stealth.py` picks one profile at
  startup and keeps it. Don't rotate mid-session.
- **UA and `sec-ch-ua` must stay consistent.** Profiles in
  `_BROWSER_PROFILES` pair them deliberately — a Chrome UA with a missing
  or Firefox-shaped client hint is a classic bot tell. If you add a
  profile, capture both from the same real browser.
- **Poll intervals are per-site and randomized.** Never hard-code a single
  global interval or synchronize threads. `jittered_interval` also has a
  10% chance of a 1.5–2.5× "distracted human" pause — keep that.
- **Notify only on transitions.** The check in `run_site_loop` is
  `if in_stock and not prev`. If you change state schema, preserve this
  semantics or the user will get spammed on restart.
- **Respect the poll window.** `clip_to_window` both waits for window open
  and clips any scheduled sleep so polls never drift past window close.
  Don't add a code path that polls outside the window.
- **Respect robots.txt** via `src/robots.py`. If a site disallows, log and
  skip — don't override without asking the user.
- **Back off on 429/503.** `backoff_minutes` (default 60) plus jitter.
  Don't retry fast.
- **Detect anti-bot interstitials** (`"just a moment"`, Cloudflare,
  PerimeterX, etc.) and return `in_stock=None`, not a parsed verdict.
  Parsing a challenge page as "in stock" would fire a false notification.

## Adding or editing a site

Edit `config.yaml`. Each site takes:

```yaml
- name: "Human-readable label"
  url: "https://..."
  min_interval_minutes: 25
  max_interval_minutes: 55
  warmup_url: "https://site-root/"   # optional; null to skip
  stock_checks:
    - type: <one of the types below>
      value: "..."
```

Detectors are tried in order; the first that yields a definitive True/False
wins. Available check types (see `src/detector.py`):

- `json_ld` — parses `<script type="application/ld+json">` for
  `offers.availability`. Works on most well-built ecom sites.
- `text_absent` / `text_present` — case-insensitive substring on the
  rendered page text (not raw HTML).
- `css_absent` / `css_present` — BeautifulSoup CSS selector.
- `regex_absent` / `regex_present` — regex against raw HTML,
  case-insensitive, DOTALL. Escape hatch for weird cases.
- `wc_variation` — WooCommerce variable-product lookup. Takes
  `attribute` (e.g. `tile-color`) and `value` (e.g. `Creamy White`);
  reads `form.variations_form[data-product_variations]` JSON and returns
  the matching variant's `is_in_stock`.

When a site ships a new layout, the old detectors may flip to always-true
or always-false. First sign: `in_stock=True` for a URL that's obviously
still sold out on the site. Fix by capturing the new HTML and adjusting.

## Verifying detectors when you can't hit the site

The sandbox can't reach crisloid.com, and you may not want to hammer the
real site either. Synthesize minimal HTML that mimics the target structure
and feed it to `is_in_stock` directly:

```bash
python -c "
import sys; sys.path.insert(0, '.')
from src.detector import is_in_stock
html = open('/tmp/sample.html').read()
print(is_in_stock(html, [{'type':'wc_variation','attribute':'tile-color','value':'Creamy White'}]))
"
```

To capture a real sample once the service is running on a residential IP:
open the URL in Chrome, View Source, save to `/tmp/sample.html`. Never
paste cookies or auth headers into a sample file.

## Things to NOT do

- Don't add a headless browser (Playwright/Selenium) unless a specific site
  genuinely needs JS rendering. It's a large dependency, slower, and its
  default fingerprints are *more* bot-like than plain `requests`, not less.
- Don't lower the poll interval below ~10 minutes per site "just to catch
  it faster". Frequent polling is the single most common trigger for IP
  bans. If the user asks for faster, explain the tradeoff before doing it.
- Don't commit `.state.json`, or any config containing a webhook URL /
  ntfy topic / proxy credential. `.gitignore` already covers `.state.json`.
- Don't push to `main` or create a PR unless the user explicitly asks.
  Work on the current feature branch.
- Don't run the service against a new site without first checking
  `robots.txt` and the site's ToS. For personal shopping-bookmark use the
  legal risk is near zero; for anything larger, ask.

## Project layout

```
monitor.py                # entrypoint, thread-per-site loop, window logic
src/stealth.py            # SiteSession, browser profiles, jitter
src/detector.py           # is_in_stock + all check types
src/notifier.py           # desktop / webhook / ntfy dispatch
src/state.py              # atomic JSON state persistence
src/robots.py             # cached robots.txt enforcement
config.yaml               # live config (Crisloid URLs, ET window)
config.example.yaml       # documented template
```

## Troubleshooting cheatsheet

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `in_stock=None reason=http 403` on every poll | IP flagged / Cloudflare | Move to a residential IP; add `global.proxy`; or slow polling further |
| `reason=anti-bot interstitial detected` | Challenge page returned | Same as above; also verify UA profile is current |
| `reason=blocked by robots.txt` | Site disallows the path for our UA | Do not override — confirm with user before changing |
| `in_stock=True` for a URL that's still sold out on the site | Detector stale after layout change | Capture new HTML, adjust `stock_checks` |
| Two notifications in a row for the same URL | `.state.json` got wiped or corrupted | Check the file; it's atomic-written, so corruption is rare |
| No notifications ever | Outside poll window, or `desktop: false` and no webhook/ntfy | Check logs; confirm `notifications.*` config |
