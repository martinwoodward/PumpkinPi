# Plasma PumpkinPi: standalone credit candle design

Target: **Plasma 2350 W, 96 RGB LEDs**. The existing Raspberry Pi application in
`src/` remains separate and unchanged. The firmware implementation and desktop
tests are complete; actual-account authorization and physical-board acceptance
remain unverified. See the [runtime guide](plasma-runtime.md) for provisioning.

## Architecture

```text
GitHub billing REST
  -> cooperative DNS / NTP / verified HTTPS
  -> exact credit normalization
  -> ordered accounting snapshots
  -> candle / consumption pulse / recharge
  -> final brightness and current limiter
  -> 96-pixel Plasma output

Button A or startup timeout -> latched animation demo -> same effects and limiter
```

No LLM runs on the microcontroller. No paid model call is needed to update the
lamp. The optional setup skill assists provisioning; the board performs all
ongoing polling itself.

| Implementation | Responsibility |
| --- | --- |
| `firmware/plasma2350w/main.py` | Configuration, Wi-Fi adapter, actual RTC setup and shutdown blackout |
| `environment.py` and `.env.example` | Literal private credential loading and secret-free template |
| `providers/direct_network.py` | Bounded, cooperative DNS, NTP and certificate-verified HTTPS |
| `providers/github.py` | UTC monthly polling, retries, date checks and normalized snapshots |
| `pumpkinpi_core/billing.py` | Exact decimal normalization with explicit unit mapping |
| `pumpkinpi_core/accounting.py` | Sequence, scope, period, correction and freshness validation |
| `pumpkinpi_core/candle.py` and `runtime.py` | Candle, consumption pulse, depletion and recharge |
| `pumpkinpi_core/demo.py` | Monotonic, synthetic display sequence isolated from billing |
| `scheduler.py` and `hardware.py` | 30 fps updates, debounced button A and status LED |
| `pumpkinpi_core/power.py` | Final electrical model and brightness cap |
| `deploy.py` and `requirements.txt` | Local bundle export and explicit serial upload using mpremote |

## Accounting and authorization

Use the documented AI-credit usage routes:

```text
GET /users/{username}/settings/billing/ai_credit/usage?year=YYYY&month=M
GET /organizations/{org}/settings/billing/ai_credit/usage?year=YYYY&month=M&user=USERNAME
GET /enterprises/{enterprise}/settings/billing/ai_credit/usage?year=YYYY&month=M&user=USERNAME
```

Omit the subject filter for a whole organizational pool. The API version is
`2026-03-10`. Verify the actual payer and access policy: personal usage does not
include employer-funded consumption. Do not invent token scopes or automatically
request broad privileges.

The response reports usage, not a universal remaining wallet. Provision a named
monthly allowance/budget and an approved exact `unitType`, product, SKU and
quantity field. Do not subtract billed money, token counts or premium requests
from a credit allowance. Sum exact decimal quantities before converting to
integer microcredits; reject unsupported precision instead of rounding away a
positive balance.

Snapshots carry opaque profile/budget identities, generation, stream/sequence,
UTC billing period, observation time, quality, used/limit/remaining microcredits,
explicit unlimited status and configured-budget exhaustion. Unknown data is
distinct from zero. An unlimited snapshot requires local authorization, known
usage, null limit/remaining, and cannot claim exhaustion.

First observations, corrections, reconnects and new periods establish baselines
without inventing consumption. Only positive cumulative deltas within the same
basis queue bounded activity effects. A response must match the immutable month
requested; crossing a month boundary forces a new request. Failed polls do not
refresh age. UTC rollback, large jumps and old periods invalidate current claims.

## Trust and resource limits

Credentials are in ignored board-local `.env`, uploaded only via explicit
`--env`. Flash is physically readable: use revocable credentials and never commit
private config, tokens or flash dumps.

