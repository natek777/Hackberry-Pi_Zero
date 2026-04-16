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
  python3 meshtastic_monitor.py --port /dev/ttyACM0 [--i2c-bus 1] [--cardkb-addr 0x5F]

Keys (M5Stack CardKB or any connected terminal)
----
  q / ESC     quit
  Backspace   delete last character in the input bar
  Enter       send the typed message to the mesh

CardKB wiring (connects to the Radxa A7Z / Pi Zero 2W compute board)
---------------------------------------------------------------------
  The HackberryPi's STEMMA QT port uses the *alternate* I2C pins:
    SDA → GPIO10 (Pi physical pin 19)
    SCL → GPIO11 (Pi physical pin 23)
    3.3 V / GND from any convenient header pin
  The I2C bus appears as /dev/i2c-11; symlink it to i2c-1 first:
    sudo ln -s /dev/i2c-11 /dev/i2c-1
  The CardKB sits at I2C address 0x5F on bus 1 (after the symlink).

Dependencies
------------
  pip install meshtastic smbus2
"""

import argparse
import curses
import datetime
import logging
import queue
import threading
import time

try:
    import smbus2
    _SMBUS2_AVAILABLE = True
except ImportError:
    _SMBUS2_AVAILABLE = False

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

# M5Stack CardKB default I2C address
CARDKB_DEFAULT_ADDR = 0x5F
# CardKB returns 0x00 when no key is pressed
CARDKB_NO_KEY = 0x00


# ---------------------------------------------------------------------------
# M5Stack CardKB reader (background thread)
# ---------------------------------------------------------------------------

class CardKBReader:
    """Poll the M5Stack CardKB over I2C and push keycodes into *key_queue*.

    The CardKB sits at I2C address 0x5F on bus 1 by default.  A single-byte
    read from the device returns the ASCII keycode of the pressed key, or
    0x00 when no key is held.  Special keycodes used here:
      0x08 / 0x7F — Backspace / Delete
      0x0D        — Enter / Return
      0x1B        — Escape
    All other printable ASCII bytes are forwarded as-is.
    """

    def __init__(self, i2c_bus: int, addr: int, key_queue: queue.Queue):
        self._bus_num = i2c_bus
        self._addr = addr
        self._queue = key_queue
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="cardkb")

    def start(self) -> bool:
        """Open the I2C bus and start polling.  Returns False if unavailable."""
        if not _SMBUS2_AVAILABLE:
            log.warning("smbus2 not installed — CardKB disabled. Run: pip3 install smbus2")
            return False
        try:
            self._bus = smbus2.SMBus(self._bus_num)
        except Exception as exc:
            log.warning("Cannot open I2C bus %d: %s — CardKB disabled.", self._bus_num, exc)
            return False
        self._thread.start()
        log.info("CardKB reader started on I2C bus %d addr 0x%02X", self._bus_num, self._addr)
        return True

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                key = self._bus.read_byte(self._addr)
                if key != CARDKB_NO_KEY:
                    self._queue.put(key)
            except Exception as exc:
                log.debug("CardKB read error: %s", exc)
            time.sleep(0.03)  # ~30 ms poll interval is comfortable for typing


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
        _safe_addstr(stdscr, h - 1, 0, " [q/ESC] quit   [type + Enter] send  (CardKB or terminal)", 0)

    stdscr.refresh()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _process_key(ch, key, input_buf: str, iface, state: State) -> tuple[str, bool]:
    """Handle a single keypress.  Returns (updated_input_buf, should_quit)."""
    if ch in ("q", "Q", "\x1b"):  # q, Q, or ESC
        return input_buf, True
    if ch in ("\n", "\r"):
        msg = input_buf.strip()
        if msg:
            try:
                iface.sendText(msg)
                state.add_message(
                    f"[{datetime.datetime.now().strftime('%H:%M:%S')}] (me): {msg}"
                )
                log.info("Sent message: %s", msg)
            except Exception as exc:
                log.warning("Failed to send message: %s", exc)
        return "", False
    if key == curses.KEY_BACKSPACE or ch in ("\x7f", "\x08"):
        return input_buf[:-1], False
    if ch and ch.isprintable():
        return input_buf + ch, False
    return input_buf, False


def run_monitor(stdscr, iface, state: State, cardkb_queue: queue.Queue) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)

    input_buf = ""

    while True:
        snap = state.snapshot()
        draw(stdscr, snap, input_buf)

        # --- drain CardKB queue first (hardware keyboard has priority) ---
        while not cardkb_queue.empty():
            raw = cardkb_queue.get_nowait()
            ch = chr(raw) if 0 < raw < 256 else None
            input_buf, quit_flag = _process_key(ch, raw, input_buf, iface, state)
            if quit_flag:
                return

        # --- then check terminal keyboard ---
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

        input_buf, quit_flag = _process_key(ch, key, input_buf, iface, state)
        if quit_flag:
            return


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
    parser.add_argument(
        "--i2c-bus",
        type=int,
        default=1,
        metavar="BUS",
        help="I2C bus number for the M5Stack CardKB (default: 1)",
    )
    parser.add_argument(
        "--cardkb-addr",
        type=lambda x: int(x, 0),
        default=CARDKB_DEFAULT_ADDR,
        metavar="ADDR",
        help=f"I2C address of the CardKB in hex or decimal (default: 0x{CARDKB_DEFAULT_ADDR:02X})",
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

    # Start CardKB I2C reader
    cardkb_queue: queue.Queue = queue.Queue()
    cardkb = CardKBReader(i2c_bus=args.i2c_bus, addr=args.cardkb_addr, key_queue=cardkb_queue)
    cardkb.start()

    try:
        curses.wrapper(run_monitor, iface, state, cardkb_queue)
    finally:
        cardkb.stop()
        iface.close()
        log.info("Disconnected.")


if __name__ == "__main__":
    main()
