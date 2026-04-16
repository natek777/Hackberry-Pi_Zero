#!/usr/bin/env python3
"""
meshtastic_monitor.py
=====================
Curses-based terminal dashboard for the Meshtastic Heltec Wireless Tracker V3
on HackberryPi Zero.  Displays:

  - Current GPS fix (lat / lon / altitude)
  - GPS-sourced UTC time
  - Heltec battery level (%) and voltage (V)
  - Mesh node count
  - Incoming text messages (last 10)

Usage
-----
  python3 meshtastic_monitor.py --port /dev/ttyACM0

Keys
----
  q           quit
  m <text>    send a text message to the default channel (type in the input bar)

Dependencies
------------
  pip install meshtastic
"""

import argparse
import curses
import datetime
import logging
import threading
import time

try:
    import meshtastic
    import meshtastic.serial_interface
    from pubsub import pub
except ImportError:
    raise SystemExit(
        "ERROR: meshtastic library not found.\n"
        "Install it with:  pip3 install meshtastic"
    )

logging.basicConfig(
    filename="/tmp/meshtastic_monitor.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

MAX_MESSAGES = 10


# ---------------------------------------------------------------------------
# Shared state (written by callbacks, read by the display loop)
# ---------------------------------------------------------------------------

class State:
    def __init__(self):
        self._lock = threading.Lock()
        self.lat: float | None = None
        self.lon: float | None = None
        self.alt: float | None = None
        self.gps_time: datetime.datetime | None = None
        self.gps_fix: bool = False
        self.battery_pct: int | None = None
        self.battery_v: float | None = None
        self.node_count: int = 0
        self.messages: list[str] = []

    def update_position(self, lat, lon, alt, gps_ts):
        with self._lock:
            self.lat = lat
            self.lon = lon
            self.alt = alt
            self.gps_fix = True
            if gps_ts:
                self.gps_time = datetime.datetime.fromtimestamp(
                    gps_ts, tz=datetime.timezone.utc
                )

    def update_telemetry(self, battery_pct, battery_v):
        with self._lock:
            if battery_pct is not None:
                self.battery_pct = battery_pct
            if battery_v is not None:
                self.battery_v = battery_v

    def update_nodes(self, count):
        with self._lock:
            self.node_count = count

    def add_message(self, msg: str):
        with self._lock:
            self.messages.append(msg)
            if len(self.messages) > MAX_MESSAGES:
                self.messages.pop(0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "lat": self.lat,
                "lon": self.lon,
                "alt": self.alt,
                "gps_time": self.gps_time,
                "gps_fix": self.gps_fix,
                "battery_pct": self.battery_pct,
                "battery_v": self.battery_v,
                "node_count": self.node_count,
                "messages": list(self.messages),
            }


# ---------------------------------------------------------------------------
# Meshtastic callbacks
# ---------------------------------------------------------------------------

def make_callbacks(state: State):
    def on_receive(packet, interface):  # noqa: ARG001
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum", "")

        if portnum == "POSITION_APP":
            pos = decoded.get("position", {})
            lat_i = pos.get("latitudeI")
            lon_i = pos.get("longitudeI")
            if lat_i is not None and lon_i is not None:
                state.update_position(
                    lat=lat_i / 1e7,
                    lon=lon_i / 1e7,
                    alt=pos.get("altitude", 0) or 0,
                    gps_ts=pos.get("time"),
                )
                log.info("Position updated: %s %s", lat_i / 1e7, lon_i / 1e7)

        elif portnum == "TELEMETRY_APP":
            tel = decoded.get("telemetry", {})
            dm = tel.get("deviceMetrics", {})
            state.update_telemetry(
                battery_pct=dm.get("batteryLevel"),
                battery_v=dm.get("voltage"),
            )
            log.info("Telemetry: battery=%s%% %.2fV", dm.get("batteryLevel"), dm.get("voltage") or 0)

        elif portnum == "TEXT_MESSAGE_APP":
            text = decoded.get("text", "")
            sender = packet.get("fromId", "?")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            state.add_message(f"[{ts}] {sender}: {text}")
            log.info("Message from %s: %s", sender, text)

    def on_connection(interface, topic=pub.AUTO_TOPIC):  # noqa: ARG001
        try:
            node_db = interface.nodes or {}
            state.update_nodes(len(node_db))
            # Pull initial telemetry from our own node
            my_num = interface.myInfo.my_node_num if interface.myInfo else None
            if my_num:
                my_node = node_db.get(my_num) or node_db.get(str(my_num))
                if my_node:
                    dm = my_node.get("deviceMetrics", {})
                    state.update_telemetry(
                        battery_pct=dm.get("batteryLevel"),
                        battery_v=dm.get("voltage"),
                    )
        except Exception as exc:
            log.warning("Error reading initial node info: %s", exc)

    return on_receive, on_connection


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _safe_addstr(win, row, col, text, attr=0):
    h, w = win.getmaxyx()
    if row < 0 or row >= h:
        return
    available = w - col - 1
    if available <= 0:
        return
    win.addstr(row, col, text[:available], attr)


def draw(stdscr, state_snap: dict, input_buf: str) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)

    GREEN = curses.color_pair(1) | curses.A_BOLD
    YELLOW = curses.color_pair(2)
    CYAN = curses.color_pair(3)
    RED = curses.color_pair(4)
    HEADER = curses.color_pair(5) | curses.A_BOLD

    # Header bar
    title = " HackberryPi  ·  Meshtastic Monitor "
    _safe_addstr(stdscr, 0, 0, title.ljust(w - 1), HEADER)

    row = 2
    # -- GPS section --
    _safe_addstr(stdscr, row, 2, "[ GPS ]", CYAN)
    row += 1

    fix_str = "FIX" if state_snap["gps_fix"] else "NO FIX"
    fix_attr = GREEN if state_snap["gps_fix"] else RED
    _safe_addstr(stdscr, row, 4, f"Status : ", 0)
    _safe_addstr(stdscr, row, 13, fix_str, fix_attr)
    row += 1

    if state_snap["lat"] is not None:
        _safe_addstr(stdscr, row, 4, f"Lat    : {state_snap['lat']:+.6f}°", 0)
        row += 1
        _safe_addstr(stdscr, row, 4, f"Lon    : {state_snap['lon']:+.6f}°", 0)
        row += 1
        _safe_addstr(stdscr, row, 4, f"Alt    : {state_snap['alt']:.1f} m", 0)
        row += 1
    else:
        _safe_addstr(stdscr, row, 4, "Waiting for GPS position…", YELLOW)
        row += 1

    row += 1
    # -- Time section --
    _safe_addstr(stdscr, row, 2, "[ Time ]", CYAN)
    row += 1
    if state_snap["gps_time"]:
        gps_ts = state_snap["gps_time"].strftime("%Y-%m-%d  %H:%M:%S UTC  (GPS)")
        _safe_addstr(stdscr, row, 4, f"GPS    : {gps_ts}", GREEN)
    else:
        sys_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC  (system)")
        _safe_addstr(stdscr, row, 4, f"System : {sys_ts}", YELLOW)
    row += 2

    # -- Battery section --
    _safe_addstr(stdscr, row, 2, "[ Heltec Battery ]", CYAN)
    row += 1
    if state_snap["battery_pct"] is not None:
        pct = state_snap["battery_pct"]
        bat_attr = GREEN if pct > 40 else (YELLOW if pct > 15 else RED)
        v_str = f"  {state_snap['battery_v']:.2f} V" if state_snap["battery_v"] else ""
        _safe_addstr(stdscr, row, 4, f"Level  : {pct}%{v_str}", bat_attr)
    else:
        _safe_addstr(stdscr, row, 4, "Waiting for telemetry…", YELLOW)
    row += 2

    # -- Mesh section --
    _safe_addstr(stdscr, row, 2, "[ Mesh ]", CYAN)
    row += 1
    _safe_addstr(stdscr, row, 4, f"Nodes  : {state_snap['node_count']}", 0)
    row += 2

    # -- Messages section --
    msgs_start = row
    _safe_addstr(stdscr, row, 2, "[ Messages ]", CYAN)
    row += 1
    msgs_area = max(0, h - 4 - row)
    visible_msgs = state_snap["messages"][-msgs_area:] if msgs_area > 0 else []
    for msg in visible_msgs:
        _safe_addstr(stdscr, row, 4, msg, 0)
        row += 1
        if row >= h - 3:
            break

    # -- Divider + input bar --
    if h > 3:
        _safe_addstr(stdscr, h - 3, 0, "─" * (w - 1), CYAN)
        _safe_addstr(stdscr, h - 2, 0, " Send (Enter): ", 0)
        _safe_addstr(stdscr, h - 2, 15, input_buf, YELLOW)
        _safe_addstr(stdscr, h - 1, 0, " [q] quit   [type message + Enter] send", 0)

    stdscr.refresh()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_monitor(stdscr, iface, state: State) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)

    input_buf = ""

    while True:
        snap = state.snapshot()
        draw(stdscr, snap, input_buf)

        try:
            key = stdscr.get_wch()
        except curses.error:
            key = None

        if key is None:
            continue

        if isinstance(key, str):
            ch = key
        else:
            ch = chr(key) if 0 < key < 256 else None

        if ch == "q" or ch == "Q":
            break
        elif ch in ("\n", "\r"):
            msg = input_buf.strip()
            if msg:
                try:
                    iface.sendText(msg)
                    state.add_message(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] (me): {msg}")
                    log.info("Sent message: %s", msg)
                except Exception as exc:
                    log.warning("Failed to send message: %s", exc)
            input_buf = ""
        elif key == curses.KEY_BACKSPACE or ch in ("\x7f", "\x08"):
            input_buf = input_buf[:-1]
        elif ch and ch.isprintable():
            input_buf += ch


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Terminal dashboard for Meshtastic on HackberryPi Zero."
    )
    parser.add_argument(
        "--port",
        default="/dev/ttyACM0",
        help="Serial port for the Meshtastic device (default: /dev/ttyACM0)",
    )
    args = parser.parse_args()

    state = State()
    on_receive, on_connection = make_callbacks(state)
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")

    log.info("Connecting to Meshtastic on %s …", args.port)
    iface = meshtastic.serial_interface.SerialInterface(args.port)
    # Give the library a moment to populate node DB
    time.sleep(2)
    try:
        node_db = iface.nodes or {}
        state.update_nodes(len(node_db))
    except Exception:
        pass

    try:
        curses.wrapper(run_monitor, iface, state)
    finally:
        iface.close()
        log.info("Disconnected.")


if __name__ == "__main__":
    main()
