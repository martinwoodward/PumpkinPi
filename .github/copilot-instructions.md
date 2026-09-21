# PumpkinPi agent instructions

## Working sequence

For an approved board change: inspect local code and run targeted host tests,
discover/identify the board, preserve any existing boot program, deploy the
complete bundle, verify installed files, run the real-board smoke test, then
restart the stored application and release the port. A successful upload or
`mpremote run` alone does **not** prove autonomous boot. Documentation-only work
does not require connecting to or interrupting the board.

## Firmware and credentials

- The standalone MicroPython firmware lives in `firmware/plasma2350w`; preserve
  the legacy Raspberry Pi programs in `src`.
- Read `firmware/plasma2350w/README.md` and `docs/plasma-runtime.md` before
  changing provisioning, accounting or animation behavior.
- Never print, commit, or ask the user to paste credentials. Do not dump board
  flash or read `secrets.py` / `.env` into tool output. Provision only explicitly
  approved private files. Board flash is not encrypted.
- Blank setup templates are not live provisioning. Missing/invalid config or
  environment reports `Setup required` and enters a blue-status demo with no
  network provider or synthetic accounting. Hardware/code and corrupt TLS
  time-floor failures must remain explicit errors.
- Default requested brightness is 100%, matching the existing board examples,
  but retain the 1.5 A total modeled-current cap across flashes, candle and
  recharge. The examples address 50 pixels while PumpkinPi uses 96; they do not
  justify removing the current limiter. Preserve explicit private power settings.
- This attached strip uses **RGB**, as specified by its existing `fire.py` and
  `rainbows.py` examples. GRB made the golden/orange candle look green. Use
  `color_order: "RGB"` for this board; preserve explicit orders for other strips.
  Keep logical palette tuples RGB rather than swapping channels in animations.
- Setup demo starts immediately when configuration/environment validation fails;
  the 30-second fallback applies only to valid provisioning with no fresh live
  balance. Invalid config uses default hardware/power settings, not partially
  validated fields; valid config with invalid environment preserves its settings.
- The orange hold is the existing full-balance candle, including its white
  sparkles. Replays remain simulated and must not create billing snapshots.
  Reset effect state between cycles, and clear recharge state on entering hold.
- At high brightness, blend already-current-limited recharge endpoints. Limiting
  an unbounded white ramp afterward makes it reach the current ceiling early,
  flattening the intended one-second rise. Preserve brightness/current checks
  for the entire sequence, including flashes and recharge.

## Discover and communicate with an attached board

Use the onboard serial REPL through `mpremote`, not the UF2 BOOTSEL drive.
The last verified device was COM5, USB VID 2E8A / PID 0005, reporting
`plasma_2350_w`, MicroPython 1.27.0 (2026-03-02), and
`Pimoroni Plasma 2350 (LTE + WiFi) with RP2350`. Rediscover and confirm the
device; the port can change. Do not reflash an already working MicroPython board.

From the repository worktree on Windows:

```powershell
Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name,PNPDeviceID
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r firmware\plasma2350w\requirements.txt
.\.venv\Scripts\mpremote.exe connect COM5 exec "import os, sys; print(os.uname()); print(sys.implementation); print(os.listdir())"
```

Reuse an existing environment when available. On Linux/macOS, use the discovered
`/dev/ttyACM*` or `/dev/cu.usbmodem*` port and `.venv/bin/mpremote`.
`mpremote` was initially absent both from PATH and `python -m mpremote`; installing
the repository requirements in `.venv` supplied both mpremote and pyserial.
Use the explicit `.venv\Scripts` executable paths rather than relying on shell
activation persisting between tool calls.

Use Windows backslashes for host paths; board filesystem paths use MicroPython's
POSIX convention. For example, `fs cp <host-path> :pumpkinpi_core/demo.py` writes
to the board. `deploy.py bundle <folder>` only copies files locally; it does not
deploy. BOOTSEL mass storage accepts UF2 firmware, not application `.py` files.

Only one serial client may own the port. Close serial monitors/editor connections
and wait for any running board test before opening it again. `exec`, `run`, and
`fs` normally interrupt the app and soft-reset into raw REPL; they are not passive
status queries. For interactive use, `mpremote connect COM5 repl` opens the
friendly REPL; Ctrl-C interrupts, Ctrl-D soft-reboots, Ctrl-] exits the terminal.
Always reset after active inspection so the board is not left idle at the REPL.

Before first deployment, preserve an existing `main.py` **on the board** under a
new, non-conflicting name using bounded binary reads/writes and verify its size.
Do not overwrite an earlier backup. On this device the previous 1,029-byte
program is `main-before-pumpkinpi.py`; the pre-existing `main-original.py`,
examples and `secrets.py` remain untouched.

For a different board that does not already have this backup, this tested pattern
keeps the original on-device without exposing its contents:

```powershell
$backup = @'
import os
target = "main-before-pumpkinpi.py"
assert target not in os.listdir(), "backup already exists; do not overwrite"
with open("main.py", "rb") as source, open(target, "wb") as backup:
    while True:
        chunk = source.read(512)
        if not chunk:
            break
        backup.write(chunk)
os.sync()
assert os.stat(target)[6] == os.stat("main.py")[6], "backup size mismatch"
print("Previous main.py preserved on board")
'@
.\.venv\Scripts\mpremote.exe connect COM5 exec $backup
```

