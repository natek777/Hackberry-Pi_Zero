# Meshtastic Heltec Wireless Tracker V3 Integration

Integrate a **Heltec Wireless Tracker V3** running Meshtastic firmware with your HackberryPi Zero to add:

- 📡 LoRa mesh radio (SX1262, 868/915 MHz)
- 🛰️ GPS positioning (u-blox MAX-M10S, multi-constellation)
- 🕐 Accurate GPS-based system time (no internet required)
- 🔋 Heltec battery telemetry
- 💬 Two-way LoRa mesh messaging

---

## 1. Hardware Connection

### Recommended: USB-C → USB-A (simplest)

Plug the Heltec into any of the three USB-A ports on your HackberryPi using a USB-C to USB-A cable.

The Pi will detect it as `/dev/ttyACM0` (ESP32-S3 native USB) or `/dev/ttyUSB0`. Check with:

```sh
ls /dev/ttyACM* /dev/ttyUSB*
```

### Alternative: Direct GPIO UART (saves a USB port)

| Heltec pin | HackberryPi GPIO | Pi physical pin |
|------------|-----------------|-----------------|
| TX         | GPIO15 (UART RX) | Pin 10          |
| RX         | GPIO14 (UART TX) | Pin 8           |
| GND        | GND              | Pin 6           |

Both sides are 3.3 V — **no level shifter needed**.

Before using hardware UART, disable the serial console:

```sh
sudo raspi-config
# Interface Options → Serial Port
#   "Would you like a login shell to be accessible over serial?" → No
#   "Would you like the serial port hardware to be enabled?"    → Yes
```

> **Note:** The HackberryPi's I2C port uses GPIO10/GPIO11 for the display. GPIO14/GPIO15 (UART) are free.

### Battery considerations

- The Heltec has a JST-PH 2.0 mm LiPo connector with an onboard 500 mA charger.
- When powered via USB from the HackberryPi, the Heltec draws from your Nokia BL-5C cells.
- To power the Heltec independently, connect a small 400–1000 mAh LiPo to its JST port and use a **data-only USB cable** (VBUS wire removed) for serial communication.

---

## 2. Software Installation

```sh
sudo apt update
sudo apt install python3-pip gpsd gpsd-clients chrony socat
pip3 install meshtastic
```

Verify connectivity:

```sh
meshtastic --port /dev/ttyACM0 --info
```

---

## 3. GPS Integration

### Option A: Native NMEA passthrough (if supported by your firmware build)

Enable **Serial Module** in Meshtastic with **NMEA** output mode, then point `gpsd` at the port directly:

```sh
sudo gpsd /dev/ttyACM0 -F /var/run/gpsd.sock
cgps -s   # verify fix
```

### Option B: meshtastic-python NMEA bridge (universal — works with any firmware)

This approach uses the `meshtastic_gps_bridge.py` script included in this folder. It:

1. Connects to the Heltec over serial using `meshtastic-python`.
2. Subscribes to position packets.
3. Converts each position to `$GPGGA` and `$GPRMC` NMEA sentences.
4. Writes the sentences to a virtual serial port consumed by `gpsd`.

**Quick start:**

```sh
# Create the virtual serial pair
socat PTY,raw,echo=0,link=/tmp/gps0 PTY,raw,echo=0,link=/tmp/gps1 &

# Start gpsd on one end
sudo gpsd /tmp/gps1 -F /var/run/gpsd.sock

# Start the bridge on the other end (replace port if needed)
python3 meshtastic_gps_bridge.py --port /dev/ttyACM0 --pty /tmp/gps0
```

See [§6 Systemd Services](#6-systemd-services) to run this automatically on boot.

---

## 4. System Time from GPS

With `gpsd` running, configure `chrony` to use the GPS as a stratum-1 reference clock.

Edit `/etc/chrony/chrony.conf` and add:

```
refclock SHM 0 offset 0.5 delay 0.2 refid NMEA
```

Restart and verify:

```sh
sudo systemctl restart chrony
chronyc sources -v
```

You should see an `NMEA` row. The HackberryPi will now keep accurate time in the field without any internet connection.

---

## 5. Terminal Dashboard

`meshtastic_monitor.py` is a `curses`-based terminal dashboard that shows:

- Current GPS fix, latitude/longitude/altitude
- GPS-sourced system time (UTC)
- Heltec battery % and voltage
- Mesh node count
- Incoming Meshtastic text messages (last 10)

```sh
python3 meshtastic_monitor.py --port /dev/ttyACM0
```

It fits comfortably in a standard 80×24 terminal on the HackberryPi's display.

---

## 6. Systemd Services

Copy the two service files to make everything start on boot:

```sh
sudo cp meshtastic-gps-bridge.service /etc/systemd/system/
sudo cp meshtastic-monitor.service    /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now meshtastic-gps-bridge
sudo systemctl enable --now meshtastic-monitor
```

Check status:

```sh
sudo systemctl status meshtastic-gps-bridge
sudo systemctl status meshtastic-monitor
journalctl -u meshtastic-gps-bridge -f
```

> Edit the `ExecStart=` lines in the service files if your serial port is not `/dev/ttyACM0`.

---

## 7. LoRa Mesh Messaging

Once connected you can use the full Meshtastic CLI or Python API:

```sh
# Send a message to all nodes on the default channel
meshtastic --port /dev/ttyACM0 --sendtext "hello from HackberryPi"

# Configure a named channel
meshtastic --port /dev/ttyACM0 --ch-set name FieldOps --ch-index 0
```

The `meshtastic_monitor.py` dashboard displays incoming messages in real time. Press **q** to quit.

---

## 8. Optional Enhancements

| Enhancement | How |
|---|---|
| Offline maps | Install `Viking` with cached OpenStreetMap tiles |
| Keyboard messaging | Use `meshtastic_monitor.py` input bar with the BlackBerry keyboard |
| NMEA output in firmware | Enable **Serial Module → NMEA** in the Meshtastic app or web UI |
| Power isolation | Add an inline USB power switch so the Heltec can be turned off without unplugging |
| Multiple channels | Add `--ch-index 1` (etc.) to the CLI or use `iface.sendText(msg, channelIndex=1)` |

---

## Troubleshooting

**`meshtastic --info` hangs or fails**
- Make sure no other process (e.g. `gpsd`) holds the port open.
- Try `--port /dev/ttyUSB0` if `/dev/ttyACM0` does not exist.
- Add your user to the `dialout` group: `sudo usermod -aG dialout $USER` then log out/in.

**`gpsd` shows no fix**
- Run `cgps -s` or `gpsmon` and wait up to 90 seconds for a cold-start fix.
- Ensure the Heltec has a clear view of the sky.

**`chronyc sources` does not show NMEA**
- Confirm `gpsd` is running: `systemctl status gpsd`.
- Confirm `chrony` has the `refclock SHM 0` line and was restarted after editing.
