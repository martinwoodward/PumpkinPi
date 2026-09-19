import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from .support import FIRMWARE, ROOT
from .test_direct_provider import configuration
import deploy
import hardware
import main


class FirmwareTests(unittest.TestCase):
    def test_real_adapter_uses_board_pins_and_starts_black(self):
        created, writes = {}, []
        class Strip:
            def __init__(self, pixels, color_order):
                created["strip"] = (pixels, color_order)
            def set_rgb(self, *args): writes.append(args)
            def start(self, rate):
                created["rate"] = rate
                self.black_at_start = len(writes) == 96 and all(
                    entry[1:] == (0, 0, 0) for entry in writes)
        class RGBLED:
            def __init__(self, *pins): created["pins"] = pins
            def set_rgb(self, *_args): pass
        class Pin:
            IN, PULL_UP = "in", "pull-up"
            pressed = False
            def __init__(self, *args): created["button"] = args
            def value(self): return 0 if self.pressed else 1
        with patch.dict(sys.modules, {
                "plasma": types.SimpleNamespace(COLOR_ORDER_GRB="grb", WS2812=Strip),
                "pimoroni": types.SimpleNamespace(RGBLED=RGBLED),
                "machine": types.SimpleNamespace(Pin=Pin)}):
            adapter = hardware.PlasmaHardware()
        self.assertEqual(created["pins"], ("LED_R", "LED_G", "LED_B"))
        self.assertEqual(created["strip"], (96, "grb"))
        self.assertEqual(created["rate"], 60)
        self.assertTrue(adapter.strip.black_at_start)
        self.assertEqual(created["button"], ("SW_A", "in", "pull-up"))
        self.assertFalse(adapter.button_a_pressed())
        adapter.button_a.pressed = True
        self.assertTrue(adapter.button_a_pressed())

    def test_deployment_bundle_is_complete_and_private_upload_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            files = deploy.export_bundle(folder)
            self.assertEqual(set(files), set(deploy.FILES))
            for name in files:
                self.assertEqual((Path(folder) / name).read_bytes(),
                                 (FIRMWARE / name).read_bytes())
            self.assertFalse((Path(folder) / ".env").exists())
            self.assertFalse((Path(folder) / "config.json").exists())
            self.assertIn("pumpkinpi_core/demo.py", files)
            self.assertEqual(set(name for name in files if name.startswith("providers/")),
                             {"providers/__init__.py", "providers/github.py",
                              "providers/direct_network.py"})
            commands = []
            deploy.install("/dev/cu.test", "private.json", env="private.env",
                           runner=lambda command, check: commands.append((command, check)))
            copied = {command[-1] for command, _check in commands[1:]}
            self.assertEqual(copied, {":" + name for name in files} | {":config.json", ":.env"})
            self.assertTrue(all(check for _command, check in commands))

    def test_config_defaults_to_github_and_rejects_invalid_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            config = configuration(folder)
            config.pop("provider")
            path = folder / "config.json"
            path.write_text(json.dumps(config))
            self.assertEqual(main.load_config(path)["source"]["owner"], "octo")
            for change in ({"max_brightness": float("inf")},
                           {"billing_poll_interval_seconds": True},
                           {"startup_demo_timeout_seconds": 0},
                           {"provider": "invalid"}, {"source": {}}):
                path.write_text(json.dumps(dict(config, **change)))
                with self.assertRaises(ValueError):
                    main.load_config(path)

    def test_shutdown_blacks_strip_and_closes_transport(self):
        class Hardware:
            blacked = False
            def black(self): self.blacked = True
        class Transport:
            closed = False
            def close(self): self.closed = True
        class Scheduler:
            hardware, transport = Hardware(), Transport()
            def tick(self): raise RuntimeError("fatal")
        scheduler = Scheduler()
        with patch.object(main, "load_config", return_value={}), \
                patch.object(main, "build", return_value=scheduler):
            with self.assertRaises(RuntimeError):
                main.run()
        self.assertTrue(scheduler.hardware.blacked)
        self.assertTrue(scheduler.transport.closed)

    def test_clock_accumulates_wrapped_ticks_without_wall_clock(self):
        class Ticks:
            value = 1000
            def ticks_ms(self): return self.value
            def ticks_diff(self, a, b): return ((a - b + 512) % 1024) - 512
        ticks = Ticks()
        with patch.object(main, "time", ticks), \
                patch.object(main.Clock, "_utc_anchor", None), \
                patch.object(main.Clock, "_ticks_anchor", None), \
                patch.object(main.Clock, "_last_ticks", None), \
                patch.object(main.Clock, "_elapsed_ms", 0):
            self.assertIsNone(main.Clock.utc())
            main.Clock.sync_utc(1_789_689_600)
            for elapsed in range(100, 5100, 100):
                ticks.value = (1000 + elapsed) % 1024
                self.assertEqual(main.Clock.utc(), 1_789_689_600 + elapsed // 1000)

    def test_setup_skill_points_to_board_upload_and_safe_credentials(self):
        skill = (ROOT / "plugin/skills/pumpkin/SKILL.md").read_text()
        runtime = (ROOT / "docs/plasma-runtime.md").read_text()
        self.assertTrue(skill.startswith("---\nname: pumpkin\n"))
        self.assertIn("Never ask the user to paste", skill)
        self.assertIn("deploy.py install", skill)
        self.assertIn(".github/skills/pumpkin/SKILL.md", runtime)
