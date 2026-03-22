#!/usr/bin/env python3
"""
modbus_client.py — RBMK-1000 Modbus Training Control Client

Demonstrates PLC-style closed-loop control of the reactor simulator via
Modbus TCP. Implements:

  • PI power controller   — adjusts average rod withdrawal to hit MWe setpoint
  • Spatial flattening    — trims individual rods using T-sensor proximity
                            weights to suppress hot spots in the core
  • Coolant management    — controls each pump individually; ramps flow with
                            power, boosts on high void or temperature
  • Safety interlocks     — automatic SCRAM on dangerous conditions

Usage:
  python modbus_client.py                     # defaults: localhost:502
  python modbus_client.py --host 192.168.1.10 --port 502 --setpoint 600

Interactive keys (while running):
  UP / +    Increase power setpoint by 25 MWe
  DOWN / -  Decrease power setpoint by 25 MWe
  a         Switch to AUTO control
  m         Switch to MANUAL (suspends all outputs)
  s         Send AZ-5 SCRAM command
  q         Quit

Register map used (all 0-based, matches simulator defaults):
  INPUT REGISTERS (FC04):
    0   reactor_power %     (0–100   → 0–32767)
    1   output_mw           (0–1100  → 0–32767)
    2   steam_pressure MPa  (0–15    → 0–32767)
    3   total_flow %        (0–100   → 0–32767)
    4   void_fraction %     (0–100   → 0–32767)
    5   avg_core_temp °C    (0–500   → 0–32767)
    6   max_chan_temp °C     (0–500   → 0–32767)
    7   turbine_eff %       (0–35    → 0–32767)
    8   avg_iodine          (0–1     → 0–32767)
    9   avg_xenon           (0–1     → 0–32767)
    10  target_mw           (0–1100  → 0–32767, 0 = not in shift mode)
    100–129  T-sensors °C   (0–500   → 0–32767, 32767=fault)
    150–169  F-sensors       (0–1.5  → 0–32767, 32767=fault)
    200–229  CR positions %  (0–100  → 0–32767)
    250–253  pump speeds %   (0–100  → 0–32767)

  HOLDING REGISTERS (FC03/FC06):
    0–29     CR setpoints %  (0–32767 → 0–100%)
    100–103  pump setpoints  (0–32767 → 0–100%)

  COILS (FC05):
    0    SCRAM command
    10   MCP-1 run
    11   MCP-2 run
    12   MCP-3 run
    13   MCP-4 run

  DISCRETE INPUTS (FC02):
    0    reactor_running
    1    scram_active
    10   alarm_hipower
    11   alarm_hitemp
    12   alarm_void
    13   alarm_locool
    14   alarm_pumptrip
    15   alarm_scram
    16   alarm_damage
    17   alarm_hipress
    18   alarm_xenonpit
    30   MCP-1 running
    33   MCP-2 running
    36   MCP-3 running
    39   MCP-4 running
"""

import sys
import time
import math
import argparse
import threading
import collections
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ModbusException
except ImportError:
    print("ERROR: pymodbus not found in the current Python environment.")
    print("Please run the client via start_client.bat which sets up")
    print("a dedicated virtual environment automatically.")
    print()
    print("Or manually:  pip install pymodbus==3.6.8")
    sys.exit(1)

try:
    import curses
    HAS_CURSES = True
except ImportError:
    HAS_CURSES = False

# ── Modbus scaling helpers ─────────────────────────────────────────────────

RAW_MAX = 32767

def raw_to_eng(raw: int, lo: float, hi: float) -> float:
    return lo + (raw / RAW_MAX) * (hi - lo)

def eng_to_raw(val: float, lo: float, hi: float) -> int:
    return max(0, min(RAW_MAX, round((val - lo) / (hi - lo) * RAW_MAX)))

# ── Rod geometry (mirrors physics.py initChannels) ─────────────────────────
# 30 control rods: rows [1,4,7,10,13,16] × cols [2,6,10,14,18]
# T-sensors: rows [2,5,9,12,15] × cols [0,4,8,12,16,20]

ROD_ROWS = [1, 4, 7, 10, 13, 16]
ROD_COLS = [2, 6, 10, 14, 18]
TS_ROWS  = [2, 5, 9, 12, 15]
TS_COLS  = [0, 4, 8, 12, 16, 20]

NUM_RODS    = 30
NUM_TSENSORS = 30
NUM_PUMPS   = 4

# Spatial influence weights: rod i is influenced by T-sensor j with weight w[i][j]
# w = exp(-distance / sigma).  Higher sigma → more smoothing.
SPATIAL_SIGMA = 4.5

