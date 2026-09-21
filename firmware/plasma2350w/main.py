"""PumpkinPi Plasma 2350 W entry point."""

import json
import math
import time

from pumpkinpi_core.accounting import AccountingReducer
from pumpkinpi_core.candle import CandleRenderer
from pumpkinpi_core.power import PowerLimiter
from pumpkinpi_core.runtime import DisplayController
from pumpkinpi_core.timebase import valid_unix_utc, unix_gmtime, port_epoch_offset
from pumpkinpi_core.billing import validate_source
from environment import load_environment
from hardware import PlasmaHardware
from providers.github import DirectGitHubClient
from scheduler import Scheduler


class Clock:
    _utc_anchor = None
    _ticks_anchor = None
    _last_ticks = None
    _elapsed_ms = 0

    @staticmethod
    def ticks_ms():
        function = getattr(time, "ticks_ms", None)
        if function:
            return function()
        monotonic = getattr(time, "monotonic", None)
        if monotonic:
            return int(monotonic() * 1000)
        return int(time.time() * 1000)

    @staticmethod
    def ticks_diff(left, right):
        function = getattr(time, "ticks_diff", None)
        return function(left, right) if function else left - right

    @staticmethod
    def ticks_add(value, delta):
        function = getattr(time, "ticks_add", None)
        return function(value, delta) if function else value + delta

    @staticmethod
    def utc():
        if Clock._utc_anchor is not None:
            current = Clock.ticks_ms()
            elapsed = Clock.ticks_diff(current, Clock._last_ticks)
            if elapsed >= 0:
                Clock._elapsed_ms += elapsed
                Clock._last_ticks = current
                return Clock._utc_anchor + Clock._elapsed_ms // 1000
        return None

    @staticmethod
    def sync_utc(epoch):
        if not valid_unix_utc(epoch):
            raise ValueError("remote UTC is out of range")
        Clock._utc_anchor = epoch
        Clock._ticks_anchor = Clock.ticks_ms()
        Clock._last_ticks = Clock._ticks_anchor
        Clock._elapsed_ms = 0


