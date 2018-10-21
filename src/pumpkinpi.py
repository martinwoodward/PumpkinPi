import requests

# Azure Pipelines Build Badge for the build you want to monitor
badgeLink = "https://dev.azure.com/martin/FabrikamApps/_apis/build/status/HelloWorld?branchName=master"


def buildHue(buildBadge):
  # This is _slightly_ hacky as there as proper REST API's for Azure Pipelines, but
  # quickly parse the SVG returned by the build badge looking for a Failed or Partially
  # Successful output or return green (assuming a good build).
  r = requests.get(buildBadge)
  if r.status_code != 200:
    return 0    # red
  if "failed" in r:
    return 0.1  # orange/red
  if "partially" in r:
    return 0.2  # yellow
  return 0.5    # green


print(buildHue(badgeLink))