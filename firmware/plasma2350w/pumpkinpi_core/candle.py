"""Seeded correlated candle renderer and bounded activity effects."""

import math

PIXELS = 96
ANCHORS = (
    (1.00, (255, 170, 45)),
    (0.50, (255, 132, 72)),
    (0.20, (235, 40, 25)),
    (0.10, (180, 20, 100)),
    (0.02, (100, 12, 170)),
    (0.00, (100, 12, 170)),
)


def palette(fraction):
    if fraction is None:
        return (0, 0, 0)
    fraction = max(0.0, min(1.0, fraction))
    for index in range(len(ANCHORS) - 1):
        high_f, high_rgb = ANCHORS[index]
        low_f, low_rgb = ANCHORS[index + 1]
        if low_f <= fraction <= high_f:
            span = high_f - low_f
            mix = 0.0 if span == 0 else (fraction - low_f) / span
            return tuple(int(low_rgb[c] + (high_rgb[c] - low_rgb[c]) * mix)
                         for c in range(3))
    return ANCHORS[-1][1]


class CandleRenderer:
    def __init__(self, pixels=PIXELS, seed=2350):
        self.pixels = pixels
        self.seed = seed
        self.pulses = []

    def queue_usage(self, delta_microcredits):
        if delta_microcredits <= 0:
            return
        strength = min(1.0, math.log(1.0 + delta_microcredits / 1000000.0) / 5.0)
        if len(self.pulses) >= 3:
            self.pulses[-1][1] = max(self.pulses[-1][1], strength)
        else:
            self.pulses.append([0.0, strength])

    def clear_effects(self):
        self.pulses = []

    def render(self, fraction, elapsed, confirmed_black=False, sputter=None,
               exhausted=False, recharge=None, unlimited=False):
        if confirmed_black:
            self.clear_effects()
            return [(0, 0, 0)] * self.pixels
        base = palette(1.0 if unlimited else fraction)
        shared = 0.90 + 0.07 * math.sin(elapsed * 7.1 + self.seed)
        frame = []
        for index in range(self.pixels):
            local = 0.96 + 0.04 * math.sin(elapsed * (4.1 + (index % 5) * 0.13)
                                           + index * 1.77 + self.seed)
            scale = shared * local
            frame.append(tuple(max(0, min(255, int(channel * scale)))
                               for channel in base))
        for pulse in self.pulses:
            age, strength = pulse
            center = int((age / 1.2) * (self.pixels + 12)) - 6
            for index in range(self.pixels):
                distance = abs(index - center)
                if distance < 7:
                    boost = int(90 * strength * (1.0 - distance / 7.0)
                                * max(0.0, 1.0 - age / 1.2))
                    r, g, b = frame[index]
                    frame[index] = (min(255, r + boost), min(255, g + boost // 3), b)
            pulse[0] += 1.0 / 30.0
        self.pulses = [pulse for pulse in self.pulses if pulse[0] < 1.2]
        if exhausted:
            self.clear_effects()
            factor = .13 + .05 * math.sin(elapsed * 5.7 + self.seed)
            if sputter is not None:
                decay = max(0.0, 1.0 - sputter / 2.0)
                gate = .15 + .85 * abs(math.sin(sputter * 24.0 + self.seed))
                factor += (1.0 - factor) * decay * gate
            frame = [tuple(int(channel * factor) for channel in rgb)
                     for rgb in frame]
        elif unlimited or (fraction is not None and fraction >= 1.0):
            for index, rgb in enumerate(frame):
                phase = elapsed * 5.0 + index * .37
                slot = int(phase)
                code = (slot * 1103515245 + index * 12345 + self.seed * 2654435761) & 0xffffffff
                if code % 29 == 0:
                    sparkle = math.sin(math.pi * (phase - slot)) ** 2
                    frame[index] = tuple(int(channel + (255 - channel) * sparkle)
                                         for channel in rgb)
        if recharge is not None and not exhausted:
            if recharge <= 1.0:
                mix = max(0.0, recharge)
                # The one-second rise ends at pure white, before returning to the candle.
                dim = (12, 1, 20)
                color = tuple(int(channel + (255 - channel) * mix) for channel in dim)
                return [color] * self.pixels
            if recharge < 1.5:
                mix = (recharge - 1.0) / .5
                frame = [tuple(int(255 + (channel - 255) * mix) for channel in rgb)
                         for rgb in frame]
        return frame
