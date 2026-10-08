#!/usr/bin/env python3
"""
Barbican Conservatory Plant Adoption (2 Dec) ticket watcher.

Polls the Barbican event page every ~45s and alerts by email + ntfy push the
moment tickets look live. Standard library only.

STRONG signal -> urgent alert every 2 min for 20 min, then the bot stops:
  /node/226137/booking_button, the JSON feed behind the page's booking button.
  It returns [] today ("Checking availability..."). Anything else means time
  slots have been attached to the event, so tickets are bookable.

WEAK signals -> one urgent alert, then keep watching for the strong signal:
  - "not yet on sale" disappears from the event page
  - new wording like "book now" / "on sale from ..." appears
  - a new "plant-adoption" event link appears on the Young Barbican series page

Any other change to the event page's text sends a one-off "page changed" alert.
"""
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
import urllib.request
from email.message import EmailMessage
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- config ----
BASE = os.environ.get("BASE_URL", "https://www.barbican.org.uk")
EVENT_URL = f"{BASE}/whats-on/event/conservatory-plant-adoption"
BUTTON_URL = f"{BASE}/node/226137/booking_button"
SERIES_URL = f"{BASE}/whats-on/2026/series/young-barbican-events"
PUBLIC_EVENT_URL = "https://www.barbican.org.uk/whats-on/event/conservatory-plant-adoption"

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_APP_PASSWORD", "")
EMAIL_TO = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")

RUN_MINUTES = float(os.environ.get("RUN_MINUTES", "345"))   # GitHub job cap is 360
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "45"))
NAG_INTERVAL = float(os.environ.get("NAG_INTERVAL_SECONDS", "120"))
NAG_MINUTES = float(os.environ.get("NAG_MINUTES", "20"))
FAIL_THRESHOLD = int(os.environ.get("FAIL_THRESHOLD", "8"))   # ~6 min of failures
HEARTBEAT_HOUR = int(os.environ.get("HEARTBEAT_HOUR", "8"))
END_DATE = dt.date(2026, 12, 2)
STATE_FILE = os.environ.get("STATE_FILE", "state/state.json")
MODE = os.environ.get("MODE", "watch") or "watch"

LONDON = ZoneInfo("Europe/London")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")


def log(msg):
    print(f"[{dt.datetime.now(LONDON):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ----------------------------------------------------------------- state ----
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{key}={value}\n")


# ---------------------------------------------------------------- notify ----
def send_email(subject, body):
    if not (SMTP_USER and SMTP_PASS and EMAIL_TO):
        log("email not configured, skipping")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Plant Adoption Watcher <{SMTP_USER}>"
    msg["To"] = ", ".join(EMAIL_TO)
    msg["X-Priority"] = "1"
    msg["Importance"] = "high"
    msg.set_content(body)
    for attempt in range(3):
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(),
                                  timeout=30) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
            log(f"email sent: {subject}")
            return True
        except Exception as e:
            log(f"email failed (attempt {attempt + 1}): {e!r}")
            time.sleep(5)
    return False


def send_push(title, message, priority="default", tags="", click=PUBLIC_EVENT_URL):
    if not NTFY_TOPIC:
        log("ntfy not configured, skipping")
        return False
    headers = {"Title": title, "Priority": priority, "Click": click}
    if tags:
        headers["Tags"] = tags
    req = urllib.request.Request(f"{NTFY_SERVER}/{NTFY_TOPIC}", data=message.encode(),
                                 headers=headers, method="POST")
    for attempt in range(3):
        try:
            urllib.request.urlopen(req, timeout=20).read()
            log(f"push sent: {title}")
            return True
        except Exception as e:
            log(f"push failed (attempt {attempt + 1}): {e!r}")
            time.sleep(3)
    return False


def notify(title, body, priority="default", tags="", email=True):
    send_push(title, body, priority=priority, tags=tags)
    if email:
        send_email(title, body)


