"""Display state machine independent of hardware and transport."""

from pumpkinpi_core.power import PowerConfigError


class DisplayController:
    def __init__(self, reducer, renderer, limiter):
        self.reducer = reducer
        self.renderer = renderer
        self.limiter = limiter
        self.started = False
        self.was_positive = False
        self.had_positive = False
        self.last_confirmed = False
        self.sputter_started = None
        self.recharge_started = None
        self.awaiting_recharge = False

    def _reset_effect_state(self, preserve_exhaustion=False):
        self.renderer.clear_effects()
        self.was_positive = False
        self.had_positive = False
        self.last_confirmed = False
        self.sputter_started = None
        self.recharge_started = None
        if not preserve_exhaustion:
            self.awaiting_recharge = False

    def authorize_reconfiguration(self, provision):
        self.reducer.authorize_reconfiguration(provision)
        self._reset_effect_state()

    def on_snapshot(self, snapshot, now_utc, now_mono):
        prior = self.reducer.snapshot
        delta = self.reducer.accept(snapshot, now_utc)
        state = self.reducer.state(now_utc)
        transitioned = prior is not None and (
            prior.get("billing_period") != snapshot.get("billing_period")
            or prior.get("budget_revision") != snapshot.get("budget_revision")
        )
        self.update_effects(state, now_mono, delta, transitioned)
        self.started = True

    def update_effects(self, state, now_mono, delta=0, transitioned=False):
        remaining = state.get("remaining_microcredits")
        positive = bool(state.get("unlimited")) or (remaining is not None and remaining > 0)
        recovering = self.awaiting_recharge and positive
        if transitioned or not state.get("known"):
            self._reset_effect_state(preserve_exhaustion=True)
        if positive:
            if recovering:
                self.renderer.clear_effects()
                self.recharge_started = now_mono
            self.awaiting_recharge = False
            self.sputter_started = None
            self.had_positive = True
        elif state.get("confirmed_zero") and not self.last_confirmed \
                and self.sputter_started is None:
            self.sputter_started = now_mono
            self.renderer.clear_effects()
        if state.get("confirmed_zero"):
            self.recharge_started = None
            self.awaiting_recharge = True
        if delta and positive:
            self.renderer.queue_usage(delta)
        self.was_positive = positive
        self.last_confirmed = bool(state.get("confirmed_zero"))

    def frame(self, now_utc, now_mono):
        return self.render_state(self.reducer.state(now_utc), now_mono)

    def render_state(self, state, now_mono):
        if not state.get("known"):
            self._reset_effect_state(preserve_exhaustion=True)
            return [(0, 0, 0)] * self.limiter.pixels
        confirmed = state.get("confirmed_zero")
        sputter = None
        if confirmed:
            if self.sputter_started is not None:
                sputter = now_mono - self.sputter_started
                if sputter >= 2.0:
                    sputter = None
        recharge = None if self.recharge_started is None else now_mono - self.recharge_started
        fraction = state.get("fraction")
        if confirmed:
            fraction = 0.0
        if state.get("remaining_microcredits") == 0 and not confirmed:
            fraction = 0.02
        frame = self.limiter.apply(
            self.renderer.render(fraction, now_mono, sputter=sputter,
                                 exhausted=confirmed,
                                 unlimited=state.get("unlimited", False))
        )
        if recharge is not None and not confirmed and recharge < 1.5:
            # Blend already-limited endpoints so high brightness cannot clip
            # the one-second rise into an early, constant-current plateau.
            white = self.limiter.apply([(255, 255, 255)] * self.limiter.pixels)
            if recharge <= 1.0:
                start = self.limiter.apply([(12, 1, 20)] * self.limiter.pixels)
                target, mix = white, max(0.0, recharge)
            else:
                start, target, mix = white, frame, (recharge - 1.0) / .5
            frame = [tuple(int(a + (b - a) * mix) for a, b in zip(left, right))
                     for left, right in zip(start, target)]
        if not any(any(rgb) for rgb in frame):
            raise PowerConfigError("configured brightness/current cannot show the candle safely")
        return frame
