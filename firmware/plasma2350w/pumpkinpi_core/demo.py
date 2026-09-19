"""Latched, monotonic animation demo; never writes synthetic billing snapshots."""


class AnimationDemo:
    FLASH_SECONDS = 1.5
    WAIT_SECONDS = 15
    FULL_SECONDS = 10
    DRAIN_SECONDS = 30
    EMPTY_SECONDS = 5

    def __init__(self, controller, started):
        self.controller = controller
        self.started = started
        self.phase = "flash"
        controller._reset_effect_state()

    def state_at(self, now):
        elapsed = max(0.0, now - self.started)
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
        return ("recharge" if elapsed < 1.5 else "hold"), elapsed

    def frame(self, now):
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
        elif phase in ("recharge", "hold"):
            self.controller.recharge_started = now - elapsed
        return self.controller.render_state(state, now)