# ----------------------------------------------------------------- fetch ----
def fetch(url, bust=True):
    if bust:  # bypass any CDN/page cache
        url += ("&" if "?" in url else "?") + f"_={int(time.time() * 1000)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, r.read().decode("utf-8", "replace")


def html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg|form)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6])>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def event_text(page_html):
    """The event's own copy: <main>, minus the login modal and nav chrome."""
    i, j = page_html.find("<main"), page_html.find("</main>")
    main = page_html[i:j] if i != -1 and j != -1 else page_html
    text = html_to_text(main)
    # Start at the event title to skip login/modal boilerplate if present.
    k = text.find("Bring home a living piece")
    return text[k:] if k != -1 else text


def check(state, poll_no):
    """Returns (strong, weak, change_diff). Raises on fetch/parse failure.

    strong: bookable slots exist (booking feed non-empty) -> nag + stop.
    weak:   the wording/links changed in a way that suggests a sale is live or
            announced -> one urgent alert, keep watching for the strong signal.
    """
    strong, weak = [], []

    # 1. booking button JSON
    status, body = fetch(BUTTON_URL)
    if status != 200:
        raise RuntimeError(f"booking_button HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError(f"booking_button not JSON: {body[:120]!r}")
    if data not in ([], {}, None, ""):
        strong.append(f"Booking button feed now has data: {json.dumps(data)[:300]}")

    # 2. event page text
    status, page = fetch(EVENT_URL)
    if status != 200 or "Conservatory Plant Adoption" not in page:
        raise RuntimeError(f"event page HTTP {status} / unexpected content")
    text = event_text(page)
    prev_text = state.get("page_text", "")
    if "not yet on sale" not in text.lower() and (not prev_text or "not yet on sale" in prev_text.lower()):
        weak.append('The words "not yet on sale" have gone from the event page.')
    if prev_text:  # only phrases that are NEW since the last check count
        for pat in (r"book now", r"now on sale", r"tickets are (now )?available",
                    r"select a (date|time)", r"on sale (now|from)"):
            m = re.search(pat, text, re.I)
            if m and not re.search(pat, prev_text, re.I):
                weak.append(f'Event page now says "{m.group(0)}".')

    # 3. series page, every ~5th poll (lighter touch)
    if poll_no % 5 == 0:
        try:
            s_status, series = fetch(SERIES_URL)
            if s_status == 200:
                links = set(re.findall(r'href="([^"]*plant-adoption[^"]*)"', series, re.I))
                links.discard("/whats-on/event/conservatory-plant-adoption")
                links -= set(state.get("seen_links", []))
                if links:
                    state["seen_links"] = sorted(set(state.get("seen_links", [])) | links)
                    weak.append("New plant adoption page on Young Barbican series: "
                                   + ", ".join(sorted(links)))
        except Exception as e:
            log(f"series check failed (ignored): {e!r}")

    # Generic change detection on the event copy
    digest = hashlib.sha256(text.encode()).hexdigest()
    diff = None
    if state.get("page_hash") and state["page_hash"] != digest:
        old = state.get("page_text", "").splitlines()
        diff = "\n".join(list(difflib.unified_diff(old, text.splitlines(), "before", "now",
                                                   lineterm="", n=1))[:60])
    state["page_hash"], state["page_text"] = digest, text
    return strong, weak, diff


# ------------------------------------------------------------------ main ----
def nag(reasons):
    body = (
        "Barbican Conservatory Plant Adoption tickets for 2 December look LIVE.\n\n"
        f"BOOK NOW: {PUBLIC_EVENT_URL}\n\n"
        "Why the bot thinks so:\n- " + "\n- ".join(reasons) + "\n\n"
        "Free tickets, 40 per time slot. They will go fast.\n"
        f"(Repeating every {int(NAG_INTERVAL // 60)} min for {int(NAG_MINUTES)} min, then stopping.)"
    )
    end = time.time() + NAG_MINUTES * 60
    n = 0
    while True:
        n += 1
        tag = "" if n == 1 else f" (reminder {n - 1})"
        notify(f"PLANT ADOPTION TICKETS LIVE - BOOK NOW{tag}", body,
               priority="urgent", tags="rotating_light,potted_plant")
        if time.time() + NAG_INTERVAL > end:
            break
        time.sleep(NAG_INTERVAL)


def main():
    state = load_state()
    now = dt.datetime.now(LONDON)

    if MODE == "test":
        notify("TEST: Plant adoption watcher is set up",
               "This is a test alert from your Barbican plant adoption watcher.\n"
               f"If tickets go live you'll get urgent alerts like this, linking to:\n{PUBLIC_EVENT_URL}",
               priority="high", tags="white_check_mark,potted_plant")
        return

    if state.get("alerted"):
        log("Already alerted on a previous run; stopping.")
        set_output("stop", "true")
        return
    if now.date() > END_DATE:
        log("Event date has passed; stopping.")
        set_output("stop", "true")
        return

    deadline = time.time() + RUN_MINUTES * 60
    fails, poll_no, checks_today = 0, 0, 0
    last_err = ""
    while time.time() < deadline:
        poll_no += 1
        now = dt.datetime.now(LONDON)
        try:
            strong, weak, diff = check(state, poll_no)
            checks_today += 1
            if fails >= FAIL_THRESHOLD:
                send_push("Plant watcher recovered", "Reading the Barbican page again.", tags="ok")
            fails = 0
            state["last_ok"] = now.isoformat()
            if strong:
                log("LIVE: " + " | ".join(strong + weak))
                state["alerted"] = True
                save_state(state)
                nag(strong + weak)
                set_output("stop", "true")
                return
            if weak:
                log("Possible sale / announcement: " + " | ".join(weak))
                notify("PLANT ADOPTION: tickets may be live - CHECK NOW",
                       "The Barbican page has changed in a way that suggests tickets are on sale "
                       "or a sale time has been announced.\n\n"
                       f"CHECK NOW: {PUBLIC_EVENT_URL}\n\nSignals:\n- " + "\n- ".join(weak) +
                       (f"\n\nWhat changed:\n{diff}" if diff else "") +
                       "\n\nThe bot keeps watching and will send repeated urgent alerts once "
                       "bookable time slots appear.",
                       priority="urgent", tags="rotating_light,potted_plant")
            elif diff:
                log("Page changed (not obviously live):\n" + diff)
                notify("Barbican plant adoption page changed",
                       "The event page text changed, but tickets don't look live yet.\n"
                       f"Check it: {PUBLIC_EVENT_URL}\n\nWhat changed:\n{diff}",
                       priority="high", tags="eyes,potted_plant")
            else:
                log(f"poll {poll_no}: not on sale yet")
        except Exception as e:
            fails += 1
            last_err = repr(e)[:300]
            log(f"poll {poll_no} FAILED ({fails} in a row): {last_err}")
            if fails == FAIL_THRESHOLD:
                notify("Plant watcher can't read the Barbican page",
                       f"{fails} checks in a row have failed. Last error:\n{last_err}\n\n"
                       f"It keeps retrying. Worth checking the page yourself: {PUBLIC_EVENT_URL}",
                       priority="high", tags="warning")

        # daily heartbeat (push only)
        today = now.date().isoformat()
        if now.hour >= HEARTBEAT_HOUR and state.get("heartbeat_date") != today:
            ok = "OK" if fails == 0 else f"FAILING ({last_err[:80]})"
            send_push("Plant watcher: still watching",
                      f"Status {ok}. Not on sale yet. Polling every ~{int(POLL_SECONDS)}s.",
                      priority="low", tags="seedling")
            state["heartbeat_date"] = today
        save_state(state)

        time.sleep(max(5.0, POLL_SECONDS + random.uniform(-5, 5)))

    log("Run window finished; next scheduled run takes over.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
