"""Plasma 2350 W hardware adapter. This is the only module importing board APIs."""


class PlasmaHardware:
    def __init__(self, pixels=96, color_order="GRB", strip=None, status_led=None,
                 button_a=None):
        self.pixels = pixels
        real_hardware = strip is None
        if strip is None:
            import plasma
            order = getattr(plasma, "COLOR_ORDER_" + color_order)
            strip = plasma.WS2812(pixels, color_order=order)
        self.strip = strip
        self.status_led = status_led
        if real_hardware and self.status_led is None:
            self.status_led = self._make_status_led()
        if real_hardware and button_a is None:
            from machine import Pin
            button_a = Pin("SW_A", Pin.IN, Pin.PULL_UP)
        self.button_a = button_a
        self.black()
        self.strip.start(60)

    def _make_status_led(self):
        from pimoroni import RGBLED
        return RGBLED("LED_R", "LED_G", "LED_B")

    def write(self, frame):
        if len(frame) != self.pixels:
            raise ValueError("frame length mismatch")
        for index, rgb in enumerate(frame):
            self.strip.set_rgb(index, rgb[0], rgb[1], rgb[2])

    def black(self):
        for index in range(self.pixels):
            self.strip.set_rgb(index, 0, 0, 0)

    def button_a_pressed(self):
        return self.button_a is not None and self.button_a.value() == 0

    def status(self, state):
        colors = {
            "ok": (0, 40, 0),
            "estimated": (50, 20, 0),
            "unknown": (40, 15, 0),
            "stale": (35, 0, 20),
            "error": (50, 0, 0),
            "depleted": (0, 0, 0),
            "demo": (0, 0, 40),
        }
        if self.status_led is not None:
            self.status_led.set_rgb(*colors.get(state, colors["error"]))