def _build_spatial_weights() -> list:
    """Returns NUM_RODS × NUM_TSENSORS weight matrix (row-major)."""
    rods = [(r, c) for r in ROD_ROWS for c in ROD_COLS]
    tsensors = [(r, c) for r in TS_ROWS for c in TS_COLS]
    weights = []
    for rr, rc in rods:
        row_w = []
        total = 0.0
        for tr, tc in tsensors:
            d = math.sqrt((rr - tr)**2 + (rc - tc)**2)
            w = math.exp(-d / SPATIAL_SIGMA)
            row_w.append(w)
            total += w
        # Normalise so each rod's weights sum to 1
        weights.append([w / total if total > 0 else 1/NUM_TSENSORS for w in row_w])
    return weights

SPATIAL_WEIGHTS = _build_spatial_weights()

# ── PID controller ─────────────────────────────────────────────────────────

class PID:
    def __init__(self, kp: float, ki: float, kd: float,
                 out_min: float, out_max: float, anti_windup: bool = True):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.out_min, self.out_max = out_min, out_max
        self.anti_windup = anti_windup
        self._integral = 0.0
        self._prev_error = 0.0

    def reset(self):
        self._integral = 0.0
        self._prev_error = 0.0

    def update(self, error: float, dt: float) -> float:
        self._integral += error * dt
        derivative = (error - self._prev_error) / dt if dt > 0 else 0.0
        self._prev_error = error
        out = self.kp * error + self.ki * self._integral + self.kd * derivative
        # Clamp output
        out_clamped = max(self.out_min, min(self.out_max, out))
        # Anti-windup: undo integral accumulation when saturated
        if self.anti_windup and out != out_clamped:
            self._integral -= error * dt
        return out_clamped


# ── Process data container ─────────────────────────────────────────────────

@dataclass
class ProcessData:
    # Plant-wide
    power_pct:    float = 0.0
    output_mw:    float = 0.0
    pressure_mpa: float = 0.0
    flow_pct:     float = 0.0
    void_pct:     float = 0.0
    avg_temp_c:   float = 0.0
    max_temp_c:   float = 0.0
    turbine_eff:  float = 0.0
    xenon:        float = 0.0
    iodine:       float = 0.0
    target_mw:    float = 0.0  # grid demand target from game mode (0 = not in shift)
    # Arrays (30 rods, 30 T-sensors, 4 pumps)
    rod_pos:      List[float] = field(default_factory=lambda: [0.0]*NUM_RODS)
    t_sensors:    List[float] = field(default_factory=lambda: [0.0]*NUM_TSENSORS)
    pump_speeds:  List[float] = field(default_factory=lambda: [0.0]*NUM_PUMPS)
    # Alarms & status
    reactor_running: bool = False
    scram_active:    bool = False
    alarms: dict = field(default_factory=dict)
    pump_running: List[bool] = field(default_factory=lambda: [False]*NUM_PUMPS)
    # Connection
    connected: bool = False
    read_error: str = ""


# ── Main controller ─────────────────────────────────────────────────────────

