---
name: pumpkin
description: Configure and diagnose the standalone PumpkinPi Plasma 2350 W AI-credit candle without exposing credentials or spending credits.
---

# PumpkinPi setup and diagnostics

The board connects directly to GitHub over HTTPS. Never infer billing state from
Copilot token usage, premium-request quota, model activity or a demo animation.
Never ask the user to paste a token into chat. Never put it on a command line,
print `.env`, or collect a flash dump.

## Safe workflow

1. Establish the actual payer/account, named allowance or budget, exact unit,
   product/SKU/quantity mapping, and minimally privileged credential access.
   Fetch success alone does not establish a remaining balance.
2. Follow `firmware/plasma2350w/README.md`: copy the GitHub config and `.env.example`
   templates to ignored private files. The owner enters Wi-Fi credentials and a
   revocable GitHub token locally. Board flash is physically readable.
3. Install the serial upload tool from `firmware/plasma2350w/requirements.txt`.
   Upload only explicitly approved files with `deploy.py install --config ...
   --env ...`, then reset the board. No desktop service is needed afterward.
4. Diagnose the onboard status LED and secret-free startup/network error. Check
   outbound DNS/NTP/HTTPS, RTC, CA bundle and response bounds. Never weaken
   certificate checks or guess billing units to make an error disappear.
5. Explain that button A starts a latched animation demo, marked by blue status.
   Startup also falls back to demo after 30 seconds without fresh valid data.
   Inspect the error and `startup_demo_timeout_seconds`, then reset/power-cycle
   to retry normal mode. Full/demo sparkles do not prove remaining credits.
6. Point the owner to `docs/plasma-runtime.md` for accounting and diagnostics, and
   the firmware README for upload, token rotation and exact demo timings.

Confirmed configured-budget zero ends in a dull flicker, not black. Recovery rises
to white over one second, then returns to the candle. Sparkles at unlimited credits
require an explicitly approved `source.unlimited: true` and null allowance.
Missing usage, failed authorization and paid overages never imply unlimited.
Credential changes and device uploads require explicit owner approval.
