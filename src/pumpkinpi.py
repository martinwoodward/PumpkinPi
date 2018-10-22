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

# Azure Pipelines Build Badge for the build you want to monitor
BADGE_LINK = "https://dev.azure.com/martin/FabrikamApps/_apis/build/status/HelloWorld?branchName=master"
# How often to check (in seconds). Remember - be nice to the server. Once a minute is plenty.
REFRESH_INTERVAL = 10

# Configure the unicorn hat
unicorn.set_layout(unicorn.AUTO)
unicorn.rotation(0)
unicorn.brightness(1)
width,height=unicorn.get_shape()

class BuildStatus(Enum):
  SUCCEEDED = 1
  PARTIALLY_SUCCESSFUL = 2
  FAILED = 3
  UNKOWN = 4

def getBuildStatus(buildBadge):
  """ 
    This is _very_ hacky as there are proper REST API's for Azure Pipelines, but
    quickly parse the SVG returned by the build badge looking for a Failed or Partially
    Successful output or return green (assuming a good build).
  """
  r = requests.get(buildBadge)
  if r.status_code != 200:
    return BuildStatus.UNKOWN
  if "failed" in r.text:
    return BuildStatus.FAILED
  if "partially" in r.text:
    return BuildStatus.PARTIALLY_SUCCESSFUL
  if "succeeded" in r.text:
    return BuildStatus.SUCCEEDED
  return BuildStatus.UNKOWN

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
    time.sleep(0.5)
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
          lightsOn = False
        else:
          unicorn.set_pixel(x,y,0,0,0)
          lightsOn = True
    unicorn.show()
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
  elif buildStatus == BuildStatus.PARTIALLY_SUCCESSFUL
    # Partially successful - still flicker but with a yellow flame
    workerThread = threading.Thread(target=flicker, args=(0.1, lambda: stop_thread))
  else:
    # Build not happy or code not - flash bright white
    workerThread = threading.Thread(target=flash, args=(255,255,255,1, lambda: stop_thread))
  
  # Start the light doing something and wait a bit
  workerThread.start()
  time.sleep(REFRESH_INTERVAL)

  # Go check the status again
  buildStatus = buildHue(BADGE_LINK)

  # Stop the light doing it's thing and repeat
  stop_thread = True
  workerThread.join()