class ReactorController:
    """
    Closed-loop Modbus controller.

    Control structure (runs every CONTROL_INTERVAL seconds):

    1. READ all inputs via Modbus.
    2. Safety check — SCRAM if any hard limit exceeded.
    3. Coolant loop — set pump speeds based on power & temp.
    4. Power loop  — PI controller adjusts base rod withdrawal.
    5. Spatial loop — P controller trims individual rods using T-sensor
                      proximity weights to flatten the radial flux profile.
    6. WRITE rod setpoints and pump speeds via Modbus.
    """

    CONTROL_INTERVAL = 1.0   # seconds between control updates
    DISPLAY_INTERVAL = 0.25  # seconds between screen refreshes

    # Safety limits — SCRAM if exceeded
    SCRAM_POWER_PCT  = 92.0
    SCRAM_TEMP_C     = 370.0
    SCRAM_VOID_PCT   = 35.0
    SCRAM_PRESSURE   = 9.5

    # Coolant targets
    FLOW_BASE_PCT    = 70.0   # minimum flow at zero power
    FLOW_PER_MW      = 0.025  # extra % flow per MWe setpoint
    FLOW_TEMP_BOOST  = 0.20   # extra % flow per °C above 290°C
    FLOW_VOID_BOOST  = 1.50   # extra % flow per % void above 10%
    FLOW_MIN_PCT     = 25.0
    FLOW_MAX_PCT     = 100.0

    # Power PI gains (output = base rod withdrawal %)
    POWER_KP = 0.04    # %rod per MWe error
    POWER_KI = 0.006   # %rod per MWe·s error
    POWER_KD = 0.00    # derivative not useful with noisy flux

    # Spatial flattening gain (rod trim per °C above local average)
    SPATIAL_KP   = 0.06
    SPATIAL_MAX  = 12.0  # max trim ±% from base setpoint

    # Rod movement rate limiting (max change per control cycle)
    ROD_RATE_LIMIT = 3.0   # % per second

    def __init__(self, host: str, port: int, unit: int, setpoint_mw: float):
        self.host = host
        self.port = port
        self.unit = unit
        self.setpoint_mw = setpoint_mw
        self.mode = "AUTO"   # "AUTO" | "MANUAL"

        self._client: Optional[ModbusTcpClient] = None
        self._lock = threading.Lock()
        self._data = ProcessData()

        self._power_pid = PID(
            self.POWER_KP, self.POWER_KI, self.POWER_KD,
            out_min=0.0, out_max=95.0,
        )
        # Initialise integral to 50% so rods start at mid-withdrawal
        self._power_pid._integral = 50.0 / max(self.POWER_KI, 1e-9)

        # Current rod setpoints (maintained locally between writes)
        self._rod_sp   = [50.0] * NUM_RODS
        # Current pump setpoints
        self._pump_sp  = [75.0] * NUM_PUMPS

        self._log: collections.deque = collections.deque(maxlen=60)
        self._running = False
        self._last_control_time = 0.0

    # ── Logging ────────────────────────────────────────────────────────────

    def _log_msg(self, msg: str, level: str = "info"):
        ts = time.strftime("%H:%M:%S")
        self._log.appendleft(f"[{ts}] {msg}")

    # ── Modbus connection ──────────────────────────────────────────────────

    def _connect(self) -> bool:
        try:
            if self._client:
                self._client.close()
            self._client = ModbusTcpClient(host=self.host, port=self.port, timeout=2)
            result = self._client.connect()
            if result:
                self._log_msg(f"Connected to {self.host}:{self.port} unit {self.unit}")
                self._data.connected = True
                self._data.read_error = ""
            else:
                self._data.connected = False
                self._data.read_error = "Connection refused"
            return result
        except Exception as e:
            self._data.connected = False
            self._data.read_error = str(e)
            return False

    # ── Modbus read/write helpers ──────────────────────────────────────────

    def _read_ir(self, address: int, count: int) -> Optional[list]:
        """Read input registers (FC04). Returns list of raw ints or None."""
        try:
            r = self._client.read_input_registers(address=address, count=count,
                                                   slave=self.unit)
            if r.isError():
                return None
            return r.registers
        except Exception:
            return None

    def _read_di(self, address: int, count: int) -> Optional[list]:
        """Read discrete inputs (FC02). Returns list of bools or None."""
        try:
            r = self._client.read_discrete_inputs(address=address, count=count,
                                                   slave=self.unit)
            if r.isError():
                return None
            return r.bits[:count]
        except Exception:
            return None

    def _write_hr(self, address: int, values: list) -> bool:
        """Write holding registers (FC16)."""
        try:
            r = self._client.write_registers(address=address, values=values,
                                              slave=self.unit)
            return not r.isError()
        except Exception:
            return False

    def _write_coil(self, address: int, value: bool) -> bool:
        """Write single coil (FC05)."""
        try:
            r = self._client.write_coil(address=address, value=value,
                                         slave=self.unit)
            return not r.isError()
        except Exception:
            return False

    # ── Read all process inputs ────────────────────────────────────────────

    def _read_inputs(self) -> bool:
        d = self._data

        # Plant-wide (10 registers starting at 0)
        regs = self._read_ir(0, 11)  # 0-9 plant-wide + 10 target_mw
        if regs is None:
            d.connected = False
            d.read_error = "Read failed — is the simulator running?"
            return False

        d.power_pct    = raw_to_eng(regs[0], 0,    100)
        d.output_mw    = raw_to_eng(regs[1], 0,    1100)
        d.pressure_mpa = raw_to_eng(regs[2], 0,    15)
        d.flow_pct     = raw_to_eng(regs[3], 0,    100)
        d.void_pct     = raw_to_eng(regs[4], 0,    100)
        d.avg_temp_c   = raw_to_eng(regs[5], 0,    500)
        d.max_temp_c   = raw_to_eng(regs[6], 0,    500)
        d.turbine_eff  = raw_to_eng(regs[7], 0,    35)
        d.iodine       = raw_to_eng(regs[8], 0,    1)
        d.xenon        = raw_to_eng(regs[9], 0,    1)
        d.target_mw    = raw_to_eng(regs[10], 0, 1100) if len(regs) > 10 else 0.0

        # T-sensors (addresses 100–129)
        ts_regs = self._read_ir(100, NUM_TSENSORS)
        if ts_regs:
            for i, raw in enumerate(ts_regs):
                # 32767 = sensor fault → use avg_temp as fallback
                d.t_sensors[i] = (d.avg_temp_c if raw == RAW_MAX
                                  else raw_to_eng(raw, 0, 500))

        # Rod actual positions (addresses 200–229)
        rod_regs = self._read_ir(200, NUM_RODS)
        if rod_regs:
            for i, raw in enumerate(rod_regs):
                d.rod_pos[i] = raw_to_eng(raw, 0, 100)

        # Pump actual speeds (addresses 250–253)
        pump_regs = self._read_ir(250, NUM_PUMPS)
        if pump_regs:
            for i, raw in enumerate(pump_regs):
                d.pump_speeds[i] = raw_to_eng(raw, 0, 100)

        # Discrete inputs — alarms & pump status
        di = self._read_di(0, 50)
        if di:
            d.reactor_running = bool(di[0])
            d.scram_active    = bool(di[1])
            alarm_ids = ['hipower','hitemp','void','locool','pumptrip',
                         'scram','damage','hipress','xenonpit']
            for k, aid in enumerate(alarm_ids):
                d.alarms[aid] = bool(di[10 + k]) if 10+k < len(di) else False
            pump_di_addrs = [30, 33, 36, 39]
            for i, addr in enumerate(pump_di_addrs):
                d.pump_running[i] = bool(di[addr]) if addr < len(di) else False

        d.connected  = True
        d.read_error = ""
        return True

    # ── Safety interlocks ──────────────────────────────────────────────────

    def _check_safety(self) -> bool:
        """Returns True if SCRAM was triggered."""
        d = self._data
        if d.scram_active:
            return False  # already scrammed

        reasons = []
        if d.power_pct   > self.SCRAM_POWER_PCT:  reasons.append(f"power {d.power_pct:.1f}%")
        if d.max_temp_c  > self.SCRAM_TEMP_C:      reasons.append(f"max temp {d.max_temp_c:.0f}°C")
        if d.void_pct    > self.SCRAM_VOID_PCT:    reasons.append(f"void {d.void_pct:.1f}%")
        if d.pressure_mpa> self.SCRAM_PRESSURE:    reasons.append(f"pressure {d.pressure_mpa:.2f} MPa")

        if reasons:
            self._log_msg(f"AUTO-SCRAM: {', '.join(reasons)}", "danger")
            self._write_coil(0, True)   # coil 0 = AZ-5 SCRAM
            self._power_pid.reset()
            return True
        return False

    # ── Coolant control ────────────────────────────────────────────────────

    def _compute_flow_target(self) -> float:
        """Target flow % based on power setpoint and process conditions."""
        d = self._data
        base    = self.FLOW_BASE_PCT + self.setpoint_mw * self.FLOW_PER_MW
        # Temperature boost: ramp up flow if core getting hot
        t_boost = max(0.0, d.avg_temp_c - 290.0) * self.FLOW_TEMP_BOOST
        # Void boost: void fraction rising is an emergency
        v_boost = max(0.0, d.void_pct - 10.0) * self.FLOW_VOID_BOOST
        target  = base + t_boost + v_boost
        return max(self.FLOW_MIN_PCT, min(self.FLOW_MAX_PCT, target))

    def _control_pumps(self):
        """
        Set all four pumps independently.
        The pumps get slightly staggered speeds (±2%) to exercise individual
        pump control — in a real plant each pump would be controlled by its
        own interlock card.
        """
        flow_target = self._compute_flow_target()
        offsets = [0.0, +1.5, -1.5, +1.0]  # individual pump offsets
        for i in range(NUM_PUMPS):
            sp = max(20.0, min(100.0, flow_target + offsets[i]))
            self._pump_sp[i] = sp

        # Ensure all pumps are running
        for coil_addr in [10, 11, 12, 13]:
            self._write_coil(coil_addr, True)

        # Write pump speed setpoints (HR addresses 100–103)
        raw_speeds = [eng_to_raw(sp, 0, 100) for sp in self._pump_sp]
        self._write_hr(100, raw_speeds)

    # ── Power + spatial control ────────────────────────────────────────────

    def _control_rods(self, dt: float):
        """
        Two-layer rod control:
          1. PI power controller → base rod withdrawal for all 30 rods
          2. Spatial P controller → individual trim per rod based on local
             T-sensor temperatures (hot area → insert more, cool area → pull out)

        Rate limiting prevents sudden large rod movements.
        """
        d = self._data

        # ── Layer 1: PI power controller ──────────────────────────────────
        # If the simulator is in a shift game mode, the target_mw register
        # holds the current grid demand. Track it automatically.
        effective_sp = d.target_mw if d.target_mw > 10 else self.setpoint_mw
        error_mw  = effective_sp - d.output_mw
        base_sp   = self._power_pid.update(error_mw, dt)

        # ── Layer 2: Spatial flattening using T-sensor proximity ──────────
        # Valid (non-faulted) T-sensor temperatures
        valid_temps = [t for t in d.t_sensors if t > 0]
        avg_temp    = sum(valid_temps) / len(valid_temps) if valid_temps else d.avg_temp_c

        trims = []
        for rod_idx in range(NUM_RODS):
            w = SPATIAL_WEIGHTS[rod_idx]
            # Weighted local temperature for this rod
            local_temp = sum(w[j] * d.t_sensors[j] for j in range(NUM_TSENSORS))
            # Positive trim = this area is hotter than average → insert rod more
            # (reduce withdrawal to reduce local flux)
            raw_trim = self.SPATIAL_KP * (local_temp - avg_temp)
            trim = max(-self.SPATIAL_MAX, min(self.SPATIAL_MAX, raw_trim))
            trims.append(trim)

        # ── Combine and rate-limit ─────────────────────────────────────────
        max_change = self.ROD_RATE_LIMIT * dt
        new_sps = []
        for i in range(NUM_RODS):
            target = max(0.0, min(95.0, base_sp - trims[i]))
            delta  = target - self._rod_sp[i]
            delta  = max(-max_change, min(max_change, delta))
            self._rod_sp[i] = max(0.0, min(95.0, self._rod_sp[i] + delta))
            new_sps.append(self._rod_sp[i])

        # ── Write to Modbus in two batches (16-register limit per write) ───
        raw_sps = [eng_to_raw(sp, 0, 100) for sp in new_sps]
        self._write_hr(0,  raw_sps[:16])
        self._write_hr(16, raw_sps[16:])

    # ── Control loop ──────────────────────────────────────────────────────

    def _control_loop(self):
        """Background thread — reconnects, reads, controls, writes."""
        while self._running:
            now = time.monotonic()

            # Reconnect if needed
            if not self._data.connected or (
                    self._client and not self._client.is_socket_open()):
                if not self._connect():
                    time.sleep(3.0)
                    continue

            # Read all inputs
            with self._lock:
                ok = self._read_inputs()

            if not ok:
                time.sleep(1.0)
                continue

            # Control update (rate-limited)
            dt = now - self._last_control_time
            if dt >= self.CONTROL_INTERVAL:
                with self._lock:
                    if self.mode == "AUTO" and not self._data.scram_active:
                        if not self._check_safety():
                            self._control_pumps()
                            self._control_rods(dt)
                self._last_control_time = now

            time.sleep(0.1)

    # ── Public interface ───────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._last_control_time = time.monotonic()
        t = threading.Thread(target=self._control_loop, daemon=True)
        t.start()

    def stop(self):
        self._running = False
        if self._client:
            self._client.close()

    def send_scram(self):
        if self._client and self._data.connected:
            self._write_coil(0, True)
            self._log_msg("Manual SCRAM sent", "danger")
            self._power_pid.reset()

    @property
    def data(self) -> ProcessData:
        return self._data

    @property
    def log(self):
        return list(self._log)

    @property
    def rod_setpoints(self) -> List[float]:
        return list(self._rod_sp)

    @property
    def pump_setpoints(self) -> List[float]:
        return list(self._pump_sp)


