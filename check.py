#!/usr/bin/env python3
"""
Apple UAE (apple.com/ae) in-store pickup watcher.
Target: iPhone 18 Pro Max 256GB Burgundy (Gulf eSIM version).

Polls Apple's retail pickup API and sends a Telegram message when the
phone becomes available for pickup at any UAE Apple Store.

No third-party dependencies. Python 3.8+.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

COUNTRY = "ae"
API = "https://www.apple.com/{}/shop/retail/pickup-message".format(COUNTRY)
BUY_LINK = "https://www.apple.com/{}/shop/buy-iphone".format(COUNTRY)

DEFAULT_PARTS = "MJX74AH/A,MJX74AE/A"
DEFAULT_LOCATIONS = "Dubai,Abu Dhabi"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-AE,en;q=0.9",
    "Referer": BUY_LINK,
}


def env_list(name, default):
    raw = os.environ.get(name, default) or default
    return [x.strip() for x in raw.split(",") if x.strip()]


PARTS = env_list("PARTS", DEFAULT_PARTS)
LOCATIONS = env_list("LOCATIONS", DEFAULT_LOCATIONS)
TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state/state.json")
REPEAT_SEC = int(os.environ.get("REPEAT_ALERT_SEC", "1800"))
DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


def fetch(location, part):
    """Query Apple's pickup availability API for one location + part number."""
    params = urllib.parse.urlencode(
        {"parts.0": part, "location": location, "_": int(time.time() * 1000)}
    )
    req = urllib.request.Request("{}?{}".format(API, params), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_stores(data):
    """Return the store list from either the new or legacy response shape."""
    body = data.get("body") or {}
    stores = body.get("stores")
    if stores is None:
        try:
            stores = body["content"]["pickupMessage"]["stores"]
        except (KeyError, TypeError):
            stores = []
    return stores or []


def check_location(location, part):
    """Return a list of (store_name, product_title) that are available."""
    hits = []
    try:
        data = fetch(location, part)
    except Exception as exc:  # noqa: BLE001 - any network failure is tolerable
        print("[warn] {} / {}: request failed: {}".format(location, part, exc), flush=True)
        return hits
    for store in extract_stores(data):
        name = store.get("storeName") or store.get("city") or "unknown store"
        for part_num, info in (store.get("partsAvailability") or {}).items():
            status = info.get("pickupDisplay")
            title = info.get("storePickupProductTitle") or part_num
            if DEBUG:
                print("[debug] {} / {} -> {} : {}".format(location, name, part_num, status), flush=True)
            if status == "available":
                hits.append((name, title))
    return hits


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    directory = os.path.dirname(STATE_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh)


def telegram(method, params):
    if not TG_TOKEN:
        return None
    url = "https://api.telegram.org/bot{}/{}".format(TG_TOKEN, method)
    req = urllib.request.Request(
        url,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("[warn] telegram {} failed: {}".format(method, exc), flush=True)
        return None


def discover_chat_id():
    result = telegram("getUpdates", {"timeout": 0}) or {}
    for update in result.get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id:
            return str(chat_id)
    return ""


def notify(text):
    global TG_CHAT_ID
    if not TG_CHAT_ID:
        TG_CHAT_ID = discover_chat_id()
    if not TG_CHAT_ID:
        print("[warn] no chat id known; message not sent: {}".format(text), flush=True)
        return
    telegram("sendMessage", {"chat_id": TG_CHAT_ID, "text": text})


def main():
    if not TG_TOKEN:
        print("[error] TG_TOKEN is not set", flush=True)
        return 1

    state = load_state()
    now = time.time()
    current = {}

    for location in LOCATIONS:
        for part in PARTS:
            for store, title in check_location(location, part):
                key = "{} | {}".format(store, title)
                current[key] = (store, title)

    for key, (store, title) in sorted(current.items()):
        prev = state.get(key) or {}
        recently_alerted = prev.get("available") and (now - prev.get("t", 0) < REPEAT_SEC)
        if not recently_alerted:
            notify(
                "IN STOCK NOW: {}\nStore: {}\nPickup: available today\n{}".format(
                    title, store, BUY_LINK
                )
            )
            print("[ALERT] {} @ {}".format(title, store), flush=True)
        state[key] = {"available": True, "t": now if not recently_alerted else prev.get("t", now)}

    for key, info in list(state.items()):
        if key not in current and info.get("available"):
            state[key]["available"] = False
            print("[info] no longer available: {}".format(key), flush=True)

    save_state(state)

    if not current:
        print("[info] no pickup availability found yet", flush=True)
        print("[info] checked locations: {}; parts: {}".format(", ".join(LOCATIONS), ", ".join(PARTS)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
