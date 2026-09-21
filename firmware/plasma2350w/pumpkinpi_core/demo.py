"""Latched, monotonic animation demo; never writes synthetic billing snapshots."""


class AnimationDemo:
    FLASH_SECONDS = 1.5
    WAIT_SECONDS = 15
    FULL_SECONDS = 10
    DRAIN_SECONDS = 30
    EMPTY_SECONDS = 5
    RECHARGE_SECONDS = 1.5
    INITIAL_CYCLES = 3
    HOLD_SECONDS = 60 * 60
    CYCLE_SECONDS = (FLASH_SECONDS + WAIT_SECONDS + FULL_SECONDS +
                     DRAIN_SECONDS + EMPTY_SECONDS + RECHARGE_SECONDS)

    def __init__(self, controller, started):
        self.controller = controller
        self.started = started
        self.phase = "flash"
        self.cycle = 0
        controller._reset_effect_state()

    def _cycle_at(self, now):
        elapsed = max(0.0, now - self.started)
        initial = self.INITIAL_CYCLES * self.CYCLE_SECONDS
        if elapsed < initial:
            return int(elapsed // self.CYCLE_SECONDS), elapsed % self.CYCLE_SECONDS
        elapsed -= initial
        period = self.HOLD_SECONDS + self.CYCLE_SECONDS
        repeat = int(elapsed // period)
        elapsed %= period
        if elapsed < self.HOLD_SECONDS:
            return self.INITIAL_CYCLES - 1 + repeat, self.CYCLE_SECONDS + elapsed
        return self.INITIAL_CYCLES + repeat, elapsed - self.HOLD_SECONDS

    def state_at(self, now):
        _, elapsed = self._cycle_at(now)
        if elapsed < self.FLASH_SECONDS:
            return "flash", elapsed
        elapsed -= self.FLASH_SECONDS
        if elapsed < self.WAIT_SECONDS:
            return "wait", elapsed
        elapsed -= self.WAIT_SECONDS
        if elapsed < self.FULL_SECONDS:
            return "full", elapsed
        elapsed -= self.FULL_SECONDS
        if elapsed < self.DRAIN_SECONDS:
            return "drain", elapsed
        elapsed -= self.DRAIN_SECONDS
        if elapsed < self.EMPTY_SECONDS:
            return "empty", elapsed
        elapsed -= self.EMPTY_SECONDS
        if elapsed < self.RECHARGE_SECONDS:
            return "recharge", elapsed
        return "hold", elapsed - self.RECHARGE_SECONDS

    def frame(self, now):
        cycle, _ = self._cycle_at(now)
        if cycle != self.cycle:
            self.controller._reset_effect_state()
            self.cycle = cycle
        phase, elapsed = self.state_at(now)
        self.phase = phase
        if phase in ("flash", "wait"):
            on = phase == "flash" and int(elapsed / .25) % 2 == 0
            color = (255, 255, 255) if on else (0, 0, 0)
            return self.controller.limiter.apply([color] * self.controller.limiter.pixels)
        fraction = max(0.0, 1.0 - elapsed / self.DRAIN_SECONDS) if phase == "drain" else 1.0
        if phase == "empty":
            fraction = 0.0
        state = {"known": True, "fraction": fraction,
                 "remaining_microcredits": int(fraction * 100_000_000),
                 "confirmed_zero": phase == "empty", "unlimited": False}
        self.controller.update_effects(state, now)
        # Use phase boundaries, not frame arrival time, even if a frame was delayed.
        if phase == "empty":
            self.controller.sputter_started = now - elapsed
        elif phase == "recharge":
            self.controller.recharge_started = now - elapsed
        elif phase == "hold":
            self.controller.recharge_started = None
        return self.controller.render_state(state, now)