# ── Curses TUI ─────────────────────────────────────────────────────────────

ALARM_LABELS = {
    'hipower':  'HI POWER',  'hitemp':  'HI TEMP',
    'void':     'HI VOID',   'locool':  'LO FLOW',
    'pumptrip': 'PUMP TRIP', 'scram':   'SCRAM',
    'damage':   'DAMAGE',    'hipress': 'HI PRESS',
    'xenonpit': 'Xe PIT',
}


def _colour(val, warn, crit, c_norm, c_warn, c_crit):
    if val >= crit:  return c_crit
    if val >= warn:  return c_warn
    return c_norm


# ── Key input thread (Windows-safe) ──────────────────────────────────────
# On Windows, curses getch() inside a wrapper is unreliable for keypresses.
# We read keys in a daemon thread using msvcrt (Windows) or tty/termios
# (Linux/Mac) and push them into a queue that the display loop drains.

import queue as _queue
_key_queue: _queue.SimpleQueue = _queue.SimpleQueue()

def _key_reader_thread(stop_event: threading.Event):
    """Read keypresses and put them onto _key_queue. Platform-aware."""
    if sys.platform == "win32":
        import msvcrt
        while not stop_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                # Arrow keys on Windows come as two chars: '\x00' or '\xe0' + code
                if ch in ('\x00', '\xe0'):
                    ch2 = msvcrt.getwch()
                    if   ch2 == 'H': _key_queue.put('UP')
                    elif ch2 == 'P': _key_queue.put('DOWN')
                    elif ch2 == 'M': _key_queue.put('RIGHT')
                    elif ch2 == 'K': _key_queue.put('LEFT')
                else:
                    _key_queue.put(ch.lower())
            else:
                time.sleep(0.02)
    else:
        # Linux/Mac: use select on stdin with raw tty
        import tty, termios, select
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while not stop_event.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    ch = sys.stdin.read(1)
                    if ch == '\x1b':  # escape sequence (arrow keys)
                        r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if r2:
                            ch2 = sys.stdin.read(1)
                            if ch2 == '[':
                                ch3 = sys.stdin.read(1)
                                if   ch3 == 'A': _key_queue.put('UP')
                                elif ch3 == 'B': _key_queue.put('DOWN')
                    else:
                        _key_queue.put(ch.lower())
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _handle_key(key: str, ctrl: ReactorController) -> bool:
    """Process one keypress. Returns True if quit requested."""
    if key in ('q',):
        return True
    elif key in ('up', '+', '='):
        ctrl.setpoint_mw = min(1000, ctrl.setpoint_mw + 25)
        ctrl._log_msg(f"Setpoint -> {ctrl.setpoint_mw:.0f} MWe")
    elif key in ('down', '-', '_'):
        ctrl.setpoint_mw = max(0, ctrl.setpoint_mw - 25)
        ctrl._log_msg(f"Setpoint -> {ctrl.setpoint_mw:.0f} MWe")
    elif key == 'a':
        ctrl.mode = "AUTO"
        ctrl._log_msg("Mode -> AUTO")
    elif key == 'm':
        ctrl.mode = "MANUAL"
        ctrl._log_msg("Mode -> MANUAL (outputs suspended)")
    elif key == 's':
        ctrl.send_scram()
    return False


