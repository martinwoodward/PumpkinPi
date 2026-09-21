import time
import unittest
from unittest.mock import patch

from .support import PROVISION, snapshot
from pumpkinpi_core.accounting import AccountingReducer, SnapshotError, validate_snapshot
from pumpkinpi_core.candle import CandleRenderer, palette
from pumpkinpi_core.power import PowerConfigError, PowerLimiter
from pumpkinpi_core.runtime import DisplayController
import pumpkinpi_core.accounting as accounting


class AccountingTests(unittest.TestCase):
    def test_rejects_bool_bounds_period_and_inconsistent_amounts(self):
        for change in (
            {"schema_version": True},
            {"used_microcredits": True},
            {"remaining_microcredits": 81},
            {"billing_period": "2026-99"},
            {"billing_period": "2026- 9"},
            {"limit_microcredits": 9_000_000_000_000_001},
        ):
            with self.assertRaises(SnapshotError):
                validate_snapshot(snapshot(**change), PROVISION)

    def test_over_limit_is_zero(self):
        value = validate_snapshot(snapshot(used=120, limit=100,
                                           remaining_microcredits=0), PROVISION)
        self.assertEqual(value["remaining_microcredits"], 0)

    def test_baseline_duplicate_correction_and_later_usage(self):
        reducer = AccountingReducer(PROVISION)
        now = int(time.time())
        self.assertEqual(reducer.accept(snapshot(1, used=20), now), 0)
        self.assertEqual(reducer.accept(snapshot(2, used=30), now), 10)
        self.assertEqual(reducer.accept(snapshot(3, used=25), now), 0)
        self.assertEqual(reducer.accept(snapshot(4, used=28), now), 3)
        with self.assertRaises(SnapshotError):
            reducer.accept(snapshot(4, used=28), now)

    def test_scope_stream_revision_and_period_rejected(self):
        reducer = AccountingReducer(PROVISION)
        now = int(time.time())
        reducer.accept(snapshot(), now)
        for value in (
            snapshot(2, stream="old"),
            snapshot(2, config_generation=0),
            snapshot(2, budget_revision="old"),
            snapshot(2, billing_period="2025-01"),
        ):
            with self.assertRaises(SnapshotError):
                reducer.accept(value, now)

    def test_cached_observation_stales_and_clock_rollback_invalidates(self):
        now = int(time.time())
        reducer = AccountingReducer(PROVISION, stale_after=10)
        reducer.accept(snapshot(observed=now - 20), now)
        self.assertTrue(reducer.state(now)["stale"])
        reducer.last_utc = now + 100
        self.assertFalse(reducer.state(now)["known"])
        with self.assertRaises(SnapshotError):
            reducer.accept(snapshot(2), now)
        reducer.last_utc = now
        self.assertFalse(reducer.state(now + 21601)["known"])

    def test_unknown_is_not_zero(self):
        state = AccountingReducer(PROVISION).state(None)
        self.assertFalse(state["known"])
        unknown = snapshot(quality="unknown", used_microcredits=None,
                           limit_microcredits=None, remaining_microcredits=None)
        reducer = AccountingReducer(PROVISION)
        reducer.accept(unknown, int(time.time()))
        self.assertFalse(reducer.state(int(time.time()))["known"])
        with self.assertRaises(SnapshotError):
            validate_snapshot(snapshot(quality="unknown"), PROVISION)

    def test_future_observation_and_unauthorized_confirmation_rejected(self):
        now = int(time.time())
        with self.assertRaises(SnapshotError):
            AccountingReducer(PROVISION).accept(
                snapshot(observed=now + 301), now)
        provision = dict(PROVISION)
        provision["allow_confirmed_exhaustion"] = False
        with self.assertRaises(SnapshotError):
            validate_snapshot(snapshot(used=100, confirmed=True), provision)

    def test_full_snapshot_accepts_unix_time_on_2000_epoch_port(self):
        unix_now = 1_789_689_600
        class PortTime:
            @staticmethod
            def gmtime(value):
                if value == 0:
                    return (2000, 1, 1, 0, 0, 0, 5, 1)
                return time.gmtime(value + 946684800)
        value = snapshot(observed=unix_now, billing_period="2026-09")
        with patch.object(accounting, "_time", PortTime()):
            reducer = AccountingReducer(PROVISION)
            self.assertEqual(reducer.accept(value, unix_now), 0)
            self.assertTrue(reducer.state(unix_now)["known"])

    def test_authenticated_reconnect_rebaselines_and_old_stream_replay_fails(self):
        now = int(time.time())
        reducer = AccountingReducer(PROVISION)
        reducer.accept(snapshot(10, used=40, stream="old"), now)
        reducer.authorize_reconnect("new")
        self.assertEqual(reducer.accept(
            snapshot(1, used=60, stream="new"), now), 0)
        with self.assertRaises(SnapshotError):
            reducer.accept(snapshot(11, used=70, stream="old"), now)

    def test_authorized_reconfiguration_rebaselines(self):
        reducer = AccountingReducer(PROVISION)
        now = int(time.time())
        reducer.accept(snapshot(1, used=20), now)
        updated = dict(PROVISION)
        updated["budget_revision"] = "rev-2"
        reducer.authorize_reconfiguration(updated)
        self.assertEqual(reducer.accept(snapshot(
            1, used=50, budget_revision="rev-2"), now), 0)


