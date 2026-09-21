"""Post-composition brightness and modeled-current limiter."""


class PowerConfigError(ValueError):
    pass


class PowerLimiter:
    def __init__(self, pixels=96, max_brightness=1.0, max_milliamps=1500,
                 idle_ma_per_pixel=0.5, channel_ma=20.0):
        if pixels <= 0 or not 0 < max_brightness <= 1:
            raise PowerConfigError("invalid pixel count or brightness")
        if int(255 * max_brightness) == 0:
            raise PowerConfigError(
                "max_brightness cannot produce a visible channel safely")
        idle = pixels * idle_ma_per_pixel
        if max_milliamps <= idle:
            raise PowerConfigError("current budget does not exceed modeled idle")
        self.pixels = pixels
        self.max_brightness = max_brightness
        self.max_milliamps = max_milliamps
        self.idle_ma_per_pixel = idle_ma_per_pixel
        self.channel_ma = channel_ma

    def apply(self, frame):
        if len(frame) != self.pixels:
            raise PowerConfigError("frame length mismatch")
        brightness = self.max_brightness
        demand = self.pixels * self.idle_ma_per_pixel
        for rgb in frame:
            if len(rgb) != 3:
                raise PowerConfigError("invalid pixel")
            demand += sum(max(0, min(255, int(v))) for v in rgb) / 255.0 \
                * self.channel_ma * brightness
        scale = brightness
        active = demand - self.pixels * self.idle_ma_per_pixel
        available = self.max_milliamps - self.pixels * self.idle_ma_per_pixel
        if active > available:
            scale *= available / active
        bounded = [tuple(max(0, min(255, int(value))) for value in rgb)
                   for rgb in frame]
        output = [tuple(int(value * scale) for value in rgb)
                  for rgb in bounded]
        visible_without_current_reduction = any(
            any(int(value * brightness) > 0 for value in rgb)
            for rgb in bounded)
        if visible_without_current_reduction \
                and not any(any(value > 0 for value in rgb) for rgb in output):
            raise PowerConfigError(
                "safe power limit quantized a nonzero frame to black; "
                "increase the configured brightness/current budget")
        return output

    def estimate_milliamps(self, frame):
        return self.pixels * self.idle_ma_per_pixel + sum(
            sum(rgb) / 255.0 * self.channel_ma for rgb in frame
        )