def run_curses(stdscr, ctrl: ReactorController):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN,  -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED,    -1)
    curses.init_pair(4, curses.COLOR_CYAN,   -1)
    curses.init_pair(5, curses.COLOR_WHITE,  -1)
    curses.init_pair(6, curses.COLOR_MAGENTA,-1)

    C_NORM  = curses.color_pair(1)
    C_WARN  = curses.color_pair(2)
    C_CRIT  = curses.color_pair(3)
    C_INFO  = curses.color_pair(4)
    C_WHITE = curses.color_pair(5)
    C_XE    = curses.color_pair(6)

    curses.curs_set(0)
    # Display-only: curses just refreshes at DISPLAY_INTERVAL.
    # Keys are read by _key_reader_thread via msvcrt/tty — not via getch().
    stdscr.nodelay(True)   # non-blocking so we can drive the display loop ourselves
    stdscr.timeout(0)

    # Start key reader thread
    _stop = threading.Event()
    _kt = threading.Thread(target=_key_reader_thread, args=(_stop,), daemon=True)
    _kt.start()

    ctrl.start()

    while True:
        # Drain the key queue
        quit_requested = False
        while not _key_queue.empty():
            try:
                key = _key_queue.get_nowait()
                if _handle_key(key, ctrl):
                    quit_requested = True
            except _queue.Empty:
                break
        if quit_requested:
            break

        d   = ctrl.data
        rsp = ctrl.rod_setpoints
        psp = ctrl.pump_setpoints
        rows, cols = stdscr.getmaxyx()
        stdscr.erase()

        # ── Header ─────────────────────────────────────────────────────
        title = " RBMK-1000 MODBUS TRAINING CLIENT "
        conn  = " CONNECTED " if d.connected else " OFFLINE "
        mode  = f" {ctrl.mode} "
        stdscr.addstr(0, 0, "─" * cols, C_INFO)
        stdscr.addstr(0, max(0, (cols - len(title)) // 2), title,
                      C_WHITE | curses.A_BOLD)
        conn_attr = C_NORM if d.connected else C_CRIT
        stdscr.addstr(0, cols - len(conn) - len(mode) - 2, conn, conn_attr)
        mode_attr = C_WARN if ctrl.mode == "MANUAL" else C_NORM
        stdscr.addstr(0, cols - len(mode) - 1, mode, mode_attr | curses.A_BOLD)

        row = 2

        # ── Left column: process values ────────────────────────────────
        col_w = 32
        def lbl(r, c, text, attr=C_INFO):
            if r < rows and c + len(text) < cols:
                stdscr.addstr(r, c, text, attr)

        lbl(row,   0, "── PROCESS VALUES ──────────────", C_INFO)
        row += 1

        effective_sp = d.target_mw if d.target_mw > 10 else ctrl.setpoint_mw
        mw_c = _colour(d.output_mw, effective_sp * 0.85,
                       effective_sp * 1.1, C_NORM, C_WARN, C_CRIT)
        err  = d.output_mw - effective_sp
        if d.target_mw > 10:
            lbl(row, 0, f"  Game Demand: {d.target_mw:>5.0f} MWe  [TRACKING]", C_WARN); row+=1
        lbl(row,   0, f"  Setpoint  : {ctrl.setpoint_mw:>6.0f} MWe", C_INFO); row+=1
        lbl(row,   0, f"  Output    : {d.output_mw:>6.0f} MWe", mw_c);        row+=1
        lbl(row,   0, f"  Error     : {err:>+6.0f} MWe",
            C_CRIT if abs(err) > ctrl.setpoint_mw*0.15 else
            C_WARN if abs(err) > ctrl.setpoint_mw*0.05 else C_NORM);           row+=1
        lbl(row,   0, f"  Power     : {d.power_pct:>5.1f} %",
            _colour(d.power_pct, 80, 90, C_NORM, C_WARN, C_CRIT));             row+=1
        lbl(row,   0, f"  Avg Temp  : {d.avg_temp_c:>5.0f} °C",
            _colour(d.avg_temp_c, 290, 320, C_NORM, C_WARN, C_CRIT));          row+=1
        lbl(row,   0, f"  Max Temp  : {d.max_temp_c:>5.0f} °C",
            _colour(d.max_temp_c, 310, 360, C_NORM, C_WARN, C_CRIT));          row+=1
        lbl(row,   0, f"  Flow      : {d.flow_pct:>5.1f} %",
            _colour(100-d.flow_pct, 50, 70, C_NORM, C_WARN, C_CRIT));          row+=1
        lbl(row,   0, f"  Pressure  : {d.pressure_mpa:>5.2f} MPa",
            _colour(d.pressure_mpa, 7.5, 8.5, C_NORM, C_WARN, C_CRIT));        row+=1
        lbl(row,   0, f"  Void      : {d.void_pct:>5.1f} %",
            _colour(d.void_pct, 15, 25, C_NORM, C_WARN, C_CRIT));              row+=1
        lbl(row,   0, f"  Xenon     : {d.xenon:>6.3f}",
            C_XE if d.xenon > 0.25 else C_NORM);                               row+=1
        lbl(row,   0, f"  Iodine    : {d.iodine:>6.3f}", C_NORM);              row+=1
        lbl(row,   0, f"  Turbine   : {d.turbine_eff:>5.1f} %", C_NORM);       row+=1

        row += 1
        lbl(row, 0, "── PUMPS (setpoint / actual) ──", C_INFO); row+=1
        pump_names = ["MCP-1","MCP-2","MCP-3","MCP-4"]
        for i in range(NUM_PUMPS):
            running = d.pump_running[i]
            run_str = "RUN" if running else "OFF"
            run_c   = C_NORM if running else C_CRIT
            sp_str  = f"{psp[i]:>5.1f}% → {d.pump_speeds[i]:>5.1f}%"
            if row < rows:
                lbl(row, 0, f"  {pump_names[i]}: ", C_INFO)
                lbl(row, 10, sp_str, C_NORM)
                lbl(row, 26, f"[{run_str}]", run_c)
            row += 1

        row += 1
        lbl(row, 0, "── ALARMS ──────────────────────", C_INFO); row+=1
        alarm_row = row
        for i, (aid, albl) in enumerate(ALARM_LABELS.items()):
            c = cols // 2
            r2 = alarm_row + i // 3
            c2 = (i % 3) * 11
            active = d.alarms.get(aid, False)
            attr   = C_CRIT | curses.A_BOLD if active else C_INFO
            if r2 < rows and c2 + 11 <= col_w:
                lbl(r2, c2, f"[{albl[:8]:8}]", attr)
        row = alarm_row + (len(ALARM_LABELS) + 2) // 3 + 1

        # ── Right column: rod map ─────────────────────────────────────
        rc = col_w + 2
        rod_header_row = 2
        if rc + 50 < cols:
            lbl(rod_header_row, rc, "── CONTROL RODS (setpoint → actual) ──────────────", C_INFO)
            rod_header_row += 1
            for i in range(NUM_RODS):
                r2 = rod_header_row + i // 5
                c2 = rc + (i % 5) * 14
                sp   = rsp[i]
                act  = d.rod_pos[i]
                diff = sp - act
                # Colour: green=close, yellow=moving, red=large mismatch
                if abs(diff) < 2:    rc2 = C_NORM
                elif abs(diff) < 8:  rc2 = C_WARN
                else:                rc2 = C_CRIT
                label = f"R{i+1:02d}:{sp:>4.0f}→{act:>4.0f}"
                if r2 < rows and c2 + 14 < cols:
                    lbl(r2, c2, label, rc2)

            # T-sensor heat map
            t_row = rod_header_row + 7
            if t_row + 8 < rows:
                lbl(t_row, rc, "── T-SENSOR GRID (°C) — hot=red, cool=cyan ───────", C_INFO)
                t_row += 1
                valid_t = [t for t in d.t_sensors if t > 0]
                t_avg   = sum(valid_t) / len(valid_t) if valid_t else 280
                for i, temp in enumerate(d.t_sensors):
                    r2 = t_row + i // 6
                    c2 = rc + (i % 6) * 12
                    dev = temp - t_avg
                    if   dev >  20: tc = C_CRIT
                    elif dev >  8:  tc = C_WARN
                    elif dev < -8:  tc = C_INFO
                    else:           tc = C_NORM
                    label = f"T{i+1:02d}:{temp:>5.1f}"
                    if r2 < rows and c2 + 12 < cols:
                        lbl(r2, c2, label, tc)

        # ── Event log ─────────────────────────────────────────────────
        log_row = rows - 12
        if log_row > row and log_row < rows:
            lbl(log_row, 0, "─" * cols, C_INFO)
            log_row += 1
            lbl(log_row, 0, "  EVENT LOG", C_INFO | curses.A_BOLD)
            log_row += 1
            for i, entry in enumerate(ctrl.log[:rows - log_row - 1]):
                if log_row + i < rows - 1:
                    try:
                        stdscr.addstr(log_row + i, 2, entry[:cols - 4], C_NORM)
                    except curses.error:
                        pass

        # ── Footer ────────────────────────────────────────────────────
        footer = (" ↑/↓ or +/- : setpoint ±25MWe  |"
                  "  a : AUTO  |  m : MANUAL  |  s : SCRAM  |  q : quit ")
        if rows - 1 >= 0:
            try:
                stdscr.addstr(rows - 1, 0, footer[:cols], C_INFO)
            except curses.error:
                pass

        stdscr.refresh()

    ctrl.stop()


# ── Fallback print mode (Windows without windows-curses) ──────────────────

def run_print(ctrl: ReactorController):
    """Print loop with threaded key reader — works without curses."""
    import shutil

    # Start key reader thread (same as TUI mode)
    _stop = threading.Event()
    _kt = threading.Thread(target=_key_reader_thread, args=(_stop,), daemon=True)
    _kt.start()

    ctrl.start()
    print("RBMK-1000 Modbus Client — Print Mode")
    print("Keys: UP/+  DOWN/-  a=AUTO  m=MANUAL  s=SCRAM  q=quit")
    print("(Keys active immediately)\n")
    try:
        while True:
            # Drain keys
            while not _key_queue.empty():
                try:
                    key = _key_queue.get_nowait()
                    if _handle_key(key, ctrl):
                        raise KeyboardInterrupt
                except _queue.Empty:
                    break
            d = ctrl.data
            w = shutil.get_terminal_size().columns
            print("\033[H\033[J", end="")  # clear screen
            print(f"{'─'*w}")
            print(f"  RBMK-1000 Modbus Client  |  "
                  f"{'CONNECTED' if d.connected else 'OFFLINE'}  |  Mode: {ctrl.mode}")
            print(f"{'─'*w}")
            print(f"  Setpoint : {ctrl.setpoint_mw:>6.0f} MWe   "
                  f"Output: {d.output_mw:>6.0f} MWe   "
                  f"Error: {d.output_mw - ctrl.setpoint_mw:>+6.0f} MWe")
            print(f"  Power    : {d.power_pct:>5.1f}%   "
                  f"Temp: {d.avg_temp_c:>5.0f}°C (max {d.max_temp_c:.0f})   "
                  f"Flow: {d.flow_pct:.1f}%   Void: {d.void_pct:.1f}%")
            print(f"  Pressure : {d.pressure_mpa:>5.2f} MPa   "
                  f"Xenon: {d.xenon:.3f}   Iodine: {d.iodine:.3f}")
            print()
            print(f"  Pumps: " + "  ".join(
                f"MCP-{i+1}: {ctrl.pump_setpoints[i]:.0f}%→{d.pump_speeds[i]:.0f}%"
                for i in range(NUM_PUMPS)
            ))
            print()
            print("  Rods (setpoint):")
            for i in range(0, NUM_RODS, 6):
                print("    " + "  ".join(
                    f"CR{i+j+1:02d}:{ctrl.rod_setpoints[i+j]:>4.0f}%"
                    for j in range(6) if i+j < NUM_RODS
                ))
            print()
            active_alarms = [ALARM_LABELS[k] for k, v in d.alarms.items() if v]
            if active_alarms:
                print(f"  ALARMS: {', '.join(active_alarms)}")
            print()
            print("  Log:")
            for entry in list(ctrl.log)[:5]:
                print(f"    {entry}")
            time.sleep(ctrl.DISPLAY_INTERVAL)
    except KeyboardInterrupt:
        ctrl.stop()


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="RBMK-1000 Modbus Training Control Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--host",     default="127.0.0.1",
                    help="Modbus server host (default: 127.0.0.1)")
    ap.add_argument("--port",     type=int, default=502,
                    help="Modbus TCP port (default: 502)")
    ap.add_argument("--unit",     type=int, default=1,
                    help="Modbus unit/slave ID (default: 1)")
    ap.add_argument("--setpoint", type=float, default=600.0,
                    help="Initial power setpoint in MWe (default: 600)")
    ap.add_argument("--mode",     choices=["AUTO","MANUAL"], default="AUTO",
                    help="Initial control mode (default: AUTO)")
    ap.add_argument("--no-tui",   action="store_true",
                    help="Use print mode instead of curses TUI")
    args = ap.parse_args()

    ctrl = ReactorController(
        host        = args.host,
        port        = args.port,
        unit        = args.unit,
        setpoint_mw = args.setpoint,
    )
    ctrl.mode = args.mode

    print(f"Connecting to {args.host}:{args.port} (unit {args.unit}) ...")
    print(f"Power setpoint: {args.setpoint:.0f} MWe | Mode: {args.mode}")
    print()

    if args.no_tui or not HAS_CURSES:
        run_print(ctrl)
    else:
        try:
            curses.wrapper(run_curses, ctrl)
        except Exception as e:
            ctrl.stop()
            print(f"TUI error: {e}")
            print("Try running with --no-tui flag.")
            sys.exit(1)

    print("\nClient stopped.")


if __name__ == "__main__":
    main()
