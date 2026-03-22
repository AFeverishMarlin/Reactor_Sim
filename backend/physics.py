"""
physics.py — RBMK-1000 Reactor Physics Engine
Ported from the JavaScript simulator. All physics constants and equations
are identical to the browser version. Runs as an asyncio task at 150ms/tick.
"""

import math
import random
import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable

log = logging.getLogger(__name__)

# ── Grid constants ────────────────────────────────────────────────────
ROWS = 18
COLS = 22
N    = ROWS * COLS

# Channel types
CH_GRAPHITE = 0
CH_FUEL     = 1
CH_ROD      = 2
CH_TSENSOR  = 3
CH_FSENSOR  = 4

# ── Xenon/Iodine decay constants ──────────────────────────────────────
BASE_TC   = 40                              # time compression × (set by difficulty)
_LN2      = math.log(2)
def _lambda_I(tc):  return _LN2 / (6.6 * 3600) * tc
def _lambda_Xe(tc): return _LN2 / (9.2 * 3600) * tc

SIGMA_XE     = 0.040    # neutron burnout cross-section
GAMMA_I      = None     # set at init (= λI, so I_eq = pwr)
GAMMA_XE     = 0.00496  # direct Xe fission yield (recalibrated for σXe=0.040)
XENON_WORTH  = 1.2      # Xe=1 → mult drops by 1.2

# ── Thermal constants ─────────────────────────────────────────────────
HEAT_K = 48     # fission heat coefficient
COOL_C = 0.45   # forced cooling per unit flow fraction
NAT    = 0.015  # natural convection floor

# ── Rated power ───────────────────────────────────────────────────────
RATED_THERMAL_MW = 3200
THERMAL_TO_ELEC  = 0.315
RATED_ELEC_MW    = round(RATED_THERMAL_MW * THERMAL_TO_ELEC)  # 1008


@dataclass
class Rod:
    idx:      int
    row:      int
    col:      int
    id:       str
    pos:      float = 0.0     # current position 0–100%
    target:   float = 0.0     # target position
    selected: bool  = False
    mode:     str   = "auto"  # auto | manual | fault


@dataclass
class Sensor:
    idx:   int
    row:   int
    col:   int
    type:  str    # T | F
    label: str
    value: Optional[float] = None  # None = faulted
    fault: bool = False


@dataclass
class Pump:
    id:    int
    name:  str
    on:    bool  = True
    speed: float = 80.0
    fault: bool  = False


@dataclass
class GlobalState:
    power:        float = 0.0
    temp:         float = 40.0
    pressure:     float = 0.3
    flux:         float = 0.0
    xenon:        float = 0.0
    iodine:       float = 0.0
    void_fraction: float = 0.0
    max_chan_t:   float = 40.0
    scramming:    bool  = False
    scram_done:   bool  = False
    meltdown:     bool  = False
    frame:        int   = 0
    # Alarms
    alarms: Dict[str, bool] = field(default_factory=dict)
    # Log buffer (most recent events)
    log_buffer: List[dict] = field(default_factory=list)


# ── Difficulty presets ────────────────────────────────────────────────
DIFF_PRESETS = {
    "easy": dict(
        tc=20, rate_heat_base=0.008, rate_heat_flow=0.022,
        rate_cool_base=0.002, rate_cool_flow=0.028,
        flux_lag=0.022, void_mult=0.55,
        fault_min_ticks=500, fault_rand_ticks=700,
    ),
    "normal": dict(
        tc=40, rate_heat_base=0.015, rate_heat_flow=0.040,
        rate_cool_base=0.003, rate_cool_flow=0.050,
        flux_lag=0.040, void_mult=1.0,
        fault_min_ticks=200, fault_rand_ticks=400,
    ),
    "hard": dict(
        tc=80, rate_heat_base=0.028, rate_heat_flow=0.075,
        rate_cool_base=0.005, rate_cool_flow=0.090,
        flux_lag=0.075, void_mult=1.5,
        fault_min_ticks=100, fault_rand_ticks=150,
    ),
    "extreme": dict(
        tc=160, rate_heat_base=0.055, rate_heat_flow=0.140,
        rate_cool_base=0.008, rate_cool_flow=0.170,
        flux_lag=0.140, void_mult=2.2,
        fault_min_ticks=60, fault_rand_ticks=80,
    ),
}

