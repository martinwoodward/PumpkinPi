#!/usr/bin/env python

import requests

# Azure Pipelines Build Badge for the build you want to monitor
badgeLink = "https://dev.azure.com/martin/FabrikamApps/_apis/build/status/HelloWorld?branchName=master"

r = requests.get(badgeLink)

print(r.text)
