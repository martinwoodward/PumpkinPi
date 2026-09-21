import calendar
from email.utils import formatdate
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from .support import FIRMWARE, ROOT
from environment import load_environment
from pumpkinpi_core import billing
from pumpkinpi_core.accounting import AccountingReducer
from pumpkinpi_core.candle import CandleRenderer
from pumpkinpi_core.power import PowerLimiter
from pumpkinpi_core.runtime import DisplayController
from providers.github import DirectGitHubClient
from scheduler import Scheduler

NOW = calendar.timegm((2026, 9, 18, 10, 0, 0))
MAPPING = {"unit_type": "credit", "product": "copilot", "sku": "ai-credit",
           "quantity_field": "grossQuantity"}


def configuration(folder):
    return {
        "provider": "github", "profile_id": "profile", "config_generation": 1,
        "balance_basis": "configured-budget", "budget_revision": "rev-1",
        "device_id": "pumpkin", "environment_file": str(folder / ".env"),
        "time_floor_file": str(folder / "direct-time.json"),
        "ca_file": str(FIRMWARE / "certs/github-root.pem"),
        "exhaustion_policy": "configured-budget", "billing_poll_interval_seconds": 300,
        "source": {"owner_type": "user", "owner": "octo",
                   "allowance_microcredits": 100_000_000,
                   "quality": "estimated", "mapping": MAPPING},
    }


def response(quantity="25.000001", month=9, now=NOW):
    raw = ('{"timePeriod":{"year":2026,"month":%d},"usageItems":['
           '{"unitType":"credit","product":"copilot","sku":"ai-credit",'
           '"grossQuantity":%s}]}' % (month, quantity)).encode()
    return {"status": 200, "headers": {"date": formatdate(now, usegmt=True)}, "body": raw}


class Clock:
    value = None
    ticks = 0
    def utc(self): return self.value
    def sync_utc(self, value): self.value = value
    def ticks_ms(self): return self.ticks
    def ticks_add(self, a, b): return a + b
    def ticks_diff(self, a, b): return a - b


class WLAN:
    connected = True
    def isconnected(self): return self.connected
    def ifconfig(self): return ("192.0.2.2", "255.255.255.0", "192.0.2.1", "192.0.2.53")
    def active(self, value): self.active_value = value
    def connect(self, ssid, password): self.credentials = (ssid, password)


class Networking:
    class NetworkError(Exception):
        pass

    def __init__(self):
        self.calls = []
        self.operations = []
        self.reply = response()
        self.ntp = NOW
        self.exception = None
        self.rtc_ready = False

    def operation(self, result):
        owner = self
        class Operation:
            closed = False
            pending = True
            def step(self, _now, _diff):
                if self.pending:
                    self.pending = False
                    return None
                if owner.exception is not None:
                    raise owner.exception
                return result
            def close(self): self.closed = True
        op = Operation()
        self.operations.append(op)
        return op

    def DNSLookup(self, hostname, server):
        self.calls.append(("dns", hostname, server))
        return self.operation("192.0.2.10")

    def NTPQuery(self, ip):
        self.calls.append(("ntp", ip))
        return self.operation(self.ntp)

    def HTTPSRequest(self, ip, host, path, token, ca_file, **kwargs):
        if not self.rtc_ready:
            raise AssertionError("TLS started before RTC")
        self.calls.append(("https", host, path, token, ca_file, kwargs))
        return self.operation(self.reply)


class DirectProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = configuration(self.folder)
        self.clock = Clock()
        self.net = Networking()
        self.wlan = WLAN()
        def set_utc(value):
            self.net.rtc_ready = True
            self.clock.sync_utc(value)
        self.client = DirectGitHubClient(
            self.config, "fixture-only-token", self.clock, set_utc, self.wlan,
            "fixture-network", "fixture-password", networking=self.net)
        self.addCleanup(self.client.close)

    def drive(self, count=20, start=0):
        result = []
        for index in range(count):
            self.clock.ticks = start + index * 33
            result.extend(self.client.service(self.clock.ticks, self.clock.ticks_diff))
        return result

    def test_complete_boot_rtc_then_https_exact_credits_and_cleanup(self):
        snapshots = self.drive()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["used_microcredits"], 25_000_001)
        self.assertEqual(snapshots[0]["remaining_microcredits"], 74_999_999)
        self.assertEqual([call[0] for call in self.net.calls], ["dns", "ntp", "dns", "https"])
        https = self.net.calls[-1]
        self.assertEqual(https[1], "api.github.com")
        self.assertIn("/users/octo/settings/billing/ai_credit/usage?year=2026&month=9", https[2])
        self.assertTrue(all(op.closed for op in self.net.operations))
        self.assertEqual(json.loads((self.folder / "direct-time.json").read_text())["unix"], NOW)
        self.assertNotIn("fixture-only-token", json.dumps(snapshots))

    def test_explicit_unlimited_source_still_fetches_exact_usage(self):
        self.config["source"].update(unlimited=True, allowance_microcredits=None)
        result = self.drive()[0]
        self.assertEqual(result["used_microcredits"], 25_000_001)
        self.assertTrue(result["unlimited"])
        self.assertIsNone(result["limit_microcredits"])
        self.assertIsNone(result["remaining_microcredits"])
        self.assertFalse(result["exhaustion_confirmed"])
        self.assertEqual(self.net.calls[-1][0], "https")

    def test_missing_time_or_tls_failure_never_updates_balance(self):
        self.net.exception = self.net.NetworkError("DNS response timeout")
        self.assertEqual(self.drive(), [])
        self.assertIsNone(self.clock.utc())
        self.assertFalse(any(call[0] == "https" for call in self.net.calls))
        self.assertTrue(all(op.closed for op in self.net.operations))

    def test_error_messages_do_not_echo_token(self):
        self.net.exception = ValueError("request with fixture-only-token")
        self.drive()
        self.assertNotIn("fixture-only-token", self.client.error)
        self.assertIn("failure", self.client.error)

    def test_rate_limit_and_auth_retain_prior_with_bounded_polling(self):
        first = self.drive()[0]
        self.net.reply = {"status": 429, "headers": {"retry-after": "900"}, "body": b""}
        self.assertEqual(self.drive(start=301000), [])
        self.assertGreaterEqual(self.client.next_attempt, 1_200_000)
        self.assertEqual(first["remaining_microcredits"], 74_999_999)
        count = len(self.net.calls)
        self.drive(start=600000)
        self.assertEqual(len(self.net.calls), count)
        self.net.reply = {"status": 401, "headers": {}, "body": b""}
        self.drive(start=1_300_000)
        self.assertIn("401", self.client.error)

    def test_redirect_is_not_followed(self):
        self.net.reply = {"status": 302, "headers": {"location": "https://elsewhere.invalid"}, "body": b""}
        self.assertEqual(self.drive(), [])
        self.assertEqual(sum(call[0] == "https" for call in self.net.calls), 1)
        self.assertIn("302", self.client.error)

    def test_response_month_mismatch_and_unsupported_precision_fail_closed(self):
        self.net.reply = response(month=8)
        self.assertEqual(self.drive(), [])
        self.assertIn("month", self.client.error)
        self.net.reply = response("0.0000001")
        self.assertEqual(self.drive(start=40000), [])
        self.assertIn("precision", self.client.error)

    def test_month_rollover_during_response_discards_old_report(self):
        for index in range(20):
            self.client.service(index * 33, self.clock.ticks_diff)
            if self.client.phase == "https":
                break
        self.clock.value = calendar.timegm((2026, 10, 1, 0, 0, 0))
        self.net.reply = response(now=self.clock.value)
        self.client.operation = self.net.operation(self.net.reply)
        result = self.drive(2, start=1000)
        self.assertEqual(result, [])
        self.assertIn("month changed", self.client.error)

    def test_rollback_before_saved_tls_floor_rejected(self):
        self.client.time_floor = NOW + 1000
        self.assertEqual(self.drive(), [])
        self.assertIsNone(self.clock.utc())
        self.assertIn("predates", self.client.error)

    def test_utc_sync_failure_prevents_token_connection(self):
        def failed_rtc(_epoch):
            raise ValueError("RTC refused")
        self.client.set_utc = failed_rtc
        self.assertEqual(self.drive(), [])
        self.assertFalse(any(call[0] == "https" for call in self.net.calls))

    def test_disagreement_with_verified_date_retries_time_bootstrap(self):
        self.net.reply = response(now=NOW + 1000)
        self.assertEqual(self.drive(), [])
        self.assertIsNone(self.client.time_checked)
        self.assertIn("disagrees", self.client.error)
        self.net.reply = response()
        self.assertEqual(len(self.drive(start=40000)), 1)
        self.assertEqual(sum(call[0] == "ntp" for call in self.net.calls), 2)

    def test_corrupt_saved_time_floor_is_explicit_configuration_error(self):
        (self.folder / "direct-time.json").write_text('{"unix":false}')
        with self.assertRaisesRegex(ValueError, "invalid saved TLS time floor"):
            self.client._read_time_floor()

    def test_wifi_reconnect_is_baseline_not_false_activity(self):
        provision = {key: self.config[key] for key in (
            "profile_id", "config_generation", "balance_basis", "budget_revision")}
        reducer = AccountingReducer(provision)
        first = self.drive()[0]
        self.assertEqual(reducer.accept(first, NOW), 0)
        self.wlan.connected = False
        self.drive(1, start=1000)
        self.wlan.connected = True
        self.net.reply = response("50")
        recovered = self.drive(start=301000)[0]
        self.assertTrue(recovered.pop("_stream_rebaseline"))
        reducer.authorize_reconnect(recovered["stream_id"])
        self.assertEqual(reducer.accept(recovered, NOW), 0)
        self.assertEqual(recovered["remaining_microcredits"], 50_000_000)
        self.net.reply = response("51")
        updated = self.drive(start=602000)[0]
        self.assertEqual(reducer.accept(updated, NOW), 1_000_000)

    def test_reconnect_and_tick_wrap_keep_rendering(self):
        self.wlan.connected = False
        self.drive(2)
        self.assertEqual(self.wlan.credentials, ("fixture-network", "fixture-password"))
        self.wlan.connected = True
        self.assertEqual(len(self.drive(start=100)), 1)
        prior = self.client.elapsed
        self.client.last_tick = 250
        self.client.service(20, lambda a, b: ((a-b+128) % 256) - 128)
        self.assertEqual(self.client.elapsed, prior + 26)

    def test_provider_scheduler_depletion_and_estimated_zero(self):
        provision = {key: self.config[key] for key in (
            "profile_id", "config_generation", "balance_basis", "budget_revision")}
        provision["allow_confirmed_exhaustion"] = True
        controller = DisplayController(AccountingReducer(provision), CandleRenderer(), PowerLimiter())
        class Hardware:
            frames = []
            def write(self, frame): self.frames.append(frame)
            def status(self, state): self.state = state
        hardware = Hardware()
        scheduler = Scheduler(self.clock, hardware, self.client, controller)
        for tick in range(0, 1000, 33):
            self.clock.ticks = tick
            scheduler.tick()
        self.assertTrue(any(any(rgb) for rgb in hardware.frames[-1]))
        self.net.reply = response("100")
        for tick in range(301000, 305000, 33):
            self.clock.ticks = tick
            scheduler.tick()
        self.assertTrue(any(any(rgb) for rgb in hardware.frames[-1]))
        self.assertLessEqual(max(max(rgb) for rgb in hardware.frames[-1]), 4)
        self.assertEqual(hardware.state, "depleted")
        self.config["exhaustion_policy"] = "none"
        for tick in range(602000, 603000, 33):
            self.clock.ticks = tick
            scheduler.tick()
        self.assertTrue(any(any(rgb) for rgb in hardware.frames[-1]))
        self.assertEqual(hardware.state, "estimated")


