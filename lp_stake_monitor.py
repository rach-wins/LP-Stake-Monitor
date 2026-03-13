import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone

# ── Tokens (set via environment variables) ───────────────────────────────────
LP_BOT_TOKEN      = os.environ.get("LP_BOT_TOKEN")       # stage2capitalnetwork bot
INTERNAL_BOT_TOKEN = os.environ.get("INTERNAL_BOT_TOKEN") # internal Stage 2 workspace bot

# ── Config ────────────────────────────────────────────────────────────────────
MODE         = os.environ.get("SCAN_MODE", "TEST").upper()   # "TEST" or "PRODUCTION"
ALERT_CHANNEL = "C0ALNN4UBG9"                                # #lp-stake-monitor (both modes)
SCAN_DAYS    = int(os.environ.get("SCAN_DAYS", 1))           # default 1 day per spec

# ── Detection patterns ────────────────────────────────────────────────────────
HIGH_PATTERNS = [
    # Exclude 'secondary sale' context — that's a MEDIUM signal, not HIGH
    r'(?<!secondary )\b(sell|selling|sale)\b.{0,40}\b(my|our)\b.{0,20}\b(lp|stake|position|interest|shares)\b',
    r'\b(my|our)\b.{0,20}\b(lp|stake|position|interest|shares)\b.{0,40}\b(sell|selling|for sale|sale)\b',
    # Include 'buying' variant — 'anyone interested in buying my LP' is HIGH
    r'\banyone (want|interested).{0,30}\b(buy|buying|purchase)\b.{0,30}\b(lp|stake|position|interest)\b',
    r'\b(lp|stake|position|interest).{0,30}\bfor sale\b',
    r'\boffer(ing)?\b.{0,30}\b(my|our).{0,20}\b(position|stake|interest)\b',
    r'\blooking to (sell|offload|transfer|liquidate)\b.{0,40}\b(lp|stake|position|interest|fund)\b',
    r'\btransfer\b.{0,30}\b(my|our)\b.{0,20}\b(lp|stake|interest|position)\b',
    r'\b(exit|exiting)\b.{0,30}\b(my|our)\b.{0,20}\b(lp|stake|position|interest|fund)\b',
    r'\b(want|need|trying|looking).{0,20}\b(to exit|to liquidate|to sell)\b.{0,30}\b(lp|stake|position|interest|fund)\b',
    r'\bliquidate\b.{0,30}\b(my|our)\b.{0,20}\b(lp|stake|position|interest|fund)\b',
    r'\bliquidate.{0,10}(lp|stake|position)\b',
    r'\b(can i|could i|how (do i|can i))\b.{0,30}\b(sell|transfer|offload|exit)\b.{0,30}\b(lp|stake|position|interest|fund)\b',
]

MEDIUM_PATTERNS = [
    r'\bsecondary (market|sale|transaction).{0,40}(lp|stake|interest|position|fund)\b',
    r'\b(assign|assignment).{0,30}\b(lp|interest|stake)\b',
    r'\bbuyer.{0,40}(lp|stake|position|interest)\b',
    r'\boffload.{0,30}(stake|position|interest)\b',
    r'\bhow (do i|can i|does one).{0,40}(transfer|assign).{0,30}(lp|stake|interest|position)\b',
]

LOW_PATTERNS = [
    r'\bthinking (of|about) (exiting|leaving|selling)\b',
    r'\btake over my spot\b',
    r'\banyone interested in taking over\b',
]

SAFE_PATTERNS = [
    r'\bfund sold a portfolio\b',
    r'\bcapital call\b.*\bwire\b',
    r'\bwire\b.*\bcapital call\b',
    r'\bexit strategy for portfolio compan\b',
    r'\bportfolio compan\b.*\bexit\b',
    r'\bbuying more allocation\b',
    r'\bfuture fund\b',
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def slack_get(token, endpoint, params=None):
    url = "https://slack.com/api/" + endpoint
    headers = {"Authorization": "Bearer " + token}
    for attempt in range(3):
        r = requests.get(url, headers=headers, params=params or {})
        if r.status_code == 429:
            print("  Rate limited, waiting 1s...")
            time.sleep(1)
            continue
        data = r.json()
        if not data.get("ok"):
            print("  API error on " + endpoint + ": " + str(data.get("error")))
        return data
    return {"ok": False, "error": "max_retries_exceeded"}


def slack_post(token, endpoint, payload):
    url = "https://slack.com/api/" + endpoint
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload)
    return r.json()


