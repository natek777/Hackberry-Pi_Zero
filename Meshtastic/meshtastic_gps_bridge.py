#!/usr/bin/env python3
"""
meshtastic_gps_bridge.py
========================
Reads position packets from a Meshtastic device (Heltec Wireless Tracker V3
or any Meshtastic node with GPS) and writes standard NMEA 0183 sentences
($GPGGA and $GPRMC) to a virtual serial port so that gpsd can consume them.

Usage
-----
  # 1. Create a virtual serial pair (run once, or put in a service):
  #    socat PTY,raw,echo=0,link=/tmp/gps0 PTY,raw,echo=0,link=/tmp/gps1 &
  #    sudo gpsd /tmp/gps1 -F /var/run/gpsd.sock
  #
  # 2. Run this bridge:
  #    python3 meshtastic_gps_bridge.py --port /dev/ttyACM0 --pty /tmp/gps0

Dependencies
------------
  pip install meshtastic
"""

import argparse
import datetime
import logging
import os
import sys
import time

try:
    import meshtastic
    import meshtastic.serial_interface
    from pubsub import pub
except ImportError:
    sys.exit(
        "ERROR: meshtastic library not found.\n"
        "Install it with:  pip3 install meshtastic"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NMEA helpers
# ---------------------------------------------------------------------------

def _nmea_checksum(sentence: str) -> str:
    """Return the two-hex-digit NMEA checksum for *sentence* (no $ or *)."""
    checksum = 0
    for ch in sentence:
        checksum ^= ord(ch)
    return f"{checksum:02X}"


def _nmea_wrap(sentence: str) -> str:
    """Wrap a bare NMEA sentence with $ prefix, * checksum, and CRLF."""
    return f"${sentence}*{_nmea_checksum(sentence)}\r\n"


def _dd_to_nmea(degrees: float, is_lat: bool) -> tuple[str, str]:
    """Convert decimal degrees to NMEA ddmm.mmmm / dddmm.mmmm + hemisphere."""
    hemi_pos, hemi_neg = ("N", "S") if is_lat else ("E", "W")
    hemi = hemi_pos if degrees >= 0 else hemi_neg
    degrees = abs(degrees)
    deg = int(degrees)
    minutes = (degrees - deg) * 60.0
    if is_lat:
        return f"{deg:02d}{minutes:07.4f}", hemi
    return f"{deg:03d}{minutes:07.4f}", hemi


def build_gpgga(lat: float, lon: float, alt: float, utc: datetime.datetime) -> str:
    """Build a $GPGGA sentence from decimal degrees and UTC datetime."""
    time_str = utc.strftime("%H%M%S.00")
    lat_str, lat_h = _dd_to_nmea(lat, is_lat=True)
    lon_str, lon_h = _dd_to_nmea(lon, is_lat=False)
    # quality=1 (GPS fix), satellites=08, HDOP=1.0
    body = f"GPGGA,{time_str},{lat_str},{lat_h},{lon_str},{lon_h},1,08,1.0,{alt:.1f},M,0.0,M,,"
    return _nmea_wrap(body)


def build_gprmc(lat: float, lon: float, utc: datetime.datetime, speed_knots: float = 0.0, course: float = 0.0) -> str:
    """Build a $GPRMC sentence from decimal degrees and UTC datetime."""
    time_str = utc.strftime("%H%M%S.00")
    date_str = utc.strftime("%d%m%y")
    lat_str, lat_h = _dd_to_nmea(lat, is_lat=True)
    lon_str, lon_h = _dd_to_nmea(lon, is_lat=False)
    body = f"GPRMC,{time_str},A,{lat_str},{lat_h},{lon_str},{lon_h},{speed_knots:.1f},{course:.1f},{date_str},,"
    return _nmea_wrap(body)


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class MeshtasticGPSBridge:
    def __init__(self, serial_port: str, pty_path: str):
        self.serial_port = serial_port
        self.pty_path = pty_path
        self._pty_fd: int | None = None
        self._iface = None

    def _open_pty(self) -> None:
        if not os.path.exists(self.pty_path):
            log.error("PTY %s does not exist. Did you run socat?", self.pty_path)
            sys.exit(1)
        self._pty_fd = os.open(self.pty_path, os.O_WRONLY | os.O_NOCTTY)
        log.info("Opened PTY %s for writing", self.pty_path)

    def _write_nmea(self, sentences: list[str]) -> None:
        for s in sentences:
            try:
                os.write(self._pty_fd, s.encode("ascii"))
            except OSError as exc:
                log.warning("Failed to write NMEA to PTY: %s", exc)

    def _on_receive(self, packet, interface) -> None:  # noqa: ARG002
        decoded = packet.get("decoded", {})
        if decoded.get("portnum") != "POSITION_APP":
            return

        pos = decoded.get("position", {})
        lat = pos.get("latitudeI")
        lon = pos.get("longitudeI")
        if lat is None or lon is None:
            return

        lat_deg = lat / 1e7
        lon_deg = lon / 1e7
        alt = pos.get("altitude", 0) or 0

        # Use GPS time from packet if available, otherwise fall back to system UTC
        gps_time = pos.get("time")
        if gps_time:
            utc = datetime.datetime.fromtimestamp(gps_time, tz=datetime.timezone.utc)
        else:
            utc = datetime.datetime.now(tz=datetime.timezone.utc)

        speed_knots = pos.get("groundSpeed", 0) or 0
        # groundSpeed is m/s in Meshtastic protobuf; convert to knots
        speed_knots = speed_knots * 1.94384
        course = pos.get("groundTrack", 0) or 0

        sentences = [
            build_gpgga(lat_deg, lon_deg, alt, utc),
            build_gprmc(lat_deg, lon_deg, utc, speed_knots, course),
        ]
        self._write_nmea(sentences)
        log.info(
            "Position: lat=%.6f lon=%.6f alt=%.1fm  → wrote NMEA",
            lat_deg, lon_deg, alt,
        )

    def run(self) -> None:
        self._open_pty()

        log.info("Connecting to Meshtastic device on %s …", self.serial_port)
        self._iface = meshtastic.serial_interface.SerialInterface(self.serial_port)
        pub.subscribe(self._on_receive, "meshtastic.receive")

        log.info("Bridge running. Waiting for position packets (Ctrl-C to stop).")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down.")
        finally:
            if self._iface:
                self._iface.close()
            if self._pty_fd is not None:
                os.close(self._pty_fd)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bridge Meshtastic GPS positions to gpsd via a virtual serial port."
    )
    parser.add_argument(
        "--port",
        default="/dev/ttyACM0",
        help="Serial port for the Meshtastic device (default: /dev/ttyACM0)",
    )
    parser.add_argument(
        "--pty",
        default="/tmp/gps0",
        help="Path to the write-end of the socat virtual serial pair (default: /tmp/gps0)",
    )
    args = parser.parse_args()

    bridge = MeshtasticGPSBridge(serial_port=args.port, pty_path=args.pty)
    bridge.run()


if __name__ == "__main__":
    main()
