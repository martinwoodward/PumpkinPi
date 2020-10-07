#!/usr/bin/env python

import requests
import threading
import time
import colorsys
import numpy
import time
from sys import exit
from enum import Enum

import unicornhat as unicorn

def flicker(hue, stop):
  """
    Make the light flicker around the passed hue until told to stop.
  """
  while True:
    rand_mat = numpy.random.rand(width,height)
    for y in range(height):
        for x in range(width):
            h = hue + (0.1 * rand_mat[x, y])
            s = 1
            v = rand_mat[x, y]
            rgb = colorsys.hsv_to_rgb(h, s, v)
            r = int(rgb[0]*255.0)
            g = int(rgb[1]*255.0)
            b = int(rgb[2]*255.0)
            unicorn.set_pixel(x, y, r, g, b)
    unicorn.show()    
    time.sleep(0.05)
    if stop():
        break

def flash(r, g, b, period, stop):
  """
    Make the light flash around the passed color until told to stop.
  """
  lightsOn = True
  while True:
    for y in range(height):
      for x in range(width):
        if lightsOn:
          unicorn.set_pixel(x,y,r,g,b)
        else:
          unicorn.set_pixel(x,y,0,0,0)
    unicorn.show()
    lightsOn = not lightsOn 
    time.sleep(period)
    if stop():
        break


  stop_thread = False
  
  workerThread = threading.Thread(target=flicker, args=(0.0, lambda: stop_thread))
  workerThread.start()
  time.sleep(5)

  workerThread = threading.Thread(target=flash, args=(255,255,255,1, lambda: stop_thread))
  workerThread.start()
  time.sleep(10)
  
  workerThread = threading.Thread(target=flicker, args=(0.0, lambda: stop_thread))
  workerThread.start()
  time.sleep(5)
  
