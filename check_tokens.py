"""
check_tokens.py
───────────────
Standalone Groq token budget checker. Run from the project root.

Commands:
    python check_tokens.py            # rolling-24h local estimate
    python check_tokens.py --probe    # live check via a real Groq API call
    python check_tokens.py --models   # all Groq models + their limits
    python check_tokens.py --history  # all-time usage log grouped by day

HONEST EXPLANATION OF WHAT'S POSSIBLE
──────────────────────────────────────
Groq tracks your usage in a rolling 24-hour window on their backend,
but they do NOT expose a "how many tokens do I have left?" API endpoint
that you can call proactively. This means there is no way to ask Groq
"show me my current TPD balance" — the number only surfaces when you
either hit the 429 error (which embeds "Used X, Limit Y" in the message)
or make a real API call and read the HTTP response headers.

This script gives you two levels of information:

  LOCAL ESTIMATE (default, no API call, free):
    Reads your local data/token_usage.jsonl log and adds up calls made
    through this app in the last 24 hours. Uses a rolling window (not
    a calendar day) to match Groq's own accounting. The weakness: if
    you used the same API key from another tool or browser session,
    those tokens aren't in our local log and the estimate will be
    optimistic. The tracker can only know what it witnessed.

  LIVE PROBE (--probe, costs ~30 tokens):
    Makes a real minimal API call to Groq and reads the HTTP response
    headers. Groq includes rate limit headers (x-ratelimit-*) in every
    response — these give you live per-minute (TPM) remaining capacity.
    If you are currently rate-limited (429), the error message contains
    the exact TPD numbers — "Used 97622, Limit 100000" — which this
    script parses and displays. This is the closest thing to a real-time
    balance check that Groq's API currently allows.

Requirements:
    pip install groq python-dotenv   (both already in requirements.txt)
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Load API key from .env
# ─────────────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# ─────────────────────────────────────────────────────────────────────────────
# Groq model catalogue — free tier limits as of May 2026
# ─────────────────────────────────────────────────────────────────────────────
GROQ_MODELS = {
    "llama-3.3-70b-versatile": {
        "tpd": 100_000, "tpm": 6_000,  "params": "70B",
        "our_use": "copilot_query() — complex reasoning",
    },
    "llama-3.1-8b-instant": {
        "tpd": 500_000, "tpm": 20_000, "params": "8B",
        "our_use": "parse_order() — structured extraction",
    },
    "mixtral-8x7b-32768": {
        "tpd": 500_000, "tpm": 5_000,  "params": "8x7B MoE",
        "our_use": "fallback if 70B TPD exhausted",
    },
    "llama-3.1-70b-versatile": {
        "tpd": 100_000, "tpm": 6_000,  "params": "70B",
        "our_use": "not currently used",
    },
    "llama3-8b-8192": {
        "tpd": 500_000, "tpm": 20_000, "params": "8B",
        "our_use": "not currently used",
    },
    "gemma2-9b-it": {
        "tpd": 500_000, "tpm": 15_000, "params": "9B",
        "our_use": "not currently used",
    },
}

# Average token cost per call type in our app — derived from actual usage
TYPICAL_COST = {"parse_order": 500, "copilot_query": 4500}

LOG_FILE = Path("data") / "token_usage.jsonl"

# ─────────────────────────────────────────────────────────────────────────────
# FIX: Rolling 24-hour window instead of calendar day
#
# Groq's rate limit is a rolling 24-hour window, not a midnight-to-midnight
# calendar day. If you made calls at 6pm yesterday, those tokens are still
# counting against your limit until 6pm today. Our original tracker used
# date == today which would miss those calls.
#
# The fix: instead of filtering by calendar date, filter by
# timestamp >= now - 24 hours. This mirrors exactly how Groq counts.
# ─────────────────────────────────────────────────────────────────────────────

def read_rolling_24h() -> list:
    """
    Returns all log records from the past 24 hours using a rolling window.
    This matches Groq's own accounting — calls made 23 hours ago still count.
    """
    if not LOG_FILE.exists():
        return []

    cutoff = datetime.now() - timedelta(hours=24)
    records = []

    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                # Parse the ISO timestamp and compare against the 24h cutoff
                ts = datetime.fromisoformat(r.get("timestamp", "2000-01-01T00:00:00"))
                if ts >= cutoff:
                    records.append(r)
            except (json.JSONDecodeError, ValueError):
                continue

    return records


def read_all_records() -> list:
    """Returns every record in the log regardless of age."""
    if not LOG_FILE.exists():
        return []
    records = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def aggregate_by_model(records: list) -> dict:
    """Groups usage records by model and sums token counts."""
    by_model = {}
    for r in records:
        m = r.get("model", "unknown")
        if m not in by_model:
            by_model[m] = {"calls": 0, "prompt_tokens": 0,
                           "completion_tokens": 0, "total_tokens": 0,
                           "call_types": {}}
        by_model[m]["calls"]             += 1
        by_model[m]["prompt_tokens"]     += r.get("prompt_tokens", 0)
        by_model[m]["completion_tokens"] += r.get("completion_tokens", 0)
        by_model[m]["total_tokens"]      += r.get("total_tokens", 0)
        ct = r.get("call_type", "unknown")
        by_model[m]["call_types"][ct] = by_model[m]["call_types"].get(ct, 0) + 1
    return by_model


# ─────────────────────────────────────────────────────────────────────────────
# Visual helpers
# ─────────────────────────────────────────────────────────────────────────────

def bar(used: int, total: int, width: int = 35) -> str:
    if total == 0:
        return "[" + "?" * width + "] n/a"
    pct    = min(used / total * 100, 100.0)
    filled = int(pct / 100 * width)
    ch     = "▓" if pct < 60 else ("█" if pct < 80 else "■")
    return f"[{ch * filled}{'░' * (width - filled)}] {pct:.1f}%"

def D(ch="─", w=64):
    return ch * w


# ─────────────────────────────────────────────────────────────────────────────
# LIVE PROBE — the key addition
#
# This makes a real API call to Groq. The response object contains HTTP
# headers that Groq populates with rate limit data. These headers are:
#
#   x-ratelimit-limit-tokens         the TPM ceiling for this model
#   x-ratelimit-remaining-tokens     tokens left THIS MINUTE (TPM, not TPD)
#   x-ratelimit-reset-tokens         when the per-minute window resets
#   x-ratelimit-limit-requests       requests-per-minute ceiling
#   x-ratelimit-remaining-requests   requests left this minute
#
# IMPORTANT NUANCE: these headers reflect the per-MINUTE window, not the
# per-DAY window. Groq does not include the TPD balance in response headers.
# The only time you see TPD numbers is in the 429 error message body.
#
# So the probe does two things:
#   1. On success  → shows you live TPM headroom (are you in danger of
#                    hitting the per-minute rate limit in a demo?)
#   2. On 429 error → parses the error message to extract the authoritative
#                    TPD numbers ("Used 97622, Limit 100000") and shows them
#
# Accessing headers via the groq SDK: the SDK's `with_raw_response` context
# gives you the underlying httpx response object before the SDK parses it,
# which lets us read raw headers directly.
# ─────────────────────────────────────────────────────────────────────────────

def probe_live():
    """
    Makes a minimal real API call and reads whatever live rate limit data
    Groq exposes through HTTP headers and error messages.
    """
    if not GROQ_API_KEY:
        print("\n  ❌  No API key set. Add GROQ_API_KEY to your .env file.\n")
        return

    print()
    print(D("═"))
    print("  🔌  Live Groq Probe")
    print("      Making a minimal test call (~30 tokens) to read live rate limits.")
    print("      This is the only way to get real-time data from Groq's side.")
    print(D("═"))

    try:
        from groq import Groq, RateLimitError, AuthenticationError
    except ImportError:
        print("  ❌  groq package not installed. Run: pip install groq\n")
        return

    client = Groq(api_key=GROQ_API_KEY)

    # We probe the 8B model first because it has a 500k TPD limit — very
    # unlikely to be exhausted, so we get a clean success path and real headers.
    # If even 8B is rate-limited, we parse the 429 for authoritative numbers.
    probe_model = "llama-3.1-8b-instant"

    try:
        # with_raw_response gives us the httpx response BEFORE the SDK parses
        # it, so we can read raw HTTP headers directly.
        raw = client.with_raw_response.chat.completions.create(
            model=probe_model,
            messages=[{"role": "user", "content": "Reply: OK"}],
            max_tokens=3,
            temperature=0.0,
        )

        h = dict(raw.headers)   # all HTTP response headers as a plain dict

        # Extract the rate limit headers Groq sends back.
        # Note: these are per-MINUTE (TPM) values, not per-day (TPD).
        tpm_limit     = h.get("x-ratelimit-limit-tokens",      "not provided")
        tpm_remaining = h.get("x-ratelimit-remaining-tokens",  "not provided")
        tpm_reset     = h.get("x-ratelimit-reset-tokens",      "not provided")
        rpm_limit     = h.get("x-ratelimit-limit-requests",    "not provided")
        rpm_remaining = h.get("x-ratelimit-remaining-requests","not provided")

        # Parse the actual response to get token usage for this probe call
        parsed        = raw.parse()
        probe_tokens  = parsed.usage.total_tokens

        print()
        print(f"  ✅  API key valid — probe call succeeded ({probe_tokens} tokens used)")
        print()
        print(f"  📊  Live Rate Limit Data from Groq HTTP Headers")
        print(f"      Model probed: {probe_model}")
        print()
        print(f"  WHAT GROQ EXPOSES IN HEADERS (per-minute window):")
        print(f"  ┌──────────────────────────────────────────────┐")
        print(f"  │  TPM limit:          {str(tpm_limit):>10}  tokens/minute │")
        print(f"  │  TPM remaining:      {str(tpm_remaining):>10}  this minute   │")
        print(f"  │  TPM resets in:      {str(tpm_reset):>10}                │")
        print(f"  │  RPM limit:          {str(rpm_limit):>10}  requests/min  │")
        print(f"  │  RPM remaining:      {str(rpm_remaining):>10}  this minute   │")
        print(f"  └──────────────────────────────────────────────┘")
        print()
        print(f"  ⚠️  WHAT GROQ DOES NOT EXPOSE IN HEADERS:")
        print(f"      TPD (tokens per day) balance — Groq tracks this internally")
        print(f"      but does NOT include it in response headers. The only time")
        print(f"      you see the exact TPD numbers is when you hit the 429 error.")
        print()
        print(f"      This means: to know your true remaining daily budget, the")
        print(f"      options are:")
        print(f"      1. Our local rolling-24h estimate below (best effort)")
        print(f"      2. Wait for a 429 error — it tells you exactly what's used")
        print(f"      3. Check the Groq Console: https://console.groq.com/settings/billing")

    except RateLimitError as e:
        # This is actually informative! The 429 message contains the exact
        # TPD usage numbers. We parse them out and show them clearly.
        msg = str(e)
        print()
        print(f"  🚫  Rate limited — but this gives us AUTHORITATIVE TPD numbers!")
        print()

        # The error message format is:
        # "Rate limit reached for model X ... TPD: Limit 100000, Used 97622, Requested 7516.
        #  Please try again in 1h17m37.824s."
        limit_match   = re.search(r"Limit\s+([\d,]+)",    msg)
        used_match    = re.search(r"Used\s+([\d,]+)",     msg)
        requested_match = re.search(r"Requested\s+([\d,]+)", msg)
        retry_match   = re.search(r"try again in ([^.\"]+)", msg)

        limit     = int(limit_match.group(1).replace(",",""))     if limit_match     else None
        used      = int(used_match.group(1).replace(",",""))      if used_match      else None
        requested = int(requested_match.group(1).replace(",","")) if requested_match else None
        retry     = retry_match.group(1).strip()                  if retry_match     else "unknown"

        print(f"  📊  FROM GROQ'S 429 ERROR — These numbers are exact, not estimates:")
        print(f"  ┌──────────────────────────────────────────────┐")
        if limit and used:
            remaining = limit - used
            pct       = used / limit * 100
            print(f"  │  Model:     {probe_model:<33}│")
            print(f"  │  TPD limit: {limit:>10,}  tokens/day          │")
            print(f"  │  TPD used:  {used:>10,}  ({pct:.1f}% consumed)       │")
            print(f"  │  Remaining: {remaining:>10,}  tokens               │")
            if requested:
                print(f"  │  Requested: {requested:>10,}  (what this call needed)│")
            print(f"  │  Retry in:  {retry:<33}│")
        else:
            print(f"  │  Could not parse exact numbers from error.    │")
            print(f"  │  Raw message: {msg[:45]}...│")
        print(f"  └──────────────────────────────────────────────┘")
        print()
        print(f"      This is the most accurate view of your TPD balance you")
        print(f"      can get from Groq's API without a dedicated usage endpoint.")

    except AuthenticationError:
        print(f"\n  ❌  Authentication failed — API key is invalid or revoked.")
        print(f"      Get a new key: https://console.groq.com/keys\n")

    except Exception as e:
        print(f"\n  ❌  Unexpected error: {e}\n")

    print(D("═"))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL ESTIMATE — rolling 24h window
# ─────────────────────────────────────────────────────────────────────────────

def report_today():
    now     = datetime.now()
    cutoff  = now - timedelta(hours=24)
    records = read_rolling_24h()
    usage   = aggregate_by_model(records)

    print()
    print(D("═"))
    print(f"  🔋  Groq Token Budget — Local Estimate (Rolling 24h Window)")
    print(f"      Window: {cutoff.strftime('%d %b %H:%M')} → {now.strftime('%d %b %H:%M')} (last 24 hours)")
    print(f"      API key: {'✅ loaded' if GROQ_API_KEY else '❌ not set'}")
    print(f"      Log file: {LOG_FILE}  ({len(records)} calls in window)")
    print()
    print(f"  Why rolling 24h not calendar day?")
    print(f"  Groq's TPD limit is a 24-hour rolling window — calls made at 6pm")
    print(f"  yesterday still count until 6pm today. Calendar-day grouping would")
    print(f"  miss those calls and give you an optimistic (wrong) estimate.")
    print()
    print(f"  ⚠️  Calls made outside this app are NOT in the local log.")
    print(f"      For the authoritative number: python check_tokens.py --probe")
    print(f"      Or check: https://console.groq.com/settings/billing")
    print(D("═"))

    for model_name in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]:
        info        = GROQ_MODELS[model_name]
        limit       = info["tpd"]
        used        = usage.get(model_name, {}).get("total_tokens", 0)
        remaining   = max(0, limit - used)
        calls       = usage.get(model_name, {}).get("calls", 0)
        call_types  = usage.get(model_name, {}).get("call_types", {})

        print()
        print(f"  📌  {model_name}")
        print(f"      {info['params']}  ·  {info['our_use']}")
        print()
        print(f"      {bar(used, limit)}")
        print(f"      Used (last 24h): {used:>8,} / {limit:,}  ({calls} calls)")
        print(f"      Remaining:       {remaining:>8,} tokens")
        print(f"      Queries left at average cost:")
        for ct, avg in TYPICAL_COST.items():
            print(f"        • {ct:<22} ~{remaining // avg:>4} more  (avg {avg:,} tok/call)")
        if call_types:
            print(f"      Breakdown:")
            for ct, n in sorted(call_types.items(), key=lambda x: -x[1]):
                print(f"        • {ct:<35} {n} call(s)")
        print(f"      {D()}")

    # Summary line
    rem_70b = max(0, 100_000 - usage.get("llama-3.3-70b-versatile",{}).get("total_tokens",0))
    rem_8b  = max(0, 500_000 - usage.get("llama-3.1-8b-instant",{}).get("total_tokens",0))
    print()
    print(f"  📊  Quick Summary")
    print(f"      70B → ~{rem_70b // 4500:>3} copilot queries left  |  8B → ~{rem_8b // 500:>4} parses left")
    print(f"      Run --probe to get live TPM data and exact TPD numbers if rate-limited.")
    print(D("═"))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# MODEL CATALOGUE
# ─────────────────────────────────────────────────────────────────────────────

def report_models():
    print()
    print(D("═"))
    print("  📋  Groq Model Directory — Free Tier Limits")
    print(D("═"))
    print(f"\n  {'Model':<35} {'TPD':>8}  {'TPM':>7}  {'Params':<10}  Our usage")
    print(f"  {D('-', 35)} {'───────':>8}  {'──────':>7}  {'──────':<10}  ─────────")
    for name, info in GROQ_MODELS.items():
        active = "◀" if "()" in info.get("our_use","") else " "
        print(f"  {name:<35} {info['tpd']:>8,}  {info['tpm']:>7,}  {info['params']:<10}  {info['our_use']} {active}")
    print(f"\n  TPD = per day  |  TPM = per minute  |  Source: console.groq.com/docs/rate-limits")
    print(D("═"))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY REPORT
# ─────────────────────────────────────────────────────────────────────────────

def report_history():
    all_records = read_all_records()
    if not all_records:
        print(f"\n  No usage history found at {LOG_FILE}\n")
        return

    by_date = {}
    for r in all_records:
        try:
            ts   = datetime.fromisoformat(r.get("timestamp","2000-01-01T00:00:00"))
            day  = ts.strftime("%Y-%m-%d")
        except ValueError:
            day  = r.get("date","unknown")
        if day not in by_date:
            by_date[day] = []
        by_date[day].append(r)

    print()
    print(D("═"))
    print(f"  📅  Usage History — All Time  ({len(all_records)} total calls)")
    print(D("═"))
    print()
    for day in sorted(by_date.keys(), reverse=True):
        day_records = by_date[day]
        total_tok   = sum(r.get("total_tokens",0) for r in day_records)
        by_model    = aggregate_by_model(day_records)
        print(f"  {day}   {total_tok:>7,} tokens  ·  {len(day_records)} calls")
        for model, stats in sorted(by_model.items(), key=lambda x: -x[1]["total_tokens"]):
            limit    = GROQ_MODELS.get(model, {}).get("tpd", 100_000)
            pct      = stats["total_tokens"] / limit * 100
            short    = model.replace("llama-3.3-70b-versatile","70B").replace("llama-3.1-8b-instant","8B").replace("mixtral-8x7b-32768","mixtral")
            print(f"      {short:<12} {stats['total_tokens']:>7,} tokens  ({pct:4.1f}% of daily limit)  {stats['calls']} calls")
        print()
    print(D("═"))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--probe" in args:
        probe_live()
        if "--models" not in args:
            report_today()

    elif "--models" in args:
        report_models()

    elif "--history" in args:
        report_history()

    elif "--help" in args or "-h" in args:
        print(__doc__)

    else:
        report_today()
        print("  Other commands:")
        print("    --probe    live rate-limit check via real Groq API call (costs ~30 tokens)")
        print("    --models   full model catalogue with limits")
        print("    --history  all-time usage grouped by day")
        print()
