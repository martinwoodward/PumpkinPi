import time
import unittest

from .support import PROVISION, snapshot
from pumpkinpi_core.accounting import AccountingReducer, SnapshotError, validate_snapshot
from pumpkinpi_core.billing import BillingError, source_limit
from pumpkinpi_core.candle import CandleRenderer
from pumpkinpi_core.demo import AnimationDemo
from pumpkinpi_core.power import PowerConfigError, PowerLimiter
from pumpkinpi_core.runtime import DisplayController
from scheduler import Scheduler


def controller():
    return DisplayController(AccountingReducer(PROVISION), CandleRenderer(), PowerLimiter())


class AnimationTests(unittest.TestCase):
    def test_exhausted_flicker_persists_and_varies_without_becoming_black(self):
        control = controller()
        now = int(time.time())
        control.on_snapshot(snapshot(used=100, confirmed=True), now, 0)
        frames = [control.frame(now, 3 + tick / 30) for tick in range(300)]
        self.assertTrue(all(any(any(rgb) for rgb in frame) for frame in frames))
        self.assertLessEqual(max(max(rgb) for frame in frames for rgb in frame), 30)
        self.assertGreater(len({tuple(frame) for frame in frames}), 10)
        control.on_snapshot(snapshot(2, used=100, confirmed=True), now, 20)
        self.assertEqual(control.sputter_started, 0)
        self.assertLessEqual(max(max(rgb) for rgb in control.frame(now, 20)), 30)

    def test_zero_limit_is_depleted_not_unlimited(self):
        control = controller()
        now = int(time.time())
        control.on_snapshot(snapshot(used=0, limit=0, confirmed=True), now, 0)
        self.assertFalse(control.reducer.state(now)["unlimited"])
        self.assertTrue(any(any(rgb) for rgb in control.frame(now, 3)))

    def test_impossibly_dim_configuration_fails_explicitly_not_silent_black(self):
        control = controller()
        control.limiter = PowerLimiter(max_brightness=.004)
        now = int(time.time())
        control.on_snapshot(snapshot(used=100, confirmed=True), now, 0)
        with self.assertRaisesRegex(PowerConfigError, "cannot show"):
            control.frame(now, 3)

    def test_one_second_white_recharge_then_golden_and_no_retrigger(self):
        control = controller()
        now = int(time.time())
        control.on_snapshot(snapshot(used=100, confirmed=True), now, 0)
        control.on_snapshot(snapshot(2, used=0), now, 5)
        start, middle, white = [control.frame(now, t) for t in (5, 5.5, 6)]
        self.assertLess(sum(start[0]), sum(middle[0]))
        self.assertLess(sum(middle[0]), sum(white[0]))
        self.assertEqual(white, [(64, 64, 64)] * 96)
        control.on_snapshot(snapshot(3, used=0), now, 6.1)
        self.assertEqual(control.recharge_started, 5)
        golden = control.frame(now, 6.5)
        self.assertGreater(sum(r > g > b for r, g, b in golden), 85)
        self.assertIsNone(control.sputter_started)

    def test_recharge_survives_unknown_period_gap_and_cancels_on_new_zero(self):
        control = controller()
        now = int(time.time())
        control.on_snapshot(snapshot(used=100, confirmed=True), now, 0)
        control.render_state({"known": False}, 3)
        control.on_snapshot(snapshot(2, used=0), now, 4)
        self.assertEqual(control.frame(now, 5), [(64, 64, 64)] * 96)
        control.on_snapshot(snapshot(3, used=100, confirmed=True), now, 5.1)
        self.assertIsNone(control.recharge_started)
        self.assertEqual(control.sputter_started, 5.1)

    def test_sparkles_persist_only_for_full_or_unlimited(self):
        renderer = CandleRenderer()
        full = [renderer.render(1, tick / 30) for tick in range(300)]
        for second in range(10):
            frames = full[second * 30:(second + 1) * 30]
            self.assertTrue(any(b >= 250 for frame in frames for r, g, b in frame))
            self.assertGreater(sum(any(b > 80 for r, g, b in frame) for frame in frames), 20)
        below = [renderer.render(.999, tick / 30) for tick in range(100)]
        self.assertTrue(all(b < 50 for frame in below for r, g, b in frame))
        for tick in (0, 1, 70, 3000):
            self.assertEqual(renderer.render(None, tick, unlimited=True),
                             renderer.render(1, tick))
        self.assertNotEqual(full[0], full[15])

    def test_unlimited_is_explicit_authorized_and_not_an_unknown_limit(self):
        value = snapshot(used=123, unlimited=True, limit_microcredits=None,
                         remaining_microcredits=None)
        with self.assertRaises(SnapshotError):
            validate_snapshot(value, PROVISION)
        provision = dict(PROVISION, allow_unlimited=True)
        normalized = validate_snapshot(value, provision)
        self.assertIsNone(normalized["remaining_microcredits"])
        reducer = AccountingReducer(provision)
        reducer.accept(value, int(time.time()))
        self.assertTrue(reducer.state(int(time.time()))["known"])
        self.assertEqual(reducer.state(int(time.time()))["fraction"], 1)
        for changes in ({"unlimited": "true"}, {"unlimited": False},
                        {"exhaustion_confirmed": True}, {"quality": "unknown"},
                        {"limit_microcredits": 100}):
            with self.assertRaises(SnapshotError):
                validate_snapshot(dict(value, **changes), provision)
        self.assertIsNone(source_limit({"unlimited": True, "allowance_microcredits": None}))
        for invalid in ({}, {"unlimited": 1}, {"unlimited": True, "allowance_microcredits": 5}):
            with self.assertRaises(BillingError):
                source_limit(invalid)

    def test_demo_timeline_three_cycles_then_hourly_replay(self):
        control = controller()
        demo = AnimationDemo(control, 100)
        checkpoints = [(0, "flash"), (1.499, "flash"), (1.5, "wait"),
                       (16.499, "wait"), (16.5, "full"), (26.499, "full"),
                       (26.5, "drain"), (56.499, "drain"), (56.5, "empty"),
                       (61.499, "empty"), (61.5, "recharge"), (62.5, "recharge"),
                       (63, "flash"), (126, "flash"), (188.999, "recharge"),
                       (189, "hold"), (3788.999, "hold"), (3789, "flash"),
                       (3851.999, "recharge"), (3852, "hold"),
                       (7451.999, "hold"), (7452, "flash"), (7515, "hold")]
        for elapsed, phase in checkpoints:
            self.assertEqual(demo.state_at(100 + elapsed)[0], phase)
        samples = [demo.frame(100 + i * .25 + .01) for i in range(6)]
        self.assertEqual([any(any(rgb) for rgb in frame) for frame in samples],
                         [True, False, True, False, True, False])
        self.assertEqual(demo.frame(103), [(0, 0, 0)] * 96)
        demo.frame(126.5)
        demo.frame(141.5)
        demo.frame(156.5)
        self.assertLessEqual(max(max(rgb) for rgb in demo.frame(160)), 30)
        demo.frame(161.5)
        self.assertEqual(demo.frame(162.5), [(64, 64, 64)] * 96)
        self.assertTrue(any(b > 8 for r, g, b in demo.frame(100000)))
        self.assertIsNone(control.reducer.snapshot)

    def test_hourly_hold_durations_and_replay_phases_do_not_drift(self):
        demo = AnimationDemo(controller(), 123)
        self.assertEqual(demo.CYCLE_SECONDS, 63)
        self.assertEqual(demo.INITIAL_CYCLES, 3)
        self.assertEqual(demo.HOLD_SECONDS, 3600)
        for repeat in range(100):
            hold = 123 + 189 + repeat * 3663
            self.assertEqual(demo.state_at(hold), ("hold", 0))
            self.assertEqual(demo.state_at(hold + 3599.5), ("hold", 3599.5))
            replay = hold + 3600
            for offset, phase in ((0, "flash"), (1.5, "wait"), (16.5, "full"),
                                  (26.5, "drain"), (56.5, "empty"),
                                  (61.5, "recharge"), (63, "hold")):
                self.assertEqual(demo.state_at(replay + offset), (phase, 0))

    def test_new_cycle_clears_effects_even_when_frames_skip_boundaries(self):
        control = controller()
        demo = AnimationDemo(control, 0)
        demo.frame(59)
        self.assertTrue(control.awaiting_recharge)
        demo.frame(63 + 17)
        self.assertEqual(demo.cycle, 1)
        self.assertFalse(control.awaiting_recharge)
        self.assertIsNone(control.recharge_started)
        demo.frame(126 + 59)
        hold = demo.frame(189)
        self.assertEqual(demo.phase, "hold")
        self.assertIsNone(control.recharge_started)
        self.assertGreater(sum(r > g > b for r, g, b in hold), 85)
        self.assertNotEqual(hold, demo.frame(190))
        demo.frame(3789)
        self.assertEqual(demo.cycle, 3)
        self.assertIsNone(control.sputter_started)
        self.assertIsNone(control.reducer.snapshot)

    def test_entire_demo_is_bounded_by_brightness_and_current(self):
        control = controller()
        demo = AnimationDemo(control, 0)
        control.limiter = PowerLimiter(max_brightness=.2, max_milliamps=200)
        times = [tick / 30 for tick in range(192 * 30)]
        times += [3788.9 + tick / 30 for tick in range(65 * 30)]
        for now in times:
            frame = demo.frame(now)
            self.assertEqual(len(frame), 96)
            self.assertLessEqual(max(max(rgb) for rgb in frame), 51)
            self.assertLessEqual(control.limiter.estimate_milliamps(frame), 200)

    def test_recharge_rises_for_full_second_even_when_current_limited(self):
        for brightness in (.1, 1.0):
            with self.subTest(brightness=brightness):
                control = controller()
                control.limiter = PowerLimiter(max_brightness=brightness)
                demo = AnimationDemo(control, 0)
                frames = [demo.frame(187.5 + tick / 10) for tick in range(16)]
                levels = [sum(frame[0]) for frame in frames[:11]]
                self.assertTrue(all(a < b for a, b in zip(levels, levels[1:])))
                self.assertEqual(frames[10], control.limiter.apply([(255, 255, 255)] * 96))
                for frame in frames:
                    self.assertLessEqual(control.limiter.estimate_milliamps(frame), 1500)