class DirectConfigurationTests(unittest.TestCase):
    def test_environment_literal_values_and_secret_free_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text('WIFI_SSID="a # literal"\nWIFI_PASSWORD=" x=y "\nGITHUB_TOKEN=fixture-only-token\n')
            env = load_environment(path)
            self.assertEqual(env["WIFI_SSID"], "a # literal")
            self.assertEqual(env["WIFI_PASSWORD"], " x=y ")
            for invalid in (
                "GITHUB_TOKEN=fixture-only-token\nGITHUB_TOKEN=duplicate",
                "GITHUB_TOKEN='fixture-only-token",
                "UNKNOWN=fixture-only-token",
                "GITHUB_TOKEN=fixture token value",
            ):
                path.write_text(invalid)
                with self.assertRaises(ValueError) as caught:
                    load_environment(path)
                self.assertNotIn("fixture-only-token", str(caught.exception))
                self.assertNotIn("fixture token value", str(caught.exception))

    def test_config_and_real_main_build_use_direct_https(self):
        import main
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            config = configuration(folder)
            (folder / ".env").write_text(
                "WIFI_SSID=fixture\nWIFI_PASSWORD=fixture\nGITHUB_TOKEN=fixture-only-token\n")
            path = folder / "config.json"
            path.write_text(json.dumps(config))
            loaded = main.load_config(path)
            self.assertEqual(loaded["provider"], "github")
            fake_network = types.SimpleNamespace(WLAN=lambda *_: WLAN(), STA_IF=0)
            class Hardware:
                def __init__(self, *_args): pass
            with patch.dict(sys.modules, {"network": fake_network}), \
                    patch.object(main, "PlasmaHardware", Hardware):
                scheduler = main.build(loaded)
            self.assertIsInstance(scheduler.transport, DirectGitHubClient)
            self.assertEqual(scheduler.transport.token, "fixture-only-token")
            scheduler.transport.close()
            for changes in ({"provider": "unknown"}, {"billing_poll_interval_seconds": True},
                            {"source": {"owner_type": "user"}}, {"environment_file": ""}):
                path.write_text(json.dumps(dict(config, **changes)))
                with self.assertRaises(ValueError):
                    main.load_config(path)

    def test_exact_json_preserves_decimals_and_strings(self):
        data = billing.loads_exact(
            b'{"timePeriod":{"year":2026,"month":9},"quantity":0.000001,'
            b'"text":"a \\"1.23\\" string","exponent":5e-7}')
        self.assertEqual(data["quantity"], "0.000001")
        self.assertEqual(data["timePeriod"]["month"], 9)
        self.assertEqual(data["text"], 'a "1.23" string')
        self.assertEqual(billing.exact_microcredits([data["quantity"]]), 1)
        self.assertEqual(billing.exact_microcredits(["5e-7", data["exponent"]]), 1)
        for raw in (b'{"a":1e}', b'{"a":01}', b'{1.2 : 3}', b"x"*32769):
            with self.assertRaises(billing.BillingError):
                billing.loads_exact(raw)
        for values in ([True], [1.2], ["NaN"], ["1e999999"], ["-1"], ["0.0000001"]):
            with self.assertRaises(billing.BillingError):
                billing.exact_microcredits(values)

    def test_named_source_configuration_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            source = configuration(Path(folder))["source"]
            for kind, route in (("user", "users"), ("organization", "organizations"),
                                ("enterprise", "enterprises")):
                source["owner_type"] = kind
                billing.validate_source(source)
                self.assertTrue(billing.github_path(source, "2026-09").startswith("/"+route+"/octo/"))
            source["owner"] = "bad\r\nhost"
            with self.assertRaises(billing.BillingError):
                billing.validate_source(source)

    def test_rtc_written_and_checked_before_clock_anchor(self):
        import main
        fields = []
        rtc = types.SimpleNamespace(datetime=lambda values: fields.append(values))
        with patch.dict(sys.modules, {"machine": types.SimpleNamespace(RTC=lambda: rtc)}), \
                patch.object(main.time, "time", return_value=NOW), \
                patch.object(main.Clock, "sync_utc") as sync:
            main.set_board_utc(NOW)
            self.assertEqual(fields[0][:3], (2026, 9, 18))
            sync.assert_called_once_with(NOW)
        with patch.dict(sys.modules, {"machine": types.SimpleNamespace(RTC=lambda: rtc)}), \
                patch.object(main.time, "time", return_value=0), \
                patch.object(main.Clock, "sync_utc") as sync:
            with self.assertRaises(ValueError):
                main.set_board_utc(NOW)
            sync.assert_not_called()
        port_time = types.SimpleNamespace(
            time=lambda: NOW - 946684800,
            gmtime=lambda seconds: time.gmtime(seconds + 946684800))
        with patch.dict(sys.modules, {"machine": types.SimpleNamespace(RTC=lambda: rtc)}), \
                patch.object(main, "time", port_time), \
                patch.object(main.Clock, "sync_utc") as sync:
            main.set_board_utc(NOW)
            self.assertEqual(fields[-1][:3], (2026, 9, 18))
            sync.assert_called_once_with(NOW)

    def test_public_root_fingerprint_environment_ignore_and_explicit_upload(self):
        import deploy
        pem = (FIRMWARE / "certs/github-root.pem").read_text()
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        self.assertEqual(digest, "4ff460d54b9c86dabfbcfc5712e0400d2bed3fbc4d4fbdaa86e06adcd2a9ad7a")
        result = subprocess.run(["git", "check-ignore", "firmware/plasma2350w/.env"],
                                cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0)
        result = subprocess.run(["git", "check-ignore", "firmware/plasma2350w/.env.example"],
                                cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1)
        commands = deploy.mpremote_commands(config="private.json", env="private.env")
        self.assertEqual(commands[-1][-1], ":.env")
        self.assertFalse(any(command[-1] == ":.env" for command in deploy.mpremote_commands()))
        self.assertIn("certs/github-root.pem", deploy.FILES)
        self.assertIn("providers/github.py", deploy.FILES)
        self.assertNotIn(".env", deploy.FILES)
