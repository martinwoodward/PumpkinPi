#!/usr/bin/env python

# Quick flame test based on the Unicorn pHat example code:
# https://github.com/pimoroni/unicorn-hat/blob/master/examples/random_blinky.py

import colorsys
import time
from sys import exit

try:
    import numpy
except ImportError:
    exit("This script requires the numpy module\nInstall with: sudo pip install numpy")

import unicornhat as unicorn

def flame(hue, duration):
    print("flame: ", hue)
    sleepTime = 0.05
    for i in range(0, int(duration/sleepTime)):
        rand_mat = numpy.random.rand(width,height)
        for y in range(height):
            for x in range(width):
                h = hue + (0.1 * rand_mat[x, y])
                s = 0.8
                v = rand_mat[x, y]
                rgb = colorsys.hsv_to_rgb(h, s, v)
                r = int(rgb[0]*255.0)
                g = int(rgb[1]*255.0)
                b = int(rgb[2]*255.0)
                unicorn.set_pixel(x, y, r, g, b)
        unicorn.show()
        time.sleep(sleepTime)

print("Flame color test")

unicorn.set_layout(unicorn.AUTO)
unicorn.rotation(0)
unicorn.brightness(0.5)
width,height=unicorn.get_shape()

flame(0.0, 2)
flame(0.1, 2)
flame(0.2, 2)
flame(0.4, 2)
flame(0.8, 2)