class SchedulerDemoTests(unittest.TestCase):
    def setUp(self):
        class Clock:
            value = 0
            def ticks_ms(self): return self.value
            def ticks_add(self, a, b): return a + b
            def ticks_diff(self, a, b): return a - b
            def utc(self): return int(time.time())
        class Hardware:
            pressed = False
            def button_a_pressed(self): return self.pressed
            def write(self, frame): self.frame = frame
            def status(self, value): self.state = value
        class Transport:
            calls = 0
            closed = False
            queue = []
            error = None
            def service(self, *_args):
                self.calls += 1
                if self.queue:
                    return [self.queue.pop(0)]
                return []
            def close(self): self.closed = True
        self.clock, self.hardware, self.transport = Clock(), Hardware(), Transport()
        self.controller = controller()
        self.scheduler = Scheduler(self.clock, self.hardware, self.transport, self.controller)

    def tick(self, milliseconds):
        self.clock.value = milliseconds
        self.scheduler.tick()

    def test_a_debounced_at_boot_latches_until_restart_and_stops_network(self):
        self.hardware.pressed = True
        self.tick(0)
        self.tick(49)
        self.assertIsNone(self.scheduler.demo)
        self.tick(50)
        demo = self.scheduler.demo
        self.assertIsNotNone(demo)
        self.assertTrue(self.transport.closed)
        calls = self.transport.calls
        self.hardware.pressed = False
        self.tick(100)
        self.tick(160)
        self.hardware.pressed = True
        self.tick(200)
        self.tick(260)
        self.tick(190000)
        self.assertIs(self.scheduler.demo, demo)
        self.assertEqual(demo.phase, "hold")
        self.assertEqual(self.transport.calls, calls)
        self.assertEqual(self.hardware.state, "demo")
        self.assertIsNone(self.controller.reducer.snapshot)
        new_scheduler = Scheduler(self.clock, self.hardware, self.transport, controller())
        self.assertIsNone(new_scheduler.demo)

    def test_button_bounce_does_not_start_demo(self):
        for tick in range(0, 100, 10):
            self.hardware.pressed = not self.hardware.pressed
            self.tick(tick)
        self.hardware.pressed = False
        self.tick(110)
        self.tick(200)
        self.assertIsNone(self.scheduler.demo)

    def test_no_valid_server_data_falls_back_at_30_seconds_only(self):
        self.transport.error = "Wi-Fi unavailable"
        self.tick(0)
        self.tick(29999)
        self.assertIsNone(self.scheduler.demo)
        self.tick(30000)
        self.assertEqual(self.scheduler.demo_reason, "startup connection timeout")
        self.assertEqual(self.controller.reducer.error, "Wi-Fi unavailable")
        self.assertTrue(self.transport.closed)

    def test_fresh_zero_prevents_fallback_and_later_failure_stays_live(self):
        self.transport.queue = [snapshot(used=100, confirmed=True)]
        self.tick(0)
        self.transport.error = "connection unavailable"
        self.tick(30001)
        self.assertIsNone(self.scheduler.demo)
        self.assertTrue(self.scheduler.has_live_state)
        self.assertEqual(self.hardware.state, "error")

    def test_invalid_or_stale_snapshot_does_not_suppress_fallback(self):
        self.transport.queue = [snapshot(observed=int(time.time()) - 1000)]
        self.tick(0)
        self.transport.queue = [snapshot(2, used_microcredits=True)]
        self.tick(30001)
        self.assertIsNotNone(self.scheduler.demo)

    def test_demo_and_debounce_work_across_tick_wrap_without_rtc(self):
        self.clock.ticks_diff = lambda a, b: ((a - b + 512) % 1024) - 512
        self.clock.ticks_add = lambda a, b: (a + b) % 1024
        self.clock.utc = lambda: None
        self.hardware.pressed = True
        for elapsed in range(0, 191000, 33):
            self.tick(elapsed % 1024)
        self.assertEqual(self.scheduler.demo.phase, "hold")
        self.assertGreater(self.scheduler.frames, 1900)

    def test_live_snapshot_at_timeout_wins_and_a_overrides_live(self):
        self.tick(0)
        self.transport.queue = [snapshot(used=0)]
        self.tick(30000)
        self.assertIsNone(self.scheduler.demo)
        self.hardware.pressed = True
        self.tick(30100)
        self.tick(30150)
        self.assertEqual(self.scheduler.demo_reason, "button A")