If no `main.py` exists, no boot-program backup is needed. Never delete example
files or unrelated private files to make room without approval. Restore a backup
only on explicit request; it may depend on the board's other existing files.

## Deploy and verify

Code-only update (preserves existing board credentials/configuration):

```powershell
.\.venv\Scripts\python.exe firmware\plasma2350w\deploy.py install --device COM5 --mpremote .\.venv\Scripts\mpremote.exe
```

The uploader creates package directories and copies all 16 runtime/certificate
files, not just `main.py`. Explicitly add `--config <local-private-config>` and
`--env <local-private-env>` only when provisioning is approved. For a newly
unprovisioned board, with approval, use `config.github.example.json` and
`.env.example`: the former has blank account/mapping settings and `_setup`
instructions (JSON has no comments); the latter has comments and blank
credentials. Never replace working private settings with these templates.
The upload command does not automatically start the application afterward.
`Up to date` messages for unchanged files are normal. A failed multi-file upload
can leave a mixed version: correct the error and repeat the complete install
before booting or declaring success. Never run independent deployment commands
concurrently against the same port.

### Verify installed code without reading credentials

The file manifest in `deploy.py` is the source of truth (currently 16 files).
The following host-side PowerShell/Python recipe was used to compare SHA-256
hashes of every deployed code/certificate file. It never reads board `.env`,
`config.json`, backups or other private files:

```powershell
@'
import hashlib
from pathlib import Path
import subprocess
import sys

root = Path("firmware") / "plasma2350w"
sys.path.insert(0, str(root))
import deploy

expected = [(name, hashlib.sha256((root / name).read_bytes()).hexdigest())
            for name in deploy.FILES]
code = "import hashlib, binascii\nfor name, expected in " + repr(expected) + ":\n"
code += """ h = hashlib.sha256()
 with open(name, 'rb') as handle:
  while True:
   chunk = handle.read(512)
   if not chunk: break
   h.update(chunk)
 assert binascii.hexlify(h.digest()).decode() == expected, name
print('All installed runtime files match')
"""
subprocess.run([str(Path(".venv") / "Scripts" / "mpremote.exe"),
                "connect", "COM5", "exec", code], check=True)
'@ | .\.venv\Scripts\python.exe -
```

This actively interrupts the running program, so perform it **before** the final
restart. Do not hash private credentials into logs as a substitute for printing
them. If inspecting config, print only explicitly needed nonsecret fields such
as `color_order` or `max_brightness`, not the entire object.

### Exercise the real hardware

For an unprovisioned/offline board, exercise the actual strip through every demo
phase without installing a test boot program:

```powershell
.\.venv\Scripts\mpremote.exe connect COM5 run firmware\plasma2350w\verify_demo.py
.\.venv\Scripts\mpremote.exe connect COM5 reset
```

Allow about 191 seconds plus the startup timeout. Require `PASS` and the ordered
phases `flash`, `wait`, `full`, `drain`, `empty`, `recharge` repeated three times,
then `hold`. Demo holds the orange/golden candle for 60 minutes, then repeats one
complete 63-second sequence followed by another 60-minute hold indefinitely.
The verifier also advances demo timestamps through two hourly replay boundaries;
this checks the schedule on real hardware but is not a two-hour soak. The verifier
checks real hardware writes, brightness/current-model caps, the dark pause,
nonzero lit phases, and that live accounting stays empty. Checks add overhead;
the scheduler's 30 fps target is not a measured guarantee. It blacks the strip
in cleanup: **reset afterward** to start the persistent uploaded `main.py`.

Allow at least 300 seconds for a combined upload/test at the default startup
timeout; a configured 300-second startup timeout needs a correspondingly larger
tool wait. If a command moves to the background, wait for its completion and
retrieve that execution's output; do not start another serial session or rerun
the test while it is active.

The expected timeline relative to demo entry is:

| Event | Elapsed seconds |
| --- | --- |
| Initial cycles begin | 0, 63, 126 |
| First 60-minute orange hold begins | 189 |
| First single replay begins | 3789 |
| Second 60-minute orange hold begins | 3852 |
| Second single replay begins | 7452 |

Each cycle includes the three flashes and 15-second dark pause, not just the
drain/recharge. A dark strip during `wait` is expected. Use monotonic scheduler
time rather than RTC or frame count, and check boundaries just before/at phase
changes. Test delayed frames, tick wrap, repeated button presses, and that
network polling stays stopped. Accelerated verification advances demo timestamps,
not stored production durations or the board RTC.

`DEMO phase=...` progress lines come from `verify_demo.py`, not normal `main.py`.
Normal demo boot announces its reason once and can then be silent indefinitely.
No further serial output is not, by itself, evidence that the application hung.

### Restart the stored application and release the port

