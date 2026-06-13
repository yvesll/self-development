# Claude Code DeepSeek statusline

A Claude Code statusline that shows, per session:

```
🐋 DeepSeek V4 Pro | ↑13.1k ↓73.9k | Σ3.85M | ~$1.71 | 💰 ¥110.00
```

- `↑` input tokens / `↓` output tokens (from the session transcript — accurate)
- `Σ` cumulative session tokens (incl. cache)
- `~$` estimated cost at DeepSeek V4 list price (conservative upper bound; cache & off-peak discounts not modeled)
- `💰` remaining DeepSeek account balance (near-real-time, cached 5 min)

## Files

| File | Purpose |
|------|---------|
| `statusline_deepseek.py` | The statusline script (pure stdlib, cross-platform) |
| `settings.snippet.json` | The `statusLine` block to merge into `~/.claude/settings.json` |
| `settings.local.example.json` | The `env` block (base URL + key) for `~/.claude/settings.local.json` |

## Setup / migration to a new machine

1. **Copy the script** to `~/.claude/statusline_deepseek.py`
   (Windows: `C:\Users\<USER>\.claude\statusline_deepseek.py`).

2. **Find your python path**: `where python` (Windows) or `which python3` (Mac/Linux).

3. **Merge** the `statusLine` block from `settings.snippet.json` into `~/.claude/settings.json`.
   Fix the python path and the script path for this machine:
   - Windows: `"C:/Python311/python.exe C:/Users/<USER>/.claude/statusline_deepseek.py"`
   - Mac/Linux: `"python3 /Users/<USER>/.claude/statusline_deepseek.py"`

4. **Add your DeepSeek key**: copy `settings.local.example.json` to
   `~/.claude/settings.local.json` and replace the placeholder with your real key.
   This file is git-ignored by Claude Code — never commit the key.

5. **Restart Claude Code.** The statusline appears at the bottom.

## How balance lookup works

The script calls DeepSeek's `GET /user/balance` and caches the result for 5 minutes
(`CACHE_TTL` in the script) to avoid lag and rate limits. It only sends the key to
`api.deepseek.com` when `ANTHROPIC_BASE_URL` contains `deepseek` (or `DEEPSEEK_API_KEY`
is set), so a non-DeepSeek key is never leaked. Network failures fall back to the last
cached value and never break the token display.

## Pricing

DeepSeek V4 list price (USD / 1M tokens), edit the `PRICING` dict in the script when it changes:

| Model | Input | Output |
|-------|-------|--------|
| `deepseek-v4-pro`   | $0.435 | $0.87 |
| `deepseek-v4-flash` | $0.14  | $0.28 |

For authoritative billing, see https://platform.deepseek.com
