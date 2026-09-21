"""Run with mpremote run; exercises the real board without changing its files."""

import time

import main
from pumpkinpi_core.demo import AnimationDemo


def verify():
    scheduler = main.build_startup()
    phases = []
    lit_phases = set()
    writes = 0
    original_write = scheduler.hardware.write
    limiter = scheduler.controller.limiter

    def checked_write(frame):
        nonlocal writes
        assert len(frame) == 96, "wrong pixel count"
        assert all(0 <= value <= int(255 * limiter.max_brightness)
                   for rgb in frame for value in rgb), "brightness cap exceeded"
        assert limiter.estimate_milliamps(frame) <= limiter.max_milliamps, \
            "modeled current cap exceeded"
        if scheduler.demo is not None:
            phase = scheduler.demo.phase
            lit = any(any(rgb) for rgb in frame)
            if lit:
                lit_phases.add(phase)
            if phase == "wait":
                assert not lit, "dark pause is not black"
        original_write(frame)
        writes += 1

    scheduler.hardware.write = checked_write
    try:
        initial_seconds = AnimationDemo.INITIAL_CYCLES * AnimationDemo.CYCLE_SECONDS
        deadline_ms = scheduler.startup_timeout_ms + int((initial_seconds + 5) * 1000)
        while scheduler.elapsed_ms < deadline_ms:
            scheduler.tick()
            assert not scheduler.has_live_state, \
                "live balance available; verify demo only on an unprovisioned/offline board"
            if scheduler.demo is not None:
                phase = scheduler.demo.phase
                if not phases or phase != phases[-1]:
                    phases.append(phase)
                    print("DEMO phase=%s elapsed_ms=%d frames=%d" %
                          (phase, scheduler.elapsed_ms, scheduler.frames))
                assert scheduler.controller.reducer.snapshot is None, \
                    "demo populated live accounting"
                if phase == "hold" and scheduler.elapsed_ms / 1000.0 \
                        - scheduler.demo.started >= initial_seconds + 2:
                    break
            time.sleep_ms(2)
        assert phases == ["flash", "wait", "full", "drain", "empty",
                          "recharge"] * 3 + ["hold"], "demo sequence incomplete"
        assert lit_phases == {"flash", "full", "drain", "empty",
                              "recharge", "hold"}, "missing visible demo phase"
        assert writes == scheduler.frames, "not all frames reached hardware"
        demo = scheduler.demo
        for repeat in range(2):
            hold = initial_seconds + repeat * (AnimationDemo.HOLD_SECONDS +
                                               AnimationDemo.CYCLE_SECONDS)
            replay = hold + AnimationDemo.HOLD_SECONDS
            checkpoints = [(hold, "hold"), (replay - .1, "hold"),
                           (replay, "flash"), (replay + 1.5, "wait"),
                           (replay + 16.5, "full"), (replay + 26.5, "drain"),
                           (replay + 56.5, "empty"), (replay + 61.5, "recharge"),
                           (replay + 63, "hold")]
            for elapsed, phase in checkpoints:
                scheduler.hardware.write(demo.frame(demo.started + elapsed))
                assert demo.phase == phase, "hourly replay boundary failed"
                assert scheduler.controller.reducer.snapshot is None
                time.sleep_ms(40)
            print("PASS accelerated hourly replay %d and 3600-second hold" % (repeat + 1))
        print("PASS demo reason=%s frames=%d elapsed_ms=%d" %
              (scheduler.demo_reason, writes, scheduler.elapsed_ms))
    finally:
        scheduler.hardware.black()
        if scheduler.transport is not None:
            scheduler.transport.close()


verify()
