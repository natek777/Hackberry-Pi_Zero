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

## Radxa A7Z documentation

For board-specific documentation, pinouts, and OS images, visit the [Radxa Cubie A7Z docs page](https://docs.radxa.com/en/cubie/a7z).
