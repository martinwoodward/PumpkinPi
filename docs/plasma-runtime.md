# PumpkinPi Plasma runtime

The Plasma 2350 W connects directly to GitHub over verified HTTPS and drives
exactly 96 RGB LEDs. The token and Wi-Fi credentials live on the board. No
computer, background service or model call is needed during operation.

## Setup and upload

Follow the [firmware setup guide](../firmware/plasma2350w/README.md#standalone-setup):
copy `config.github.example.json` to ignored `config.json`, copy `.env.example`
to ignored `.env`, then fill in the private credentials and approved billing
configuration. Install the serial upload tool from
`firmware/plasma2350w/requirements.txt` and run:

```bash
.venv/bin/python firmware/plasma2350w/deploy.py install \
  --device /dev/cu.usbmodemXXXX --mpremote .venv/bin/mpremote \
  --config firmware/plasma2350w/config.json \
  --env firmware/plasma2350w/.env
.venv/bin/mpremote connect /dev/cu.usbmodemXXXX reset
```

Only explicitly supplied `--env` uploads secrets. Board flash is physically
readable, not encrypted secret storage: use a minimally privileged, revocable
token and never commit credentials or flash dumps. The BOOTSEL USB volume
accepts UF2 firmware, not Python filesystem files.

## Live source provisioning

Configure these fields in the board `config.json`:

- `source.owner_type`: `user`, `organization`, or `enterprise`, with the exact
  `source.owner` and optional organization/enterprise `source.subject`.
- `source.allowance_microcredits`: an approved named monthly allowance or budget;
  one credit equals 1,000,000 microcredits. Manually configured allowances are
  `estimated`, not evidence of GitHub account lockout.
- `source.mapping`: exact `unit_type`, `product`, `sku`, and `quantity_field`
  (`grossQuantity`, `discountQuantity`, or `netQuantity`) verified from an
  authorized response. Never infer credits from money fields, token counts,
  premium-request quotas or plan names.
- `exhaustion_policy`: `"configured-budget"` only when zero for that named budget
  should trigger the exhausted animation; otherwise `"none"`.

For an explicitly approved unlimited basis, set `source.unlimited: true` and
`source.allowance_microcredits: null`. The board still fetches and validates usage.
Missing amounts, failed requests and paid overages do not imply unlimited credits.
Change `budget_revision` when changing the balance definition.

The documented AI-credit API reports usage, not a universal remaining wallet.
A personal endpoint does not report employer-funded usage. Verify the actual
payer, token access, allowance and units locally before enabling live reporting.
Do not guess token scopes or paste a token into chat.

## Candle and demo

Full credits show a golden candle with recurring white sparkles. The palette
moves through red and purple as credits fall; sparkles stop below 100% and remain
active for explicitly unlimited credits. Confirmed zero sputters for two seconds,
then stays in a dull purple flicker. Recovery rises to white over one second and
blends back into the current-balance candle over half a second.

Press button **A** to start the latched animation demo: three flashes, a 15-second
pause, 10 seconds full, 30 seconds draining, 5 seconds exhausted, then recharge
and hold full until restart. A blue onboard LED marks simulation. It stops
network polling and never writes synthetic values into live billing state.

Every boot starts in normal mode. With no fresh valid credit data after 30 seconds,
it starts the same demo automatically. `startup_demo_timeout_seconds` can be
increased for a slower network. Later outages after a successful startup retain
normal stale/error handling instead of switching to simulated full credits.
Invalid local configuration or missing `.env` remains an explicit startup error.

## Network and diagnostics

The cooperative scheduler advances DNS, NTP and verified TLS alongside animation.
Allow outbound UDP 53/123 and TCP 443. The bundled CA verifies `api.github.com`;
redirects and insecure TLS fallbacks are prohibited. NTP bootstraps the actual RTC,
including ports with a 2000 epoch. NTP itself is unauthenticated; verified HTTP
Date and a saved daily time floor provide additional checks.

Five-minute polling observes newly reported usage, not necessarily each
interaction. GitHub reporting latency is unspecified. Failed requests preserve
the prior observation without renewing its freshness or inventing zero. Reports
are bounded to 32 KiB and 512 rows and normalized using exact decimal arithmetic.
Oversized reports fail explicitly rather than truncating usage.

Inspect the status LED and secret-free serial error for Wi-Fi, authorization,
clock, certificate, mapping or response-size failures. Blue means demo, red
means error, purple means stale, and amber marks unknown/estimated data. Never
disable certificate verification to get past a connection failure.

## Optional setup skill

`plugin/skills/pumpkin/SKILL.md` is the authoritative setup skill. To install it
for a project:

```bash
mkdir -p .github/skills/pumpkin
cp plugin/skills/pumpkin/SKILL.md .github/skills/pumpkin/SKILL.md
```

Edit the source and recopy it; no plugin manifest or automatic installation is
provided. The skill guides provisioning and diagnostics, not ongoing polling.

## Development checks

Python 3.10+ can run the standard-library test suite without installing a package:

```bash
python3 -m unittest discover -s tests -t . -v
python3 firmware/plasma2350w/deploy.py bundle build/plasma-firmware
```

Tests cover exact arithmetic, rendering, demo timing, button debounce, power
limiting, RTC conversion, deployment and real local TLS verification failures.
Public DNS, NTP and GitHub TLS were also exercised from a desktop using a synthetic
invalid credential, not a live billing token. Physical UF2 heap/frame timing,
live-account mapping, current, thermals and soak operation remain deployment
checks. All flashes, white recharge and sparkle effects retain the configured
brightness/current caps.
