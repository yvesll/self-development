#!/usr/bin/env python3
"""Claude Code statusline: session token usage + estimated DeepSeek cost,
plus (near-real-time) remaining DeepSeek account balance.

Token counts come from the session transcript (accurate). Cost is an estimate
(DeepSeek cache/off-peak discounts are NOT modeled -> conservative upper bound).
Balance is fetched from DeepSeek's GET /user/balance and CACHED for 5 minutes
to avoid lag and rate limits, so it lags reality by at most a few minutes.
For authoritative figures, see platform.deepseek.com.
"""
import sys
import os
import json
import time
import urllib.request

# Force UTF-8 output: Windows consoles default to cp1252, which cannot encode
# the emoji/arrows below and would crash the statusline command.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# DeepSeek V4 list price, USD per 1M tokens: (input, output)
PRICING = {"pro": (0.435, 0.87), "flash": (0.14, 0.28)}
DEFAULT = PRICING["pro"]

BALANCE_URL = "https://api.deepseek.com/user/balance"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".deepseek_balance_cache.json")
CACHE_TTL = 300  # seconds


def price_for(model_id):
    m = (model_id or "").lower()
    if "flash" in m:
        return PRICING["flash"]
    if "pro" in m:
        return PRICING["pro"]
    return DEFAULT


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def deepseek_key():
    """Return the DeepSeek API key only if we're actually pointed at DeepSeek,
    so we never leak a non-DeepSeek key to api.deepseek.com."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    base = os.environ.get("ANTHROPIC_BASE_URL", "").lower()
    if "deepseek" in base:
        return os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    return None


def fetch_balance():
    """Return a short balance string like '¥110.00' / '$12.34', or None.
    Uses a 5-minute file cache; only hits the network when the cache is stale."""
    key = deepseek_key()
    if not key:
        return None

    now = time.time()
    # serve fresh cache
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            c = json.load(f)
        if now - c.get("ts", 0) < CACHE_TTL:
            return c.get("text")
    except Exception:
        c = None

    # refresh from network
    try:
        req = urllib.request.Request(
            BALANCE_URL, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        infos = data.get("balance_infos") or []
        if infos:
            cur = infos[0].get("currency", "")
            bal = infos[0].get("total_balance", "?")
            sym = {"CNY": "¥", "USD": "$"}.get(cur, cur + " ")
            text = f"{sym}{bal}"
            if not data.get("is_available", True):
                text += "(!)"
        else:
            text = "bal?"
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"ts": now, "text": text}, f)
        except Exception:
            pass
        return text
    except Exception:
        # network/auth failed: fall back to last cached value if any
        return (c or {}).get("text") if c else None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("deepseek")
        return

    model = data.get("model") or {}
    model_id = model.get("id", "")
    model_name = model.get("display_name") or model_id or "?"
    transcript = data.get("transcript_path", "")

    in_tok = out_tok = cache_tok = 0
    cost = 0.0
    if transcript and os.path.exists(transcript):
        try:
            with open(transcript, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    msg = obj.get("message") or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    pin, pout = price_for(msg.get("model", model_id))
                    i = usage.get("input_tokens", 0) or 0
                    o = usage.get("output_tokens", 0) or 0
                    cr = usage.get("cache_read_input_tokens", 0) or 0
                    cw = usage.get("cache_creation_input_tokens", 0) or 0
                    in_tok += i
                    out_tok += o
                    cache_tok += cr + cw
                    cost += (i + cr + cw) / 1_000_000 * pin
                    cost += o / 1_000_000 * pout
        except Exception:
            pass

    total = in_tok + out_tok + cache_tok
    line = (f"\U0001f40b {model_name} | ↑{fmt(in_tok)} ↓{fmt(out_tok)} "
            f"| Σ{fmt(total)} | ~${cost:.4f}")

    bal = fetch_balance()
    if bal:
        line += f" | \U0001f4b0 {bal}"
    print(line)


if __name__ == "__main__":
    main()
