"""Bounded 30 fps cooperative scheduler."""


class Scheduler:
    FRAME_MS = 33

    def __init__(self, clock, hardware, transport, controller,
                 startup_demo_timeout_seconds=30):
        self.clock = clock
        self.hardware = hardware
        self.transport = transport
        self.controller = controller
        self.next_frame = clock.ticks_ms()
        self.last_tick = self.next_frame
        self.elapsed_ms = 0
        self.frames = 0
        self.demo = None
        self.demo_reason = None
        self.has_live_state = False
        self.startup_timeout_ms = int(startup_demo_timeout_seconds * 1000)
        self.button_raw = False
        self.button_stable = False
        self.button_changed_ms = 0

    def start_demo(self, reason, now_elapsed):
        if self.demo is not None:
            return
        from pumpkinpi_core.demo import AnimationDemo
        self.demo = AnimationDemo(self.controller, now_elapsed)
        self.demo_reason = reason
        close = getattr(self.transport, "close", None)
        if close is not None:
            close()
        print("Animation demo: " + reason + "; reset or power-cycle to return to live mode")

    def _button(self, now_elapsed):
        read = getattr(self.hardware, "button_a_pressed", None)
        pressed = bool(read()) if read is not None else False
        if pressed != self.button_raw:
            self.button_raw = pressed
            self.button_changed_ms = self.elapsed_ms
        if self.elapsed_ms - self.button_changed_ms >= 50 and pressed != self.button_stable:
            self.button_stable = pressed
            if pressed:
                self.start_demo("button A", now_elapsed)

    def tick(self):
        now_ms = self.clock.ticks_ms()
        elapsed = self.clock.ticks_diff(now_ms, self.last_tick)
        if elapsed >= 0:
            self.elapsed_ms += elapsed
        self.last_tick = now_ms
        now_elapsed = self.elapsed_ms / 1000.0
        self._button(now_elapsed)
        if self.demo is None:
            self._poll(now_ms, now_elapsed)
            if not self.has_live_state and self.elapsed_ms >= self.startup_timeout_ms:
                self.start_demo("startup connection timeout", now_elapsed)
        if self.clock.ticks_diff(now_ms, self.next_frame) >= 0:
            if self.demo is not None:
                frame = self.demo.frame(now_elapsed)
            else:
                frame = self.controller.frame(self.clock.utc(), now_elapsed)
            self.hardware.write(frame)
            self.frames += 1
            self.next_frame = self.clock.ticks_add(self.next_frame, self.FRAME_MS)
            if self.clock.ticks_diff(now_ms, self.next_frame) > self.FRAME_MS * 3:
                self.next_frame = self.clock.ticks_add(now_ms, self.FRAME_MS)
            self._status()

    def _poll(self, now_ms, now_elapsed):
        try:
            incoming = self.transport.service(
                now_ms, self.clock.ticks_diff)
        except Exception as exc:
            self.controller.reducer.record_error(exc)
            incoming = []
        if getattr(self.transport, "error", None):
            self.controller.reducer.record_error(self.transport.error)
        for snapshot in incoming:
            try:
                rebaseline = snapshot.pop("_stream_rebaseline", False)
                if rebaseline:
                    self.controller.reducer.authorize_reconnect(
                        snapshot.get("stream_id"))
                self.controller.on_snapshot(
                    snapshot, self.clock.utc(), now_elapsed)
                state = self.controller.reducer.state(self.clock.utc())
                if state.get("known") and not state.get("stale"):
                    self.has_live_state = True
            except Exception as exc:
                self.controller.reducer.record_error(exc)

    def _status(self):
        if self.demo is not None:
            self.hardware.status("demo")
            return
        state = self.controller.reducer.state(self.clock.utc())
        if state.get("error"):
            name = "error"
        elif not state.get("known"):
            name = "unknown"
        elif state.get("stale"):
            name = "stale"
        elif state.get("confirmed_zero"):
            name = "depleted"
        elif state.get("quality") == "estimated":
            name = "estimated"
        else:
            name = "ok"
        self.hardware.status(name)
