"""Standalone GitHub polling with cooperative Wi-Fi, time sync and verified TLS."""

import json
import os
import time

from pumpkinpi_core.billing import (BillingError, github_path, loads_exact,
                                    normalize_usage, validate_source)
from pumpkinpi_core.timebase import unix_gmtime, valid_unix_utc

API_HOST = "api.github.com"


def period_at(epoch):
    value = unix_gmtime(epoch, time)
    return "%04d-%02d" % (value[0], value[1])


def http_date(value):
    try:
        parts = value.split()
        if len(parts) != 6 or parts[-1] != "GMT":
            raise ValueError
        month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec").index(parts[2]) + 1
        year, day = int(parts[3]), int(parts[1])
        hour, minute, second = [int(piece) for piece in parts[4].split(":")]
        leap = lambda y: y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
        lengths = [31, 29 if leap(year) else 28, 31, 30, 31, 30,
                   31, 31, 30, 31, 30, 31]
        if not 2020 <= year <= 2100 or not 1 <= day <= lengths[month - 1] \
                or not 0 <= hour < 24 or not 0 <= minute < 60 or not 0 <= second < 60:
            raise ValueError
        days = sum(366 if leap(y) else 365 for y in range(1970, year))
        days += sum(lengths[:month - 1]) + day - 1
        return days * 86400 + hour * 3600 + minute * 60 + second
    except (ValueError, TypeError, AttributeError):
        raise BillingError("invalid GitHub date header")


def retry_seconds(headers, now):
    value = headers.get("retry-after")
    if value is not None:
        try:
            delay = int(value) if value.isdigit() else http_date(value) - now
            return max(1, delay)
        except (ValueError, TypeError, AttributeError):
            raise BillingError("invalid GitHub Retry-After header")
    if headers.get("x-ratelimit-remaining") == "0":
        try:
            return max(1, int(headers["x-ratelimit-reset"]) - now)
        except (ValueError, KeyError):
            raise BillingError("invalid GitHub rate limit reset")
    return None


