#!/usr/bin/env python

# Quick flame color test based on the Unicorn pHat example code:
# https://github.com/pimoroni/unicorn-hat/blob/master/examples/random_blinky.py

import colorsys
import time
from sys import exit

import numpy
import unicornhat as unicorn

def flame(hue, duration):
    """ Flicker flame effect for specified hue (0-1) and duration (in seconds)"""
    print("flame: ", hue)
    sleepTime = 0.05
    for i in range(0, int(duration/sleepTime)):
        rand_mat = numpy.random.rand(width,height)
        for y in range(height):
            for x in range(width):
                # h is the percentage of the 360 degrees of hue that we start from
                # then we add a random amount to each pixel to a max of 10%
                # so 30 h in the traditional hsv space
                h = hue + (0.1 * rand_mat[x, y])
                s = 1
                v = rand_mat[x, y]
                # Convert to RGB to send to the LED pixels
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

flame(0.9, 5)   # Red
flame(0.0, 5)   # Orange
flame(0.1, 5)   # Yellow
flame(0.2, 5)   # Green
flame(0.4, 5)   # Light Blue
flame(0.5, 5)   # Blue
flame(0.8, 5)   # Purple