def ts_to_permalink(channel_id, ts):
    ts_no_dot = ts.replace(".", "")
    return "https://stage2capitalnetwork.slack.com/archives/" + channel_id + "/p" + ts_no_dot


def ts_to_datetime(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")


# ── Detection ─────────────────────────────────────────────────────────────────
def check_message(text):
    """Return (confidence, reason) or (None, None) if no flag."""
    text_lower = text.lower()
    # Safe patterns override everything — skip if matched
    for pat in SAFE_PATTERNS:
        if re.search(pat, text_lower):
            return None, None
    for pat in HIGH_PATTERNS:
        if re.search(pat, text_lower, re.IGNORECASE):
            return "HIGH", "Message contains explicit language about selling, transferring, or liquidating an LP stake."
    for pat in MEDIUM_PATTERNS:
        if re.search(pat, text_lower, re.IGNORECASE):
            return "MEDIUM", "Message contains language suggesting secondary market interest or stake transfer inquiry."
    for pat in LOW_PATTERNS:
        if re.search(pat, text_lower, re.IGNORECASE):
            return "LOW", "Message contains soft signals suggesting the LP may be considering exiting their position."
    return None, None


# ── Alert builder ─────────────────────────────────────────────────────────────
def build_alert(display_name, user_id, channel_name, channel_id, ts, text, confidence, reason):
    dt   = ts_to_datetime(ts)
    link = ts_to_permalink(channel_id, ts)

    if MODE == "TEST":
        return (
            "🧪 *[TEST MODE] LP Stake Solicitation Detected*\n"
            "_This is a test alert. Switch to production when ready._\n\n"
            "*Poster:* " + display_name + " (" + user_id + ")\n"
            "*Channel:* #" + channel_name + "\n"
            "*Timestamp:* " + dt + "\n"
            "*Message Link:* " + link + "\n\n"
            "*Flagged Message:*\n> " + text + "\n\n"
            "*Confidence:* " + confidence + "\n"
            "*Reason:* " + reason + "\n\n"
            "_Recommended action: Reach out to this LP 1:1 to address the solicitation privately._"
        )
    else:
        return (
            "🚨 *LP Stake Solicitation Detected* 🚨\n\n"
            "*Poster:* " + display_name + " (" + user_id + ")\n"
            "*Channel:* #" + channel_name + "\n"
            "*Timestamp:* " + dt + "\n"
            "*Message Link:* " + link + "\n\n"
            "*Flagged Message:*\n> " + text + "\n\n"
            "*Confidence:* " + confidence + "\n"
            "*Reason:* " + reason + "\n\n"
            "_Recommended action: Reach out to this LP 1:1 to address the solicitation privately._"
        )


# ── Alert sender ──────────────────────────────────────────────────────────────
def send_alert(alert_text):
    """Always posts to #lp-stake-monitor regardless of mode."""
    result = slack_post(INTERNAL_BOT_TOKEN, "chat.postMessage", {
        "channel": ALERT_CHANNEL,
        "text": alert_text,
        "mrkdwn": True
    })
    return result.get("ok", False)


# ── Main scan ─────────────────────────────────────────────────────────────────
def run_scan():
    print("=" * 60)
    print("  LP Stake Monitor — Stage 2 Capital")
    print("  Mode: " + MODE + " | Scan window: Last " + str(SCAN_DAYS) + " day(s)")
    print("=" * 60)

    oldest_ts = str((datetime.utcnow() - timedelta(days=SCAN_DAYS)).timestamp())

    # Step 3 — Fetch all channels
    print("Fetching channels...")
    channels = []
    cursor = None
    while True:
        params = {"types": "public_channel", "exclude_archived": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = slack_get(LP_BOT_TOKEN, "conversations.list", params)
        if not data.get("ok"):
            print("Failed to list channels: " + str(data.get("error")))
            return
        channels.extend(data.get("channels", []))
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    print("Found " + str(len(channels)) + " channels")

    # Step 4 — Join all channels
    print("Joining channels...")
    for ch in channels:
        slack_post(LP_BOT_TOKEN, "conversations.join", {"channel": ch["id"]})

    # Step 5 — Fetch messages
    print("Fetching messages...")
    all_messages = []
    skipped_channels = []

    for ch in channels:
        ch_id   = ch["id"]
        ch_name = ch["name"]
        ch_messages = []
        cursor = None
        while True:
            params = {"channel": ch_id, "oldest": oldest_ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = slack_get(LP_BOT_TOKEN, "conversations.history", params)
            if not data.get("ok"):
                err = data.get("error", "unknown")
                if err in ("not_in_channel", "channel_not_found", "missing_scope"):
                    skipped_channels.append(ch_name)
                else:
                    print("  Error in #" + ch_name + ": " + err)
                break
            msgs = data.get("messages", [])
            for m in msgs:
                if m.get("subtype") == "bot_message":
                    continue
                if not m.get("text", "").strip():
                    continue
                ch_messages.append({
                    "channel_id":   ch_id,
                    "channel_name": ch_name,
                    "ts":           m["ts"],
                    "user":         m.get("user", ""),
                    "text":         m["text"]
                })
            if data.get("has_more"):
                cursor = data.get("response_metadata", {}).get("next_cursor")
            else:
                break
        print("  #" + ch_name + ": " + str(len(ch_messages)) + " messages")
        all_messages.extend(ch_messages)

    print("Total messages: " + str(len(all_messages)))

    # Step 6 — Resolve usernames
    print("Resolving usernames...")
    user_cache = {}
    unique_users = set(m["user"] for m in all_messages if m["user"])
    for uid in unique_users:
        data = slack_get(LP_BOT_TOKEN, "users.info", {"user": uid})
        if data.get("ok"):
            profile = data["user"]["profile"]
            name = profile.get("display_name") or data["user"].get("real_name", uid)
            user_cache[uid] = name
        else:
            user_cache[uid] = uid

    # Step 7 — Scan messages
    print("Scanning for stake solicitations...")
    flags = []
    for msg in all_messages:
        confidence, reason = check_message(msg["text"])
        if confidence:
            display_name = user_cache.get(msg["user"], msg["user"])
            flags.append({**msg, "display_name": display_name, "confidence": confidence, "reason": reason})

    # Steps 8 + 9 — Build and send alerts
    alerts_sent = 0
    for flag in flags:
        alert_text = build_alert(
            flag["display_name"], flag["user"],
            flag["channel_name"], flag["channel_id"],
            flag["ts"], flag["text"],
            flag["confidence"], flag["reason"]
        )
        sent   = send_alert(alert_text)
        status = "sent" if sent else "FAILED to send"
        print("Flag [" + flag["confidence"] + "] #" + flag["channel_name"] +
              " — " + flag["display_name"] + " — alert " + status)
        if sent:
            alerts_sent += 1

    # Step 10 — Summary
    print("=" * 60)
    timeframe = "Last " + str(SCAN_DAYS) + " day(s)"
    if flags:
        print("✅ LP Stake Scan Complete [" + MODE + "]")
        print("Scanned: " + str(len(channels)) + " channels | " +
              str(len(all_messages)) + " messages | " + timeframe)
        print("🚨 " + str(len(flags)) + " flag(s) found, " + str(alerts_sent) + " alert(s) sent to #lp-stake-monitor:")
        for f in flags:
            preview = f["text"][:80] + "..." if len(f["text"]) > 80 else f["text"]
            print("  - #" + f["channel_name"] + " — " + f["display_name"] + " — " + f["confidence"] + " confidence")
            print('    "' + preview + '"')
    else:
        print("✅ LP Stake Scan Complete [" + MODE + "] — No Violations Found")
        print("Scanned: " + str(len(channels)) + " channels | " +
              str(len(all_messages)) + " messages | " + timeframe)
        print("No stake solicitation messages detected. Community looks clean. 👍")

    if skipped_channels:
        print("Skipped (access denied): " + ", ".join(skipped_channels))

    print("=" * 60)


if __name__ == "__main__":
    run_scan()
