# PumpkinPi
Spooky build status indicator for [Azure Pipelines](https://azure.com/pipelines).

## Hardware Requirements:
This project makes use of the following:
 - Pumpkin
 - [Raspberry Pi Zero WH](https://www.adafruit.com/product/3708) - I'm lazy so purchased the version with the GPIO header pre-soldered.
 - [Pimori Unicorn pHat](https://www.adafruit.com/product/3181)
 - USB power (either battery pack or mains charger with micro-usb connector)

If you have a large pumpkin and space / cost isn't much of a concern then you can go with a [full sized Raspberry Pi](https://amzn.to/2q2TPyJ) and a full [Pimori Unicorn Hat](https://amzn.to/2CSVhfj)

While Amazon ([US](https://amzn.to/2CUGild)) and Amazon ([UK](https://amzn.to/2PbHDtz)) are always handy places (especially if you are a Prime member) I personally also highly recommend [AdaFruit](https://www.adafruit.com/) for electronics purchases in the US and [PiHut](https://thepihut.com/) in the UK. They basically should just park a truck at my house and take all my money.

## Getting Started

Format the micro SD Card and [install Raspian Lite](https://www.raspberrypi.org/downloads/raspbian/). If installing onto a Pi Zero you might not have the keyboard and HDMI adopters lying around so you probably want to do a [headless install](https://www.raspberrypi-spy.co.uk/2017/04/manually-setting-up-pi-wifi-using-wpa_supplicant-conf/), configuring SSH and WiFi by dropping an `ssh` file and a `wpa_supplicant.conf` file onto the root of the SD card after copying over the Raspbian files.

You'll need to install the [Unicorn HAT](https://github.com/pimoroni/unicorn-hat) software but they have a cool one-line installer that takes care of all the dependencies including Python and Git.

``\curl -sS https://get.pimoroni.com/unicornhat | bash``



