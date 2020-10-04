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

# Load settings (see local_settings.sample)
try:
    from local_settings import *
except ImportError:
    pass

# Configure the unicorn hat
unicorn.set_layout(unicorn.AUTO)
unicorn.rotation(0)
unicorn.brightness(1)
width,height=unicorn.get_shape()

class BuildStatus(Enum):
  SUCCEEDED = 1
  PARTIALLY_SUCCESSFUL = 2
  FAILED = 3
  UNKNOWN = 4

def getBuildStatus(buildBadge):
  """ 
    Quickly parse the SVG returned by the build badge looking for a Failed or Partially
    Successful output or return green (assuming a good build). Note that different build
    systems use different text for the various states.
  """
  r = requests.get(buildBadge)
  if r.status_code != 200:
    return BuildStatus.UNKNOWN
  if "failing" in r.text:
    return BuildStatus.FAILED
  if "failed" in r.text:
    return BuildStatus.FAILED
  if "partially" in r.text:
    return BuildStatus.PARTIALLY_SUCCESSFUL
  if "succeeded" in r.text:
    return BuildStatus.SUCCEEDED
  if "passing" in r.text:
    return BuildStatus.SUCCEEDED
  return BuildStatus.UNKNOWN

def flicker(hue, stop):
  """
    Make the light flicker around the passed hue until told to stop.
  """
  while True:
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


# Get the initial build status
buildStatus = getBuildStatus(BADGE_LINK)

# loop forever
while True:
  stop_thread = False
  if buildStatus == BuildStatus.SUCCEEDED:
    # Good build - flicker like a candle
    workerThread = threading.Thread(target=flicker, args=(0.0, lambda: stop_thread))
  elif buildStatus == BuildStatus.PARTIALLY_SUCCESSFUL:
    # Partially successful - still flicker but with a yellow flame
    workerThread = threading.Thread(target=flicker, args=(0.1, lambda: stop_thread))
  else:
    # Build not happy or code not - flash bright white
    workerThread = threading.Thread(target=flash, args=(255,255,255,1, lambda: stop_thread))
  
  # Start the light doing something and wait a bit
  workerThread.start()
  time.sleep(REFRESH_INTERVAL)

  # Go check the status again
  buildStatus = getBuildStatus(BADGE_LINK)

  # Stop the light doing it's thing and repeat
  stop_thread = True
  workerThread.join()

