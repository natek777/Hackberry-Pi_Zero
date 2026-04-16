# This page will tell you the ways to install the display driver in different operating system and some using tips of the display  
### First you need to install the operating system you need into RaspberryPi, this is the [tutorial](https://www.raspberrypi.com/documentation/computers/getting-started.html#installing-the-operating-system), when you choose device, select Raspberry Pi Zero 2W.  
### There is a mini-HDMI port on the top side of HackberryPi. You can connect it with another screen. You can use the following commands to disable/enable the displaying.

```sh
vcgencmd display_power 0
vcgencmd display_power 1
```
### The default backlight brightness of the display after you turn HackberryPi on would be 50%. There is a physical button of the top side of HackberryPi. You can single press it to toggle the backlight of the display. You can also adjust the backlight brightness by long pressing the button: If you first long press the button the backlight brightness will slowly increase to 100%, if you now release the button and long press the button again, the backlight brightness of the display will slowly drop to 10%.  

## Display: ST7798 3.5" 320×480 SPI TFT Touchscreen

The HackberryPi uses an **ST7798**-based 3.5" TFT display (320×480 pixels) connected over SPI. The touch layer is handled by an **XPT2046** resistive touch controller on a second SPI chip-select.

### Pin connections (40-pin GPIO header)

| Display pin | RPi GPIO (BCM) | Physical pin |
|---|---|---|
| VCC | 3.3 V | Pin 1 |
| GND | GND | Pin 6 |
| SCL / SCK | GPIO 11 (SPI0 SCLK) | Pin 23 |
| SDA / MOSI | GPIO 10 (SPI0 MOSI) | Pin 19 |
| CS (display) | GPIO 8 (SPI0 CE0) | Pin 24 |
| DC / RS | GPIO 25 | Pin 22 |
| RST | GPIO 27 | Pin 13 |
| BL (backlight) | GPIO 18 (or 3.3 V) | Pin 12 |
| T_CS (touch CS) | GPIO 7 (SPI0 CE1) | Pin 26 |
| T_IRQ (touch IRQ) | GPIO 17 | Pin 11 |

> Adjust GPIO numbers if your wiring differs.

---

# Raspberry Pi OS  

### Step 1 – Enable SPI and load the ST7798 driver  
Add the following lines to `/boot/config.txt` (or `/boot/firmware/config.txt` on newer images):

```sh
dtparam=spi=on

# ST7798 display (compatible with the st7796s fbtft driver)
dtoverlay=fbtft,spi0-0-speed=32000000,width=320,height=480,bgr,rotate=270,reset_pin=27,dc_pin=25,led_pin=18

# XPT2046 resistive touchscreen
dtoverlay=ads7846,cs=1,speed=2000000,penirq=17,penirq_pull=2,swapxy=1,pmax=255,xmin=200,xmax=3900,ymin=200,ymax=3900,x_plate_ohms=60
```

> **Tip:** If the `fbtft` overlay name is not found, check available overlays with `ls /boot/overlays/` and look for `st7796s` or `flexfb`. Substitute as needed.

### Step 2 – Reboot  
Insert the TF card, turn on HackberryPi, and wait for the boot sequence to complete. The display will appear as `/dev/fb1`.

### Step 3 – Route console output to the display  
```sh
sudo con2fbmap 1 1
```
To make this permanent, add `fbcon=map:10` to the `extraargs` / `cmdline.txt` kernel parameters.

### Step 4 – Calibrate the touchscreen  
```sh
sudo apt-get install xinput-calibrator
DISPLAY=:0.0 xinput_calibrator
```
Follow the on-screen prompts and save the output to `/etc/X11/xorg.conf.d/99-calibration.conf`.

---

# Kali Linux  

### Step 1  
Add the following lines to `/boot/config.txt` in your TF card:

```sh
dtparam=spi=on
dtoverlay=fbtft,spi0-0-speed=32000000,width=320,height=480,bgr,rotate=270,reset_pin=27,dc_pin=25,led_pin=18
dtoverlay=ads7846,cs=1,speed=2000000,penirq=17,penirq_pull=2,swapxy=1,pmax=255,xmin=200,xmax=3900,ymin=200,ymax=3900,x_plate_ohms=60
```