Verify that a reset boots the stored application and emits `Setup required` /
`Animation demo: setup required` for blank settings (or `startup connection
timeout` after the configured delay for valid but offline provisioning). Use a
passive serial reader without sending Ctrl-C/Ctrl-D to observe the running app;
for example pyserial, installed by mpremote, at 115200 baud with bounded timeout.
Opening the reader alone does not need to enter REPL. Reset may briefly
disconnect USB; rediscover/retry that same confirmed board, not arbitrary ports.
Do not leave a host process holding the serial port after verification.

To capture boot reliably on the same connection, the following **active**
soft-reboot procedure worked on COM5 after deployment. Unlike a passive monitor,
it deliberately interrupts first, exits raw REPL with Ctrl-B, clears old prompt
bytes, and sends Ctrl-D to execute the stored `main.py`. This version is for the
current blank-settings board:

```powershell
@'
import time
import serial

with serial.Serial("COM5", 115200, timeout=.2) as port:
    port.write(b"\x03\x03")
    time.sleep(.2)
    port.write(b"\x02")
    time.sleep(.2)
    port.read(port.in_waiting)
    port.write(b"\x04")
    output = bytearray()
    started = time.monotonic()
    while time.monotonic() - started < 5:
        output.extend(port.read(port.in_waiting or 1))
    text = output.decode("utf-8", errors="replace")
    print(text)
    assert "Animation demo: setup required" in text
    assert "Traceback" not in text and ">>>" not in text
print("Stored firmware started; serial released without interrupting it")
'@ | .\.venv\Scripts\python.exe -
```

For valid-but-offline settings, observe longer than
`startup_demo_timeout_seconds` and expect `Animation demo: startup connection
timeout` instead. A successfully provisioned live board should **not** be
required to enter demo. For longer observation, extend the bounded read; do not
send periodic Ctrl-C or execute REPL status probes. Context-manager close
releases the connection without stopping the firmware. Do not run another
`exec`/`fs` command after the final restart unless you intend to restart again.
Soft reboot verifies stored application startup, not physical power-cycle or
power-loss behavior.

### Observed results and limits

On 2026-09-20 this attached board completed all three initial cycles, entered
hold at about 189 seconds, and passed two accelerated hourly replay checks.
The final stored application was soft-rebooted successfully and left running
with the serial port released. The private setup fields were still blank.
Treat this as a dated observation, not a guarantee about the next attached
device or its current provisioning.

The initial instrumented one-cycle test rendered about 816 frames in 65 seconds.
A separate uninstrumented golden-candle sample rendered 128 frames in 10.06
seconds (about 12.7 fps). The three-cycle verifier later passed at about 191
seconds. Do not impose an arbitrary minimum frame count based on the nominal
30 fps scheduler: distinguish timeline correctness from performance and measure
the latter separately. `strip.start(60)` is a driver refresh setting, not proof
of 60 newly rendered animation frames per second.

The existing `rainbows.py` uses `set_hsv(..., 1.0, 1.0)`, `fire.py` uses random
HSV brightness up to 1.0, and `sparkles.py` uses channel values near 254. They
provided useful evidence for channel order and brightness, but their 50-pixel
count is not this project's 96-pixel configuration. The user confirmed the
increased brightness was good; preserve it and the current cap.

Serial execution proves firmware behavior, not perceived LED colors, physical
current, temperature, live GitHub billing correctness, or long-term stability.
Do not claim these without corresponding measurements. Button A is active-low
`SW_A` / GPIO12 with a pull-up; onboard RGB status uses the named pins
`LED_R`, `LED_G`, `LED_B`.
The exact working APIs were `plasma.WS2812(pixels, color_order=...)`,
`strip.set_rgb(index, r, g, b)`, `Pin("SW_A", Pin.IN, Pin.PULL_UP)` and
`pimoroni.RGBLED("LED_R", "LED_G", "LED_B")`. Inspect known nonsecret examples
or module names on a different UF2 before assuming board-specific API support.
No real GitHub token, live entitlement mapping, physical current/temperature
measurement, or long-duration soak was established by this deployment.

## Host regression checks

```powershell
.\.venv\Scripts\python.exe -m unittest tests.credit_candle.test_firmware tests.credit_candle.test_animation_demo tests.credit_candle.test_direct_provider tests.credit_candle.test_core tests.credit_candle.test_billing
```

The full command is `python -m unittest discover -s tests -t .`. Existing full
suite portability gates on Windows include Unix `select.poll` constants,
OpenSSL availability for real TLS loopback, and legacy forward-slash path
expectations. Report these accurately; do not weaken production TLS validation
or silently skip tests to claim a clean suite.
This targeted selection passed 70 tests after the repeating-demo change.

Long Windows worktree paths can also exceed the firmware's intentional
128-character configuration path limit when host fixtures use absolute CA paths.
The fixture now copies the certificate into its temporary directory. A `subst`
drive alias did not solve this because `Path.resolve()` recovered the long
underlying path. Keep host fixture paths short; do not relax board validation
to accommodate a host-only path artifact.

When reporting completion, distinguish code upload, on-board assertions,
autonomous boot, host tests, accelerated timing checks and actual physical
observations. Preserve failures and unverified gates rather than describing
the full suite or hardware as universally validated.