ALARM_DEFS = [
    ("hipower",  "HI REACTOR POWER"),
    ("hitemp",   "HI CORE TEMP"),
    ("void",     "HIGH VOID FRACTION"),
    ("locool",   "LO COOLANT FLOW"),
    ("pumptrip", "PUMP TRIP"),
    ("scram",    "AZ-5 SCRAM"),
    ("damage",   "CORE DAMAGE"),
    ("hipress",  "HI STEAM PRESSURE"),
    ("xenonpit", "XENON IODINE PIT"),
]


class ReactorPhysics:
    """
    Complete RBMK-1000 physics simulation.
    Thread-safe via asyncio; call tick() from an asyncio task.
    Notifies registered callbacks after each tick with the full serialisable state.
    """

    TICK_INTERVAL = 0.150  # seconds

    def __init__(self):
        # Per-channel arrays
        self.ch_type    = [CH_GRAPHITE] * N
        self.ch_pow     = [0.0] * N
        self.ch_t       = [40.0] * N
        self.ch_v       = [0.0] * N
        self.ch_i       = [0.0] * N   # Iodine-135
        self.ch_xe      = [0.0] * N   # Xenon-135
        self.ch_removed = [False] * N

        # Entities
        self.rods:    List[Rod]    = []
        self.sensors: List[Sensor] = []
        self.pumps:   List[Pump]   = [
            Pump(0, "MCP-1"), Pump(1, "MCP-2"),
            Pump(2, "MCP-3"), Pump(3, "MCP-4"),
        ]

        # Lookup maps keyed by channel index
        self._rod_map:    Dict[int, Rod]    = {}
        self._sensor_map: Dict[int, Sensor] = {}

        # Precomputed rod influence weights: ch_idx → list of (rod, weight)
        self._chan_rod_w: Dict[int, dict] = {}

        self.state = GlobalState()
        self.state.alarms = {k: False for k, _ in ALARM_DEFS}

        # Difficulty
        self._diff = DIFF_PRESETS["normal"].copy()
        self._tc   = self._diff["tc"]
        self._lambda_i  = _lambda_I(self._tc)
        self._lambda_xe = _lambda_Xe(self._tc)
        self._gamma_i   = self._lambda_i   # keeps I_eq = pwr

        # Sim speed multiplier (0=paused, 1=normal, 2=fast, 0.25=slow)
        self.sim_speed: float = 1.0

        # Callbacks
        self._on_tick_callbacks: List[Callable] = []
        self._on_log_callbacks:  List[Callable] = []

        # Running flag
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._init_channels()
        self._precompute_rod_weights()

    # ──────────────────────────────────────────────────────────────────
    # Initialisation
    # ──────────────────────────────────────────────────────────────────

    def _snap(self, r, c):
        return c if (r + c) % 2 == 0 else (c + 1 if c + 1 < COLS else c - 1)

    def _init_channels(self):
        # Fill active channel positions with fuel
        for r in range(ROWS):
            for c in range(COLS):
                self.ch_type[r * COLS + c] = CH_FUEL if (r + c) % 2 == 0 else CH_GRAPHITE

        # Control rods: 6 rows × 5 cols
        rod_rows = [1, 4, 7, 10, 13, 16]
        rod_cols = [2, 6, 10, 14, 18]
        for r in rod_rows:
            for c in rod_cols:
                cc = self._snap(r, c)
                idx = r * COLS + cc
                self.ch_type[idx] = CH_ROD
                rod_id = f"CR-{len(self.rods)+1:02d}"
                rod = Rod(idx=idx, row=r, col=cc, id=rod_id)
                self.rods.append(rod)
                self._rod_map[idx] = rod

        # Temperature sensors: 5 rows × 6 cols
        t_rows = [2, 5, 9, 12, 15]
        t_cols = [0, 4, 8, 12, 16, 20]
        tsn = 0
        for r in t_rows:
            for c in t_cols:
                cc = self._snap(r, c)
                idx = r * COLS + cc
                if self.ch_type[idx] == CH_FUEL:
                    self.ch_type[idx] = CH_TSENSOR
                    tsn += 1
                    s = Sensor(idx=idx, row=r, col=cc, type="T",
                               label=f"TS-{tsn:02d}", value=40.0)
                    self.sensors.append(s)
                    self._sensor_map[idx] = s

        # Flux sensors: 4 rows × 5 cols, uniform spatial grid
        fgr, fgc = 4, 5
        fsn = 0
        for gr in range(fgr):
            for gc in range(fgc):
                tr = round((gr + 0.5) * ROWS / fgr)
                tc = round((gc + 0.5) * COLS / fgc)
                best_idx, best_d = -1, 9999
                for dr in range(-4, 5):
                    for dc in range(-4, 5):
                        rr, cc2 = tr + dr, tc + dc
                        if not (0 <= rr < ROWS and 0 <= cc2 < COLS):
                            continue
                        ii = rr * COLS + cc2
                        if self.ch_type[ii] == CH_FUEL:
                            dist = dr * dr + dc * dc
                            if dist < best_d:
                                best_d, best_idx = dist, ii
                if best_idx >= 0:
                    fr, fc2 = best_idx // COLS, best_idx % COLS
                    self.ch_type[best_idx] = CH_FSENSOR
                    fsn += 1
                    s = Sensor(idx=best_idx, row=fr, col=fc2, type="F",
                               label=f"FS-{fsn:02d}", value=0.0)
                    self.sensors.append(s)
                    self._sensor_map[best_idx] = s

    def _precompute_rod_weights(self):
        L, RANGE = 3.2, 9
        for r in range(ROWS):
            for c in range(COLS):
                i = r * COLS + c
                if self.ch_type[i] == CH_GRAPHITE:
                    continue
                wlist, ws = [], 0.0
                for rod in self.rods:
                    d = math.sqrt((r - rod.row) ** 2 + (c - rod.col) ** 2)
                    if d < RANGE:
                        w = math.exp(-d / L)
                        wlist.append((rod, w))
                        ws += w
                self._chan_rod_w[i] = {"list": wlist, "ws": ws}

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _local_rod_factor(self, i: int) -> float:
        cw = self._chan_rod_w.get(i)
        if not cw or cw["ws"] == 0:
            return 0.0
        return sum((rod.pos / 100) * w for rod, w in cw["list"]) / cw["ws"]

    def _radial_imp(self, r: int, c: int) -> float:
        nr = (r / (ROWS - 1)) * 2 - 1
        nc = (c / (COLS - 1)) * 2 - 1
        d = math.sqrt(nr * nr + nc * nc)
        return max(0.70, math.cos(d * 0.55))

    def _avg_neighbour_t(self, r: int, c: int) -> float:
        s, n = 0.0, 0
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            rr, cc = r + dr, c + dc
            if 0 <= rr < ROWS and 0 <= cc < COLS:
                ii = rr * COLS + cc
                if self.ch_type[ii] == CH_FUEL:
                    s += self.ch_t[ii]
                    n += 1
        return s / n if n > 0 else self.state.temp

    def total_flow(self) -> float:
        s = sum(p.speed for p in self.pumps if p.on and not p.fault)
        return s / (len(self.pumps) * 100) * 100

    def current_output_mw(self) -> float:
        thermal_frac = self.state.power / 100
        turb_eff = THERMAL_TO_ELEC * (1 - self.state.void_fraction * 0.35)
        return max(0, RATED_THERMAL_MW * thermal_frac * turb_eff)

    def turbine_efficiency(self) -> float:
        return (1 - self.state.void_fraction * 0.35) * 31.5

    # ──────────────────────────────────────────────────────────────────
    # Alarm helpers
    # ──────────────────────────────────────────────────────────────────

    def _check_alarm(self, alarm_id: str, condition: bool):
        was = self.state.alarms.get(alarm_id, False)
        if condition and not was:
            self.state.alarms[alarm_id] = True
            label = next((l for k, l in ALARM_DEFS if k == alarm_id), alarm_id)
            self._log(f"ALARM: {label}", "danger")
        elif not condition and was:
            self.state.alarms[alarm_id] = False
            label = next((l for k, l in ALARM_DEFS if k == alarm_id), alarm_id)
            self._log(f"CLEARED: {label}", "info")

    def _log(self, msg: str, level: str = "info"):
        entry = {"msg": msg, "level": level, "frame": self.state.frame}
        self.state.log_buffer.append(entry)
        if len(self.state.log_buffer) > 200:
            self.state.log_buffer = self.state.log_buffer[-200:]
        for cb in self._on_log_callbacks:
            try:
                cb(entry)
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────────
    # Main physics tick
    # ──────────────────────────────────────────────────────────────────

    def tick(self):
        """Execute one physics tick. Call at 150ms intervals."""
        st = self.state
        if st.meltdown:
            return

        st.frame += 1
        diff = self._diff
        moving = 0

        # ── Rod animation ──────────────────────────────────────────────
        for rod in self.rods:
            if st.scramming:
                rod.target = 0.0
            elif rod.mode == "fault":
                rod.target = rod.pos  # fault: freeze at current position
            # manual mode: target was set by operator cmd_set_rod_pos — leave it

            d = rod.target - rod.pos
            if abs(d) > 0.05:
                step = 2.4 if st.scramming else 0.75
                rod.pos += math.copysign(min(abs(d), step), d)
                moving += 1
            else:
                rod.pos = rod.target

        if st.scramming and all(r.pos <= 0.1 for r in self.rods):
            st.scramming = False
            st.scram_done = True
            for r in self.rods:
                r.pos = r.target = 0.0
            self._log("AZ-5 COMPLETE — ALL RODS FULLY INSERTED", "warn")

        flow = self.total_flow()
        rate_heat = diff["rate_heat_base"] + (flow / 100) * diff["rate_heat_flow"]
        rate_cool = diff["rate_cool_base"] + (flow / 100) * diff["rate_cool_flow"]

        # ── Pass 1: per-channel flux, temperature, void ─────────────────
        sum_raw = 0.0; n_fuel = 0
        sum_t   = 0.0; n_ch   = 0
        sum_void = 0.0

        for r in range(ROWS):
            for c in range(COLS):
                i   = r * COLS + c
                ty  = self.ch_type[i]
                if ty == CH_GRAPHITE:
                    continue

                lrf = self._local_rod_factor(i)

                if ty == CH_FUEL:
                    if self.ch_removed[i]:
                        self.ch_pow[i] = self.ch_i[i] = self.ch_xe[i] = 0.0
                        inlet_r = 40 + min(230, st.power * 2.5)
                        dQ_r = -(NAT * 0.5) * max(0, self.ch_t[i] - inlet_r)
                        self.ch_t[i] = max(20, self.ch_t[i] + dQ_r * rate_cool)
                        self.ch_v[i] = max(0, min(1, (self.ch_t[i] - 285) / 90))
                        sum_t += self.ch_t[i]; n_ch += 1
                        sum_void += self.ch_v[i]
                        continue

                    xe_mult   = max(0, 1 - self.ch_xe[i] * XENON_WORTH)
                    void_coeff = (0.35 + (1 - st.flux) * 0.45) * diff["void_mult"]
                    v = self.ch_v[i]
                    void_boost = 1 + v * void_coeff + v * v * void_coeff * 1.5
                    raw = lrf * self._radial_imp(r, c) * void_boost * xe_mult
                    jitter = (random.random() - 0.5) * 0.015 * lrf
                    self.ch_pow[i] = max(0, raw + jitter)
                    sum_raw += raw; n_fuel += 1

                    inlet  = 40 + min(230, st.power * 2.5)
                    heatIn = self.ch_pow[i] * (st.power / 100) * HEAT_K
                    coolEf = (flow / 100) * COOL_C + NAT
                    dQ     = heatIn - coolEf * max(0, self.ch_t[i] - inlet)
                    rate   = rate_heat if dQ > 0 else rate_cool
                    self.ch_t[i] = max(20, self.ch_t[i] + dQ * rate)
                    self.ch_v[i] = max(0, min(1, (self.ch_t[i] - 285) / 90))
                    sum_t += self.ch_t[i]; n_ch += 1
                    sum_void += self.ch_v[i]

                elif ty in (CH_TSENSOR, CH_FSENSOR):
                    xe_mult2 = max(0, 1 - self.ch_xe[i] * XENON_WORTH)
                    inlet2   = 40 + min(230, st.power * 2.5)
                    lp2      = (lrf * self._radial_imp(r, c) * xe_mult2) * (st.power / 100) * HEAT_K
                    cool2    = (flow / 100) * COOL_C + NAT
                    dQ2      = lp2 - cool2 * max(0, self.ch_t[i] - inlet2)
                    rate2    = rate_heat if dQ2 > 0 else rate_cool
                    self.ch_t[i] = max(20, self.ch_t[i] + dQ2 * rate2)
                    self.ch_v[i] = max(0, min(1, (self.ch_t[i] - 285) / 90))
                    sum_t += self.ch_t[i]; n_ch += 1

                elif ty == CH_ROD:
                    self.ch_t[i] += (self._avg_neighbour_t(r, c) - self.ch_t[i]) * 0.03

        # ── Pass 2: graphite heat diffusion ─────────────────────────────
        for r in range(ROWS):
            for c in range(COLS):
                i = r * COLS + c
                ty = self.ch_type[i]
                if ty in (CH_GRAPHITE, CH_ROD):
                    continue
                ns, nn = 0.0, 0
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < ROWS and 0 <= cc < COLS:
                        ii = rr * COLS + cc
                        if self.ch_type[ii] != CH_GRAPHITE:
                            ns += self.ch_t[ii]; nn += 1
                if nn > 0:
                    self.ch_t[i] += (ns / nn - self.ch_t[i]) * 0.03

        # ── Global state averages ─────────────────────────────────────
        avg_raw = sum_raw / n_fuel if n_fuel > 0 else 0.0
        st.flux += (avg_raw - st.flux) * diff["flux_lag"]
        st.flux  = max(0, min(1.25, st.flux))
        st.power = st.flux * 100
        st.temp  = sum_t / n_ch if n_ch > 0 else 20.0
        st.void_fraction = sum_void / n_ch if n_ch > 0 else 0.0
        st.pressure = max(0.1, 6.5 + (st.temp - 285) * 0.042 + st.void_fraction * 2.0)

        # ── Pass 3: Iodine/Xenon two-pool ODE ───────────────────────────
        dt_comp = 0.15 * self._tc
        sub, dts = 4, dt_comp / 4
        sum_xe = 0.0; sum_iod = 0.0; n_xe = 0
        lI  = self._lambda_i
        lXe = self._lambda_xe
        gI  = self._gamma_i
        gXe = GAMMA_XE
        sXe = SIGMA_XE

        for i in range(N):
            ty = self.ch_type[i]
            if ty not in (CH_FUEL, CH_TSENSOR, CH_FSENSOR):
                continue
            if self.ch_removed[i]:
                self.ch_i[i] = self.ch_xe[i] = 0.0
                continue

            pwr = max(0, self.ch_pow[i]) if ty == CH_FUEL else 0.0

            for _ in range(sub):
                # Iodine
                I_prod  = gI * pwr
                I_decay = lI * self.ch_i[i]
                I_noise = self.ch_i[i] * 0.003 * (random.random() - random.random())
                self.ch_i[i] = max(0, self.ch_i[i] + (I_prod - I_decay) * dts + I_noise)

                # Xenon
                Xe_prod = gXe * pwr + lI * self.ch_i[i]
                Xe_loss = (lXe + sXe * pwr) * self.ch_xe[i]
                Xe_noise = self.ch_xe[i] * 0.003 * (random.random() - random.random())
                self.ch_xe[i] = max(0, self.ch_xe[i] + (Xe_prod - Xe_loss) * dts + Xe_noise)

            sum_xe  += self.ch_xe[i]
            sum_iod += self.ch_i[i]
            n_xe    += 1

        st.xenon  = sum_xe  / n_xe if n_xe > 0 else 0.0
        st.iodine = sum_iod / n_xe if n_xe > 0 else 0.0

        # ── Sensor readings ───────────────────────────────────────────
        for s in self.sensors:
            if s.fault:
                s.value = None
                continue
            ts, tn, ps, pn = 0.0, 0, 0.0, 0
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    rr = s.row + dr; cc = s.col + dc
                    if not (0 <= rr < ROWS and 0 <= cc < COLS):
                        continue
                    ii = rr * COLS + cc
                    if self.ch_type[ii] == CH_FUEL:
                        ts += self.ch_t[ii]; tn += 1
                        ps += self.ch_pow[ii]; pn += 1
            if s.type == "T":
                s.value = ts / tn if tn > 0 else self.ch_t[s.idx]
            else:
                s.value = ps / pn if pn > 0 else 0.0

        # Max channel temperature
        st.max_chan_t = max((self.ch_t[i] for i in range(N)
                             if self.ch_type[i] == CH_FUEL), default=20.0)

        # ── Alarms ────────────────────────────────────────────────────
        self._check_alarm("hipower",  st.power > 88)
        self._check_alarm("hitemp",   st.max_chan_t > 310)
        self._check_alarm("hipress",  st.pressure > 8.0)
        self._check_alarm("locool",   flow < 35)
        self._check_alarm("void",     st.void_fraction > 0.20)
        self._check_alarm("xenonpit", st.xenon > 0.30)
        self._check_alarm("scram",    st.scramming or st.scram_done)
        self._check_alarm("damage",   st.meltdown)

        any_pump_down = any(not p.on or p.fault for p in self.pumps)
        if any_pump_down and not st.alarms.get("pumptrip", False):
            st.alarms["pumptrip"] = True
            self._log("ALARM: PUMP TRIP", "danger")
        elif not any_pump_down and st.alarms.get("pumptrip", False):
            st.alarms["pumptrip"] = False
            self._log("CLEARED: PUMP TRIP", "info")

        # ── Meltdown triggers ─────────────────────────────────────────
        if not st.scramming and not st.scram_done and not st.meltdown:
            if st.power > 112:
                st.meltdown = True
                self._log("PROMPT CRITICALITY — RBMK POWER EXCURSION — CORE DESTROYED", "danger")
            if st.max_chan_t > 420:
                st.meltdown = True
                self._log("FUEL CHANNEL TEMP EXCEEDED 420°C — ZIRCALOY OXIDATION — CORE DAMAGE", "danger")

        # Notify callbacks
        for cb in self._on_tick_callbacks:
            try:
                cb()
            except Exception as e:
                log.error("tick callback error: %s", e)

    # ──────────────────────────────────────────────────────────────────
    # Control commands (called from WebSocket handler or Modbus write)
    # ──────────────────────────────────────────────────────────────────

    def cmd_scram(self):
        if self.state.scram_done or self.state.scramming or self.state.meltdown:
            return
        self.state.scramming = True
        self._log("AZ-5 BUTTON PRESSED — EMERGENCY ROD INSERTION", "danger")

    def cmd_reset(self):
        st = self.state
        st.power = 0; st.temp = 40; st.pressure = 0.3; st.flux = 0
        st.xenon = 0; st.iodine = 0; st.void_fraction = 0; st.max_chan_t = 40
        st.scramming = False; st.scram_done = False; st.meltdown = False; st.frame = 0
        st.alarms = {k: False for k, _ in ALARM_DEFS}
        for r in self.rods:
            r.pos = r.target = 0; r.selected = False; r.mode = "auto"
        for s in self.sensors:
            s.value = 40.0 if s.type == "T" else 0.0
            s.fault = False
        for p in self.pumps:
            p.on = True; p.speed = 80; p.fault = False
        self.ch_t       = [40.0] * N
        self.ch_pow     = [0.0]  * N
        self.ch_v       = [0.0]  * N
        self.ch_i       = [0.0]  * N
        self.ch_xe      = [0.0]  * N
        self.ch_removed = [False] * N
        self._log("REACTOR RESET — COLD SHUTDOWN — ALL SYSTEMS NOMINAL", "info")

    def cmd_set_rod_target(self, rod_id: str, target: float):
        """Set a rod's target position. Ignored if rod is in manual/fault mode."""
        rod = next((r for r in self.rods if r.id == rod_id), None)
        if not rod:
            return False, f"Rod {rod_id} not found"
        if rod.mode in ("manual", "fault"):
            return False, f"Rod {rod_id} is in {rod.mode} mode — write rejected"
        if self.state.scramming:
            # Write is NOT rejected during SCRAM — per requirement 4
            pass
        rod.target = max(0, min(100, float(target)))
        return True, "ok"

    def cmd_set_rod_pos(self, rod_id: str, pos: float):
        """Directly set a manual-mode rod position from the UI panel.
        Unlike cmd_set_rod_target, this works for manual-mode rods.
        Sets both pos and target so the physics tick does not revert it."""
        rod = next((r for r in self.rods if r.id == rod_id), None)
        if not rod:
            return False, f"Rod {rod_id} not found"
        if rod.mode == "fault":
            return False, f"Rod {rod_id} is in fault mode"
        if self.state.scramming:
            return False, "SCRAM in progress"
        p = max(0.0, min(100.0, float(pos)))
        rod.pos    = p
        rod.target = p
        return True, "ok"

    def cmd_set_pump(self, pump_id: int, on: bool):
        if pump_id < 0 or pump_id >= len(self.pumps):
            return False, "Invalid pump id"
        p = self.pumps[pump_id]
        if p.fault:
            return False, f"MCP-{pump_id+1} is in fault — write rejected"
        p.on = on
        return True, "ok"

    def cmd_set_pump_speed(self, pump_id: int, speed: float):
        if pump_id < 0 or pump_id >= len(self.pumps):
            return False, "Invalid pump id"
        p = self.pumps[pump_id]
        if p.fault:
            return False, f"MCP-{pump_id+1} is in fault — write rejected"
        p.speed = max(20, min(100, float(speed)))
        return True, "ok"

    def cmd_set_rod_mode(self, rod_id: str, mode: str):
        """Instructor only."""
        rod = next((r for r in self.rods if r.id == rod_id), None)
        if not rod:
            return False, "Not found"
        rod.mode = mode
        if mode != "auto":
            rod.target = rod.pos
        return True, "ok"

    def cmd_set_sensor_fault(self, label: str, fault: bool):
        """Instructor only."""
        s = next((x for x in self.sensors if x.label == label), None)
        if not s:
            return False, "Not found"
        s.fault = fault
        if fault:
            s.value = None
        return True, "ok"

    def cmd_set_pump_fault(self, pump_id: int, fault: bool):
        """Instructor only."""
        if pump_id < 0 or pump_id >= len(self.pumps):
            return False, "Invalid pump id"
        self.pumps[pump_id].fault = fault
        return True, "ok"

    def cmd_set_fuel(self, ch_idx: int, removed: bool):
        """Instructor only."""
        if not (0 <= ch_idx < N):
            return False, "Invalid index"
        if self.ch_type[ch_idx] != CH_FUEL:
            return False, "Not a fuel channel"
        self.ch_removed[ch_idx] = removed
        return True, "ok"

    def set_difficulty(self, name: str):
        self._diff = DIFF_PRESETS.get(name, DIFF_PRESETS["normal"]).copy()
        self._tc = self._diff["tc"]
        self._lambda_i  = _lambda_I(self._tc)
        self._lambda_xe = _lambda_Xe(self._tc)
        self._gamma_i   = self._lambda_i

    # ──────────────────────────────────────────────────────────────────
    # Async run loop
    # ──────────────────────────────────────────────────────────────────

    def on_tick(self, cb: Callable):
        self._on_tick_callbacks.append(cb)

    def on_log(self, cb: Callable):
        self._on_log_callbacks.append(cb)

    async def run(self):
        self._running = True
        self._log("RBMK-1000 CONTROL SYSTEM ONLINE — BACKEND PHYSICS ENGINE", "info")
        self._log(f"COLD SHUTDOWN — {len(self.rods)} CTRL RODS — "
                  f"{sum(1 for s in self.sensors if s.type=='T')} TEMP + "
                  f"{sum(1 for s in self.sensors if s.type=='F')} FLUX SENSORS", "info")
        while self._running:
            if self.sim_speed > 0:
                self.tick()
                sleep_s = self.TICK_INTERVAL / self.sim_speed
            else:
                sleep_s = self.TICK_INTERVAL  # paused: still sleep, don't tick
            await asyncio.sleep(sleep_s)

    def stop(self):
        self._running = False

    # ──────────────────────────────────────────────────────────────────
    # Serialisation — full state dict for WebSocket broadcast
    # ──────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        st = self.state
        return {
            "frame":          st.frame,
            "power":          round(st.power, 2),
            "temp":           round(st.temp, 1),
            "pressure":       round(st.pressure, 3),
            "flux":           round(st.flux, 4),
            "xenon":          round(st.xenon, 4),
            "iodine":         round(st.iodine, 4),
            "void_fraction":  round(st.void_fraction, 4),
            "max_chan_t":      round(st.max_chan_t, 1),
            "scramming":      st.scramming,
            "scram_done":     st.scram_done,
            "meltdown":       st.meltdown,
            "total_flow":     round(self.total_flow(), 1),
            "output_mw":      round(self.current_output_mw(), 1),
            "turbine_eff":    round(self.turbine_efficiency(), 2),
            "alarms":         dict(st.alarms),
            "sim_speed":      self.sim_speed,
            "rods": [
                {
                    "id":       r.id,
                    "idx":      r.idx,
                    "pos":      round(r.pos, 1),
                    "target":   round(r.target, 1),
                    "mode":     r.mode,
                    "selected": r.selected,
                    "at_min":   r.pos < 1.0,
                    "at_max":   r.pos > 99.0,
                } for r in self.rods
            ],
            "sensors": [
                {
                    "label": s.label,
                    "idx":   s.idx,
                    "type":  s.type,
                    "value": round(s.value, 3) if s.value is not None else None,
                    "fault": s.fault,
                    "row":   s.row,
                    "col":   s.col,
                } for s in self.sensors
            ],
            "pumps": [
                {
                    "id":    p.id,
                    "name":  p.name,
                    "on":    p.on,
                    "speed": p.speed,
                    "fault": p.fault,
                } for p in self.pumps
            ],
            # Per-channel data (compact arrays for canvas rendering)
            "ch_type":    self.ch_type,
            "ch_pow":     [round(v, 4) for v in self.ch_pow],
            "ch_t":       [round(v, 1) for v in self.ch_t],
            "ch_v":       [round(v, 4) for v in self.ch_v],
            "ch_xe":      [round(v, 4) for v in self.ch_xe],
            "ch_removed": [int(v) for v in self.ch_removed],
            "log":        list(st.log_buffer[-20:]),
        }