class RenderingTests(unittest.TestCase):
    def test_palette_anchors_and_frame_shape(self):
        self.assertEqual(palette(1), (255, 170, 45))
        self.assertEqual(palette(.5), (255, 132, 72))
        self.assertEqual(palette(.2), (235, 40, 25))
        self.assertEqual(palette(.1), (180, 20, 100))
        self.assertEqual(palette(.02), (100, 12, 170))
        frame = CandleRenderer().render(.5, 1.0)
        self.assertEqual(len(frame), 96)
        self.assertTrue(all(len(rgb) == 3 and max(rgb) <= 255 for rgb in frame))

    def test_seed_is_deterministic_and_correlated(self):
        left = CandleRenderer(seed=7).render(.5, 1.2)
        right = CandleRenderer(seed=7).render(.5, 1.2)
        self.assertEqual(left, right)
        self.assertLess(max(rgb[0] for rgb in left) - min(rgb[0] for rgb in left), 30)

    def test_power_limit_black_quantization_and_visibility_conflict(self):
        limiter = PowerLimiter(max_brightness=.1, max_milliamps=100)
        output = limiter.apply([(255, 255, 255)] * 96)
        self.assertLessEqual(limiter.estimate_milliamps(output), 100)
        self.assertEqual(limiter.apply([(0, 0, 0)] * 96), [(0, 0, 0)] * 96)
        with self.assertRaises(PowerConfigError):
            PowerLimiter(max_milliamps=48)
        barely_above_idle = PowerLimiter(
            max_brightness=.004, max_milliamps=48.001)
        with self.assertRaisesRegex(PowerConfigError, "quantized.*black"):
            barely_above_idle.apply([(255, 255, 255)] * 96)

    def test_estimated_zero_lit_and_confirmed_zero_sputters_then_dull(self):
        reducer = AccountingReducer(PROVISION)
        controller = DisplayController(reducer, CandleRenderer(), PowerLimiter())
        now = int(time.time())
        controller.on_snapshot(snapshot(1, used=99, limit=100), now, 0)
        controller.on_snapshot(snapshot(2, used=100, limit=100,
                                        quality="estimated", confirmed=False), now, .1)
        self.assertTrue(any(rgb != (0, 0, 0)
                            for rgb in controller.frame(now, .2)))
        controller.on_snapshot(snapshot(3, used=100, limit=100,
                                        confirmed=True), now, .3)
        self.assertTrue(any(rgb != (0, 0, 0)
                            for rgb in controller.frame(now, .5)))
        controller.on_snapshot(snapshot(4, used=100, limit=100,
                                        confirmed=True), now, .6)
        self.assertTrue(any(rgb != (0, 0, 0)
                            for rgb in controller.frame(now, .7)))
        frame = controller.frame(now, 2.4)
        self.assertTrue(any(any(rgb) for rgb in frame))
        self.assertLessEqual(max(max(rgb) for rgb in frame), 4)

    def test_positive_recovery_cancels_sputter_and_clears_queued_effects(self):
        reducer = AccountingReducer(PROVISION)
        renderer = CandleRenderer()
        controller = DisplayController(reducer, renderer, PowerLimiter())
        now = int(time.time())
        controller.on_snapshot(snapshot(1, used=90), now, 0)
        renderer.queue_usage(50_000_000)
        controller.on_snapshot(snapshot(2, used=100, confirmed=True), now, .1)
        self.assertEqual(renderer.pulses, [])
        controller.on_snapshot(snapshot(3, used=80), now, .5)
        self.assertTrue(any(rgb != (0, 0, 0)
                            for rgb in controller.frame(now, .6)))

    def test_unknown_and_reconfiguration_clear_effect_state(self):
        reducer = AccountingReducer(PROVISION)
        renderer = CandleRenderer()
        controller = DisplayController(reducer, renderer, PowerLimiter())
        now = int(time.time())
        controller.on_snapshot(snapshot(1, used=50), now, 0)
        renderer.queue_usage(1)
        controller.on_snapshot(snapshot(
            2, quality="unknown", used_microcredits=None,
            limit_microcredits=None, remaining_microcredits=None), now, .1)
        self.assertFalse(controller.had_positive)
        self.assertEqual(renderer.pulses, [])
        updated = dict(PROVISION)
        updated["budget_revision"] = "rev-2"
        controller.authorize_reconfiguration(updated)
        self.assertFalse(controller.had_positive)