TLS requires the bundled trusted root, `CERT_REQUIRED`, and hostname verification
for `api.github.com`. Never follow redirects or disable verification. NTP is
unauthenticated RTC bootstrap, not GitHub authentication. Check verified HTTP
Date against the board clock and preserve a daily verified lower time bound.
See the [CA provenance and rotation guide](../firmware/plasma2350w/certs/README.md).

DNS/NTP deadlines are five seconds and HTTPS is fifteen seconds. Bound headers
to 8 KiB, bodies to 32 KiB and billing rows to 512. Exceeding a limit is an explicit
error, not permission to truncate usage. Poll every 300 seconds by default and
honor rate-limit delays with backoff. Reporting latency is unspecified.

## Animation and controls

| State | Effect before electrical limiting |
| --- | --- |
| Full / explicitly unlimited | Golden `(255, 170, 45)` flicker and recurring white sparkles |
| 50% | Orange-red `(255, 132, 72)` |
| 20% | Red `(235, 40, 25)` |
| 10% | Red-purple `(180, 20, 100)` |
| Low positive / unconfirmed estimated zero | Purple `(100, 12, 170)` |
| Confirmed configured-budget zero | Two-second sputter, then persistent dull purple flicker |
| Recovery | One-second rise to white, then half-second blend to the current-balance candle |

Repeated zero reports do not restart the sputter. Activity pulses cannot override
depletion. Preserve pending recharge across an unknown-period gap, without
pretending the previous month is current.

Button A is active-low `SW_A` / GPIO12 with a pull-up and 50 ms debounce. Demo
flashes three times (250 ms on/off), waits 15 s, stays full 10 s, drains over 30 s,
stays exhausted 5 s including sputter, and recharges over 1.5 s. Run three full
63-second cycles, hold the orange/golden candle for 60 minutes, then repeatedly
run one complete cycle followed by another 60-minute hold until restart.
The blue status LED marks simulation. Polling stops; synthetic values do not
enter the accounting reducer or saved billing state.

Every fully provisioned boot begins in normal mode. After 30 s without fresh valid credit data,
enter the same latched demo. Make the startup timeout configurable. A later
outage after a successful startup retains live-mode error/stale handling.
Missing or invalid local configuration/environment reports a secret-free setup
error and immediately starts the blue-status demo without network access or
synthetic billing state. Hardware and TLS time-floor failures remain fatal.

## Power and physical acceptance

Default to 96 WS2812 RGB pixels, RGB order, 100% requested brightness and a provisional
1.5 A modeled LED budget. An older 96-pixel strip can demand about 5.76 A at
full white, beyond the board's published 3 A USB-C limit. All effects, including
flashes and recharge, pass through the same limiter. An impossible visibility
budget raises a diagnostic instead of bypassing the cap.

Before declaring hardware-ready, verify board/button APIs on the actual UF2,
LED order, current at worst-case white, cable/connector ratings, temperature,
Wi-Fi recovery, TLS memory and frame timing, clock/reset behaviour and an
eight-hour soak. `strip.start(60)` repeats DMA output; the scheduler must still
generate animation frames. The power limiter is not short-circuit protection.

## References

- [GitHub billing REST usage](https://docs.github.com/en/rest/billing/usage)
- [MicroPython TLS verification](https://docs.micropython.org/en/latest/library/ssl.html)
- [Plasma 2350 W hardware](https://shop.pimoroni.com/products/plasma-2350-w)
- [Pimoroni Plasma v1.1.0](https://github.com/pimoroni/plasma/releases/tag/v1.1.0)
- [Pinned Plasma driver API](https://github.com/pimoroni/plasma/blob/6440caf5f6a04834bcd489932bbcf7015d3626d7/docs/plasma.md)
- [Pinned board pins](https://github.com/pimoroni/plasma/blob/6440caf5f6a04834bcd489932bbcf7015d3626d7/boards/plasma_2350_w/pins.csv)
- [Printable models and physical-fit caveats](../stl/plasma-2350-w/README.md)
