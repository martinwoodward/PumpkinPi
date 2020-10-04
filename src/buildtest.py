#!/usr/bin/env python

import requests

# Build Badge for the build you want to monitor
badgeLink = "https://github.com/martinwoodward/calculator/workflows/CI/badge.svg?branch=main"

r = requests.get(badgeLink)

print(r.text)
