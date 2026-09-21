# Plasma 2350 W firmware

This MicroPython application drives a 96-pixel AI-credit candle using
**standalone GitHub HTTPS**. It
stores Wi-Fi credentials and the GitHub token on the Plasma 2350 W itself; no
computer or Copilot process needs to remain running.

## Standalone setup

1. Install the Pimoroni **Plasma 2350 W v1.1.0** UF2.
2. Create local private files from the committed templates:

   ```bash
   cp firmware/plasma2350w/config.github.example.json firmware/plasma2350w/config.json
   cp firmware/plasma2350w/.env.example firmware/plasma2350w/.env
   chmod 600 firmware/plasma2350w/.env firmware/plasma2350w/config.json
   ```

3. Edit `.env` **locally, not in chat or a shell argument**. Set `WIFI_SSID`,
   `WIFI_PASSWORD`, and `GITHUB_TOKEN`. This is a literal file, not a shell script:
   optional matching quotes are removed, but escapes, interpolation, `export`,
   and inline comments are not supported. A password can contain `#` or `=`.
   Blank values, duplicate/unknown keys and control characters are rejected.
4. Edit `config.json`: supply the actual payer/account, an approved
   `allowance_microcredits` (one credit = 1,000,000 microcredits), and verified
   `unit_type`, `product`, `sku`, and `quantity_field` from that account's billing
   response. For an organization or enterprise set `owner_type`, `owner` and,
   if appropriate, `source.subject`. See [accounting requirements](../../docs/plasma-runtime.md#live-source-provisioning).
   Empty template fields intentionally prevent startup; there is no guessed quota
   or demo data in live mode.
5. The template selects `exhaustion_policy: "configured-budget"`: zero triggers
   the exhausted animation for **your named monthly budget**, not necessarily
   your GitHub account. Use `"none"`
   for an estimated allowance that must not claim exhaustion. Change
   `budget_revision` when you change the budget.
6. Reboot normally into MicroPython, connect the serial USB port, then upload:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r firmware/plasma2350w/requirements.txt
   .venv/bin/python firmware/plasma2350w/deploy.py install \
     --device /dev/cu.usbmodemXXXX \
     --mpremote .venv/bin/mpremote \
     --config firmware/plasma2350w/config.json \
     --env firmware/plasma2350w/.env
   .venv/bin/mpremote connect /dev/cu.usbmodemXXXX reset
   ```

The actual `.env` and `config.json` are ignored by Git; `.env.example` is tracked.
Only explicit `--env` uploads secrets, to the board's `/.env`. The token never
appears in command arguments, normalized snapshots, status or network errors.
Board flash is **not encrypted secret storage**: anyone with physical access can
read it. Use a minimally privileged, revocable token. To rotate it, update the
private `.env`, upload that file with `mpremote ... fs cp path/to/.env :.env`,
reset the board and revoke the old credential. Do not commit flash dumps or
private backup files.

### Network, time and trust

The board cooperatively performs DHCP-resolver IPv4 DNS, NTP bootstrap, and
GitHub HTTPS. The actual RTC is set before TLS starts (including 2000-epoch
MicroPython ports). NTP is **unauthenticated clock bootstrap**, not server
authentication. Every request requires a trusted CA and the hostname
`api.github.com`; no redirect is followed and no `CERT_NONE` fallback exists.
See [the bundled CA's provenance and rotation](certs/README.md).

GitHub's verified HTTP Date must agree within five minutes. A saved
`direct-time.json` lower bound rejects substantial rollback across boots; it is
updated at most once daily after a verified response. A corrupt or unreadable
file is an explicit startup error: inspect the device rather than silently
discarding rollback protection. Time resynchronizes every six hours and after
TLS/time disagreement; the filesystem and RTC must be functional.

Allow outbound DNS (UDP 53), NTP (UDP 123), and HTTPS (TCP 443). `ntp_host` can
name a local NTP server if public NTP is blocked. DNS and NTP have five-second
deadlines; a full HTTPS operation has fifteen seconds, 8 KiB of headers and a
32 KiB body maximum. Billing normalization is limited to 512 rows with exact
decimal arithmetic, not float-rounded credit quantities. Oversized or unsupported
responses fail visibly rather than truncating usage or displaying a guessed balance.

Polling defaults to five minutes and honors rate-limit delays with bounded-work
exponential retries. GitHub's reporting latency is unknown, so effects represent
**newly reported** consumption, not every keystroke. DNS/TLS/HTTP progress is
cooperative with the 30 fps scheduler; no `urequests.get()` blocks the render loop.
Failures preserve the prior observation without renewing freshness or inventing
zero. The onboard status LED signals errors/staleness. A serial REPL traceback
identifies startup configuration errors. Actual UF2 frame timing still needs
measurement; cryptographic work and JSON parsing are not preemptible.

## Candle behaviour and button A demo

Full credits show a **golden flickering candle with recurring white sparkles**.
The palette passes through orange/red and purple as the balance falls; sparkles
stop below 100%. At confirmed zero the two-second sputter now settles into a
**dull purple flicker**, not complete darkness. Repeated zero reports do not
restart it. When credits return, the light rises to bright white over **one
second**, then blends into the credit-dependent candle over half a second.
At full balance this is golden with white sparkles again. An unconfirmed
estimated zero keeps the existing brighter low-balance purple instead.

An unlimited basis must be explicitly approved: set `source.unlimited: true` and
`source.allowance_microcredits: null` in the board config. Unlimited
snapshots still require a successful, mapped usage report; missing data, null
allowance alone, paid overages and authentication errors never imply unlimited
credits. Sparkles continue while the configured unlimited basis is active.

Press **A** (active-low `SW_A` / GPIO12 on the Plasma 2350 W, with a pull-up and
50 ms debounce) to enter animation demo mode. The sequence is:

| Stage | Duration |
| --- | --- |
| Three white flashes | 250 ms on, 250 ms off each (1.5 s total) |
| Dark pause | 15 s |
| Full golden candle with white sparkles | 10 s |
| Smooth simulated consumption from 100% to zero | 30 s |
| Exhausted state | 5 s, including the initial 2 s sputter then dull flicker |
| Recharge to white | 1 s, then 0.5 s blend into golden |
| Full golden candle with recurring white sparkles | Until reset/power-off |

Demo is latched in memory: more button presses and later network availability do
not restart or cancel it. Polling stops and active sockets close; synthetic demo
values never enter live accounting or persistent billing state. The onboard
status LED is **blue** and the serial console gives a secret-free entry reason.

Every boot starts in **normal mode**. If no fresh, valid balance arrives within
**30 seconds**, it enters the same demo automatically. Set
`startup_demo_timeout_seconds` (greater than 0, at most 300) to allow a slower
network. Wi-Fi, DNS, TLS, authorization or unsupported-response failures can all
prevent a usable startup reading; the recorded error remains available for
diagnostics. A later outage after a successful startup stays in normal mode with
the existing stale/error indication, rather than switching to synthetic full
credits. Reset/power-cycle to retry live mode after fallback.

Invalid local configuration or missing `.env` is still an explicit startup
error, not silently replaced by demo data. All frames, including flashes,
recharge white and sparkles, obey the same brightness/current caps; "bright"
never means bypassing the configured electrical limit.

## Deployment details

The RP2350 BOOTSEL drive is a UF2 bootloader volume, **not** the MicroPython user
filesystem: do not copy `.py` files to it. The `install` command uses `mpremote`
to create package directories and upload `main.py`, the scheduler, hardware
adapter, GitHub provider, cooperative networking, the CA certificate, every
`pumpkinpi_core/` module, and only explicitly supplied private files.

For inspection or packaging without a device:

```bash
python3 firmware/plasma2350w/deploy.py bundle build/plasma-firmware
```

The bundle command accesses only the named local folder and never uploads secrets.
The deployment requirement is only `mpremote`; no desktop runtime is installed.

Start with 96 WS2812 RGB pixels in confirmed GRB order. The strip is black before
`strip.start(60)`. The default 10% brightness and provisional 1.5 A model are not
electrical certification; identify the LEDs and verify current with a meter.
Fatal errors and normal interpreter shutdown execute a final blackout, but physical
power-loss behavior must still be checked on the actual DMA/strip firmware.
The onboard status LED uses Pimoroni's documented
`RGBLED("LED_R", "LED_G", "LED_B")` pin-name API.

## Hardware gates

Board entropy, DNS/NTP/HTTPS timing, onboard RGB LED pin names,
actual colour order, current, temperature, and an eight-hour soak remain physical
tests. The HTTPS implementation has host tests including real local TLS success,
wrong CA/hostname and expired-certificate rejection without transmitting an
application credential. Public DNS, NTP and GitHub TLS with the bundled root
were also exercised from the host, using a synthetic invalid credential (401).
No actual billing token was supplied, and no physical board was attached:
live entitlement/unit mapping, RP2350 heap usage and animation timing remain
deployment checks, not claims inferred from desktop tests.