class DirectGitHubClient:
    def __init__(self, config, token, clock, set_utc, wlan, wifi_ssid,
                 wifi_password, networking=None):
        validate_source(config["source"])
        if networking is None:
            from providers import direct_network as networking
        self.networking = networking
        self.config = config
        self.source = config["source"]
        self.token = token
        self.clock = clock
        self.set_utc = set_utc
        self.wlan = wlan
        self.wifi_ssid = wifi_ssid
        self.wifi_password = wifi_password
        self.poll_ms = config.get("billing_poll_interval_seconds", 300) * 1000
        self.error = None
        self.phase = "idle"
        self.operation = None
        self.last_tick = None
        self.elapsed = 0
        self.next_attempt = 0
        self.next_wifi = 0
        self.time_checked = None
        self.failures = 0
        self.sequence = 0
        self.needs_baseline = False
        self.stream_id = "".join("%02x" % b for b in os.urandom(16))
        self.request_period = None
        self.time_floor_path = config.get("time_floor_file", "direct-time.json")
        self.time_floor = self._read_time_floor()

    def _read_time_floor(self):
        try:
            with open(self.time_floor_path) as handle:
                raw = handle.read(257)
        except OSError as exc:
            if exc.args and exc.args[0] == 2:
                return None
            raise ValueError("cannot read saved TLS time floor")
        try:
            value = json.loads(raw)["unix"]
            if not valid_unix_utc(value):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError):
            raise ValueError("invalid saved TLS time floor; inspect device state")

    def _save_time_floor(self, verified_date):
        if self.time_floor is not None and verified_date - self.time_floor < 86400:
            return
        temporary = self.time_floor_path + ".tmp"
        with open(temporary, "w") as handle:
            handle.write(json.dumps({"unix": verified_date}))
        os.rename(temporary, self.time_floor_path)
        self.time_floor = verified_date

    def _close_operation(self):
        if self.operation is not None:
            self.operation.close()
            self.operation = None

    def close(self):
        self._close_operation()

    def _failed(self, message, delay=None):
        self._close_operation()
        self.phase = "idle"
        self.error = message
        self.failures = min(self.failures + 1, 6)
        wait = delay if delay is not None else min(900, 15 * 2 ** self.failures)
        self.next_attempt = self.elapsed + max(1, wait) * 1000

    def _dns(self, hostname, phase):
        dns_server = self.wlan.ifconfig()[3]
        self.operation = self.networking.DNSLookup(hostname, dns_server)
        self.phase = phase

    def service(self, now_ms, ticks_diff):
        if self.last_tick is not None:
            self.elapsed += max(0, ticks_diff(now_ms, self.last_tick))
        self.last_tick = now_ms
        try:
            if not self.wlan.isconnected():
                self._close_operation()
                self.phase = "idle"
                self.needs_baseline = True
                self.error = "Wi-Fi unavailable"
                if self.elapsed >= self.next_wifi:
                    self.next_wifi = self.elapsed + 10000
                    self.wlan.active(True)
                    self.wlan.connect(self.wifi_ssid, self.wifi_password)
                return []
            if self.elapsed < self.next_attempt:
                return []
            if self.operation is None:
                if self.clock.utc() is None or self.time_checked is None \
                        or self.elapsed - self.time_checked >= 21600000:
                    self._dns(self.config.get("ntp_host", "pool.ntp.org"), "dns-time")
                else:
                    self.request_period = period_at(self.clock.utc())
                    self._dns(API_HOST, "dns-api")
                return []
            # Each scheduler tick advances just one bounded network operation.
            result = self.operation.step(now_ms, ticks_diff)
            if result is None:
                return []
            self._close_operation()
            if self.phase == "dns-time":
                self.operation = self.networking.NTPQuery(result)
                self.phase = "ntp"
            elif self.phase == "ntp":
                if not valid_unix_utc(result) or (
                        self.time_floor is not None and result < self.time_floor - 300):
                    raise BillingError("NTP time predates the saved verified TLS time")
                self.set_utc(result)
                self.time_checked = self.elapsed
                self.phase = "idle"
            elif self.phase == "dns-api":
                self.operation = self.networking.HTTPSRequest(
                    result, API_HOST, github_path(self.source, self.request_period),
                    self.token, self.config.get("ca_file", "certs/github-root.pem"),
                    timeout_ms=15000, max_body=32768)
                self.phase = "https"
            elif self.phase == "https":
                return self._response(result)
            return []
        except self.networking.NetworkError as exc:
            if self.phase == "https":
                self.time_checked = None
            self._failed(str(exc))
        except BillingError as exc:
            self._failed(str(exc))
        except (OSError, ValueError, MemoryError):
            # Network/config values and exception reprs can contain credentials.
            self._failed("direct provider I/O, configuration or memory failure")
        return []

    def _response(self, response):
        now = self.clock.utc()
        status = response["status"]
        if status != 200:
            delay = retry_seconds(response["headers"], now)
            if status in (401, 403) and delay is None:
                delay = max(300, self.poll_ms // 1000)
            self._failed("GitHub HTTP %d; no balance update" % status, delay)
            return []
        verified_date = http_date(response["headers"].get("date"))
        if abs(verified_date - now) > 300 or (
                self.time_floor is not None and verified_date < self.time_floor - 300):
            self.time_checked = None
            raise BillingError("verified GitHub date disagrees with the board clock")
        self._save_time_floor(verified_date)
        if period_at(now) != self.request_period:
            self.phase = "idle"
            self.next_attempt = self.elapsed
            self.error = "billing month changed during request; fetching the new month"
            return []
        data = loads_exact(response["body"])
        used, limit = normalize_usage(data, self.source, self.request_period)
        if self.needs_baseline:
            self.stream_id = "".join("%02x" % b for b in os.urandom(16))
            self.sequence = 0
        self.sequence += 1
        snapshot = {
            "schema_version": 1,
            "source": "github-direct",
            "stream_id": self.stream_id,
            "sequence": self.sequence,
            "profile_id": self.config["profile_id"],
            "config_generation": self.config["config_generation"],
            "balance_basis": self.config["balance_basis"],
            "budget_revision": self.config["budget_revision"],
            "billing_period": self.request_period,
            "observed_at": now,
            "source_as_of": None,
            "used_microcredits": used,
            "limit_microcredits": limit,
            "remaining_microcredits": None if limit is None else max(limit - used, 0),
            "unlimited": self.source.get("unlimited", False),
            "quality": "estimated",
            "exhaustion_confirmed": limit is not None and used >= limit and
                self.config.get("exhaustion_policy") == "configured-budget",
        }
        if self.needs_baseline:
            snapshot["_stream_rebaseline"] = True
            self.needs_baseline = False
        self.phase = "idle"
        self.next_attempt = self.elapsed + self.poll_ms
        self.failures = 0
        self.error = None
        return [snapshot]
