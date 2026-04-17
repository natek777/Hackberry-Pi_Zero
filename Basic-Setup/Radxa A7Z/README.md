### Basic setup tips for HackberryPi with the Radxa Cubie A7Z running Debian Linux

## First boot

After flashing the Radxa A7Z Debian image and booting for the first time, update the package list and upgrade all packages:

```sh
sudo apt update && sudo apt upgrade
```

## Increase swap size

The Radxa A7Z ships with more RAM than the RPi Zero 2W, but increasing the swap space is still recommended for heavy workloads:

```sh
sudo nano /etc/dphys-swapfile
```

Set `CONF_SWAPSIZE=2048` (2 GB), then apply the change:

```sh
sudo dphys-swapfile swapoff
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

## Enable USB 3.1 host mode

The A7Z's USB-C 3.1 port can act as a USB 3.1 host. On most Debian images it works out of the box. To verify that the port is in host mode and that USB 3 devices are detected:

```sh
lsusb -t
sudo dmesg | grep -i usb3
```

If the port is in OTG/device mode, enable host mode via the device-tree overlay:

```sh
sudo nano /boot/armbianEnv.txt
```

Add (or uncomment) the line:

```
overlays=usb-host
```

Then reboot:

```sh
sudo reboot
```

## Check USB transfer speed

To verify USB 3 speeds with a connected drive:

```sh
sudo hdparm -t /dev/sda
```

You should see read speeds well above 100 MB/s for a USB 3-capable device.

## ST7798 SPI display on Radxa A7Z

The HackberryPi uses a 3.5" ST7798 SPI TFT touchscreen. On Debian for the Radxa A7Z, the display is configured in `/boot/armbianEnv.txt`. See the [Screen setup page](https://github.com/ZitaoTech/Hackberry-Pi_Zero/tree/main/Screen) for the full configuration.

To verify the display framebuffer is active after setup:

```sh
ls /dev/fb*
sudo dmesg | grep -i spi
```

If the framebuffer device `/dev/fb1` is present, route the console to it:

```sh
sudo con2fbmap 1 1
```

To calibrate the XPT2046 touch layer:

```sh
sudo apt-get install xinput-calibrator evtest
DISPLAY=:0.0 xinput_calibrator
```

## Radxa A7Z documentation

For board-specific documentation, pinouts, and OS images, visit the [Radxa Cubie A7Z docs page](https://docs.radxa.com/en/cubie/a7z).