def load_config(path="config.json"):
    with open(path, "r") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    if config.get("provider", "github") != "github":
        raise ValueError("provider must be github")
    required = ("profile_id", "config_generation", "balance_basis",
                "budget_revision", "device_id")
    for name in required:
        if name not in config:
            raise ValueError("missing config field: " + name)
    text_fields = ["profile_id", "balance_basis", "budget_revision", "device_id"]
    for name in text_fields:
        if not isinstance(config[name], str) or not config[name] \
                or len(config[name]) > 128:
            raise ValueError("invalid config field: " + name)
    generation = config["config_generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) \
            or not 0 <= generation <= 0xffffffff:
        raise ValueError("config_generation must be an unsigned 32-bit integer")
    validate_source(config.get("source"))
    interval = config.get("billing_poll_interval_seconds", 300)
    if type(interval) is not int or not 30 <= interval <= 86400:
        raise ValueError("billing_poll_interval_seconds must be 30..86400")
    for name, default in (("environment_file", ".env"),
                          ("ca_file", "certs/github-root.pem"),
                          ("time_floor_file", "direct-time.json"),
                          ("ntp_host", "pool.ntp.org")):
        value = config.get(name, default)
        if not isinstance(value, str) or not value or len(value) > 128 \
                or any(ord(char) < 32 for char in value):
            raise ValueError("invalid " + name)
    if config.get("color_order", "RGB") not in ("RGB", "RBG", "GRB", "GBR",
                                                "BRG", "BGR"):
        raise ValueError("unsupported color_order")
    for name, default, low, high in (
        ("max_brightness", 1.0, 0, 1),
        ("max_led_milliamps", 1500, 48, 100000),
        ("idle_ma_per_pixel", .5, 0, 100),
        ("channel_ma", 20, 0.001, 1000),
        ("stale_after_seconds", 900, 1, 86400),
        ("startup_demo_timeout_seconds", 30, 0, 300),
    ):
        value = config.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or not low < value <= high:
            raise ValueError("invalid " + name)
    seed = config.get("flicker_seed", 2350)
    if isinstance(seed, bool) or not isinstance(seed, int) \
            or not 0 <= seed <= 0xffffffff:
        raise ValueError("invalid flicker_seed")
    if config.get("max_led_milliamps", 1500) < \
            96 * config.get("idle_ma_per_pixel", .5):
        raise ValueError("max_led_milliamps is below modeled idle current")
    if config.get("exhaustion_policy", "none") not in \
            ("none", "configured-budget"):
        raise ValueError("invalid exhaustion_policy")
    return config


def build_controller(config, provision):
    limiter = PowerLimiter(
        pixels=96,
        max_brightness=config.get("max_brightness", 1.0),
        max_milliamps=config.get("max_led_milliamps", 1500),
        idle_ma_per_pixel=config.get("idle_ma_per_pixel", 0.5),
        channel_ma=config.get("channel_ma", 20.0),
    )
    return DisplayController(
        AccountingReducer(provision, config.get("stale_after_seconds", 900)),
        CandleRenderer(96, config.get("flicker_seed", 2350)),
        limiter,
    )


def build(config, env=None):
    if env is None:
        env = load_environment(config.get("environment_file", ".env"))
    import network
    wlan = network.WLAN(network.STA_IF)
    provision = {
        "profile_id": config["profile_id"],
        "config_generation": config["config_generation"],
        "balance_basis": config["balance_basis"],
        "budget_revision": config["budget_revision"],
        "allow_confirmed_exhaustion":
            config.get("exhaustion_policy") == "configured-budget",
        "allow_unlimited": config["source"].get("unlimited", False),
    }
    controller = build_controller(config, provision)
    transport = DirectGitHubClient(
        config, env["GITHUB_TOKEN"], Clock(), set_board_utc, wlan,
        env["WIFI_SSID"], env["WIFI_PASSWORD"])
    return Scheduler(Clock(), PlasmaHardware(96, config.get("color_order", "RGB")),
                     transport, controller, startup_demo_timeout_seconds=
                     config.get("startup_demo_timeout_seconds", 30))


def build_setup_demo(reason, config=None):
    # Never use partially validated electrical settings or synthetic accounting.
    config = {} if config is None else config
    controller = build_controller(config, {})
    controller.reducer.record_error(reason)
    scheduler = Scheduler(
        Clock(), PlasmaHardware(96, config.get("color_order", "RGB")),
        None, controller)
    print("Setup required: " + reason + "; edit config.json and .env on the board")
    scheduler.start_demo("setup required", 0)
    return scheduler


def build_startup():
    try:
        config = load_config()
    except (OSError, ValueError):
        return build_setup_demo("config.json missing, unreadable or invalid")
    try:
        env = load_environment(config.get("environment_file", ".env"))
    except ValueError:
        return build_setup_demo(".env missing, unreadable or invalid", config)
    # Hardware, code and TLS time-floor failures must not become setup demos.
    return build(config, env)


def set_board_utc(epoch):
    from machine import RTC
    value = unix_gmtime(epoch, time)
    RTC().datetime((value[0], value[1], value[2], value[6],
                    value[3], value[4], value[5], 0))
    actual = int(time.time()) + port_epoch_offset(time)
    if abs(actual - epoch) > 5:
        raise ValueError("RTC did not accept UTC; TLS must not proceed")
    Clock.sync_utc(epoch)


def run():
    scheduler = None
    try:
        scheduler = build_startup()
        while True:
            scheduler.tick()
            sleeper = getattr(time, "sleep_ms", None)
            sleeper(2) if sleeper else time.sleep(0.002)
    finally:
        if scheduler is not None:
            try:
                scheduler.hardware.black()
            finally:
                close = getattr(getattr(scheduler, "transport", None), "close", None)
                if close is not None:
                    close()


if __name__ == "__main__":
    run()