### Step 2  
Reboot. The display should be available on `/dev/fb1`. Route the console:

```sh
sudo con2fbmap 1 1
```

### Step 3 – Calibrate touch  
```sh
sudo apt-get install xinput-calibrator
DISPLAY=:0.0 xinput_calibrator
```

---

# RetroPi OS  

### Step 1  
Add the following lines to `/boot/config.txt` in your TF card:

```sh
dtparam=spi=on
dtoverlay=fbtft,spi0-0-speed=32000000,width=320,height=480,bgr,rotate=270,reset_pin=27,dc_pin=25,led_pin=18
dtoverlay=ads7846,cs=1,speed=2000000,penirq=17,penirq_pull=2,swapxy=1,pmax=255,xmin=200,xmax=3900,ymin=200,ymax=3900,x_plate_ohms=60
```

### Step 2  
In `/etc/emulationstation/es_systems.cfg` (or the equivalent Retropie config), ensure the framebuffer is pointed to `/dev/fb1`:

```sh
export SDL_FBDEV=/dev/fb1
```

Reboot. Emulation Station should launch on the ST7798 display.

---

# Radxa Cubie A7Z  
The Radxa Cubie A7Z runs Debian Linux and uses a different boot configuration from Raspberry Pi OS. Instead of `config.txt`, display settings are managed via `/boot/armbianEnv.txt`.

### Step 1 – Flash the Radxa A7Z Debian image
Download the official Debian image from the [Radxa A7Z download page](https://docs.radxa.com/en/cubie/a7z/getting-started/download) and flash it to a microSD card using [Balena Etcher](https://www.balena.io/etcher/) or Raspberry Pi Imager.

### Step 2 – Enable SPI and the ST7798 display overlay
Mount the microSD card on your PC (or SSH into the board after first boot) and open `/boot/armbianEnv.txt`:

```sh
sudo nano /boot/armbianEnv.txt
```

Add the following lines:

```sh
overlays=spi-display
param_disp_spi_bus=0
param_disp_spi_cs=0
param_disp_spi_speed=32000000
param_disp_driver=st7796s
param_disp_rotate=270
param_disp_dc=25
param_disp_reset=27
param_disp_width=320
param_disp_height=480
param_disp_bgr=1
```

> **Note:** The ST7798 is electrically compatible with the ST7796S driver. If the `spi-display` overlay is not available in your image, check `/boot/overlays/` for an equivalent SPI LCD overlay. Consult the [Radxa documentation](https://docs.radxa.com/en/cubie/a7z) or the community [Discord channel](https://discord.gg/WzPthAmMbP) for the latest confirmed working overlay for this board.

### Step 3 – Enable XPT2046 touch controller
In the same `/boot/armbianEnv.txt`, also add:

```sh
param_touch_spi_bus=0
param_touch_spi_cs=1
param_touch_speed=2000000
param_touch_irq=17
```

### Step 4 – Reboot
Insert the microSD card, power on the HackberryPi, and the display should be active after the boot sequence completes.

# DietPi
DietPi is an extremely lightweight Debian OS, highly optimised for minimal CPU and RAM resource usage. The instruction is made by [Bjoern Franck](https://github.com/bjoernfranck)  
You can view the tutorial at this [page](https://github.com/bjoernfranck/HackberryPi/tree/main/DietPi)  
![image](https://github.com/user-attachments/assets/31e83c06-085c-4b7a-b38f-0236433038fb)

# Troubleshoot  
![image](https://github.com/user-attachments/assets/72e6eebb-46fd-4443-887c-c18b7fc35222)  
You might found your display flickering or flashing like this on the picture. This happens when the display driver is powered but no image signal coming through.  
It can usually happen when you flash a new SD card or wake up from screen blanking.  
The display can recover itsself when you put the device running like 1 hour, but you need to disable screen blanking first.  
Here are steps you need to do to disable screen blanking:  
[On RaspberryPi OS](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/configuration/screensaver.adoc)  
[On Kali](https://superuser.com/questions/1185747/how-do-i-disable-the-screensaver-lock-in-kali-linux)  

