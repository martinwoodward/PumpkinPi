#!/usr/bin/env python

import requests

# Azure Pipelines Build Badge for the build you want to monitor
badgeLink = "https://dev.azure.com/martin/calculator/_apis/build/status/martinwoodward.calculator"

r = requests.get(badgeLink)

print(r.text)
