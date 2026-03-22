# Modbus Training Client — Reference Guide

`modbus_client.py` — Closed-loop PLC controller for the RBMK-1000 simulator

---

## Overview

This client demonstrates real Modbus TCP communication with the reactor simulator.
It implements a two-layer control system — a PI power controller that drives all
30 control rods collectively toward a MWe setpoint, and a spatial P controller that
trims individual rods using proximity-weighted T-sensor readings to flatten the
radial flux profile and suppress hot spots.

This is exactly how a DCS (Distributed Control System) or PLC program would work
in a real plant — the Modbus registers are the only interface; the client has no
direct access to the simulator's internal state.

---

## Quick Start

1. Launch the simulator: double-click `start.bat`
2. Select a mode and start the reactor manually via the browser UI
3. Bring the reactor to approximately 30% power with rods at ~50%
4. Open a second terminal and run the client:

```
python modbus_client.py --setpoint 600
```

The client connects, reads all process values via Modbus, and begins
controlling the reactor toward 600 MWe.

---

## Command Line Options

```
python modbus_client.py [options]

  --host HOST        Modbus server IP (default: 127.0.0.1)
  --port PORT        Modbus TCP port  (default: 502)
  --unit UNIT        Modbus unit ID   (default: 1)
  --setpoint MWe     Initial power setpoint in MWe (default: 600)
  --mode MODE        AUTO or MANUAL (default: AUTO)
  --no-tui           Use plain print mode instead of curses TUI
```

**Examples:**
```bash
# Connect to remote training PC, aim for 700 MWe
python modbus_client.py --host 192.168.10.1 --setpoint 700

# Start in manual mode (read-only, no outputs written)
python modbus_client.py --mode MANUAL

# Run without TUI (useful in some terminals)
python modbus_client.py --no-tui --setpoint 500
```

---

## TUI Layout

```
─────────────── RBMK-1000 MODBUS TRAINING CLIENT ────────── CONNECTED  AUTO
                                                                              
── PROCESS VALUES ──────────────  ── CONTROL RODS (setpoint → actual) ──────
  Setpoint  :    600 MWe         R01:  68→ 67  R02:  64→ 65  ...
  Output    :    594 MWe
  Error     :     -6 MWe         ── T-SENSOR GRID (°C) — hot=red, cool=cyan
  Power     :   58.2 %           T01: 282.3  T02: 295.1  T03: 278.8  ...
  Avg Temp  :   284 °C
  Max Temp  :   298 °C
  Flow      :   77.5 %
  Pressure  :   6.52 MPa
  Void      :    2.1 %
  Xenon     :  0.148
  Iodine    :  0.612
  Turbine   :   30.8 %

── PUMPS (setpoint / actual) ──
  MCP-1:  76.0% →  76.1%  [RUN]
  MCP-2:  77.5% →  77.3%  [RUN]
  MCP-3:  74.5% →  74.8%  [RUN]
  MCP-4:  76.5% →  76.4%  [RUN]

── ALARMS ──────────────────────
[HI POWER] [HI TEMP ] [HI VOID ]

─────────────────────────────────────────────────── EVENT LOG
  [14:32:01] Connected to 127.0.0.1:502 unit 1
  [14:32:03] Mode → AUTO
──────────────────────────────────────────────────────────────
 ↑/↓ or +/- : setpoint ±25MWe  |  a : AUTO  |  m : MANUAL  |  s : SCRAM  |  q : quit
```

**Colour coding:**
- Green — normal operating range
- Yellow — caution / approaching limit
- Red — alarm condition / limit exceeded
- Cyan — informational / label text
- Magenta — xenon/iodine values

---

## Control Algorithm

### 1. Safety Interlocks (checked every cycle, before any outputs)

Hard limits — client sends AZ-5 SCRAM and disables outputs if:

| Parameter | Limit |
|---|---|
| Reactor thermal power | > 92% |
| Maximum channel temperature | > 370°C |
| Core void fraction | > 35% |
| Steam drum pressure | > 9.5 MPa |

### 2. Coolant Control

All four pumps are controlled individually to demonstrate per-pump Modbus writes.
Each pump gets a slightly different speed offset (±1.5%) around the calculated target.

Target flow calculation:
```
flow_target = 70 + setpoint_mw × 0.025
            + max(0, avg_temp - 290) × 0.20   ← temperature boost
            + max(0, void_pct - 10) × 1.50    ← void boost
```

At 600 MWe setpoint, nominal flow ≈ 85%. The temperature and void terms cause
automatic flow increase as thermal conditions worsen.

**Pump Modbus writes:**
- Run commands: Coil addresses 10–13 (one per pump)
- Speed setpoints: Holding register addresses 100–103 (one per pump)

### 3. Power PI Controller

Controls average rod withdrawal across all 30 rods.

```
error = setpoint_mw - actual_output_mw
base_rod_withdrawal = Kp × error + Ki × ∫error dt
```

Default gains: Kp = 0.04 %rod/MWe, Ki = 0.006 %rod/(MWe·s)

The integral term handles xenon suppression automatically — during a xenon pit
(reactor difficult to bring to power), the integral accumulates and demands
higher rod withdrawal to compensate.

### 4. Spatial Flattening (individual rod control)

This is the interesting part for training. Each rod gets a trim applied on top
of the base PI setpoint based on the temperatures near it.

**Proximity weight calculation:**
For each of the 30 rods, a weighted sum of all 30 T-sensor readings is computed:
```
local_temp[rod] = Σ weight(rod, sensor) × T[sensor]
weight(rod, sensor) = exp(-distance / σ) / Σ_all_sensors(exp(-distance / σ))
```
where σ = 4.5 cells and distance is the Euclidean distance in the 18×22 grid.

**Trim calculation:**
```
trim[rod] = Kp_spatial × (local_temp[rod] - avg_temp)
```
- `trim > 0` (rod area hotter than average) → rod setpoint is **reduced** (insert more)
- `trim < 0` (rod area cooler than average) → rod setpoint is **increased** (pull out more)

Default: Kp_spatial = 0.06 %rod/°C, max trim ±12%

This means a rod in an area 20°C above the core average will be inserted 1.2% more
than the base setpoint — flattening the radial power profile over time.

**Rod Modbus writes:**
- Setpoints written to Holding Registers 0–29 (one per rod)
- Written in two batches of 16 registers each (FC16 multi-register write)

### 5. Rate Limiting

All rod setpoint changes are rate-limited to 3% per second to prevent
abrupt movements that could cause power transients. This mirrors the
real rod movement rate limit in the simulator physics.

---

## Modbus Register Reference (client perspective)

### Reads (every 100ms)

| FC | Address | Count | Content |
|---|---|---|---|
| 04 (IR) | 0 | 11 | Plant-wide: power, MWe, pressure, flow, void, temps, turbine, I/Xe, **target MWe (reg 10)** |
| 04 (IR) | 100 | 30 | T-sensor temperatures |
| 04 (IR) | 200 | 30 | CR actual positions |
| 04 (IR) | 250 | 4  | Pump actual speeds |
| 02 (DI) | 0 | 50 | Status bits, alarms, pump running |

### Writes (every 1s in AUTO mode)

| FC | Address | Count | Content |
|---|---|---|---|
| 05 (coil) | 10–13 | 1 each | Pump run commands |
| 16 (HR) | 100 | 4 | Pump speed setpoints |
| 16 (HR) | 0 | 16 | Rod setpoints CR-01..CR-16 |
| 16 (HR) | 16 | 14 | Rod setpoints CR-17..CR-30 |

Total Modbus traffic per control cycle: 5 reads + 7 writes.

---

## Tuning Guide

### The controller is oscillating (power hunting)

Reduce `POWER_KI` and `POWER_KP`. A good starting approach:
1. Set Ki = 0, increase Kp until the system responds (typically 0.02–0.06)
2. Add a small Ki (0.001–0.01) to eliminate steady-state offset

### The controller responds too slowly

Increase `POWER_KP`. At normal difficulty the process time constant is
approximately 8–15 seconds, so an aggressive Kp of 0.08+ is acceptable.

### Hot spots are not being suppressed

Increase `SPATIAL_KP` (try 0.10–0.15) or reduce `SPATIAL_SIGMA` (try 3.0) to
make each rod's response more localised to its immediate sensors.

### Rod positions are all identical (no spatial variation)

This means T-sensor temperatures are nearly uniform — the reactor is well-mixed.
This is normal at low power or with all pumps running at high speed. Variation
appears more obviously at 70%+ power.

---

## Training Exercises

1. **Watch the spatial correction develop** — Set setpoint to 700 MWe, wait for
   steady state, then declare a fuel assembly fault in the browser UI. Watch the
   nearby rods respond to the local temperature change.

2. **Setpoint ramp** — Start at 200 MWe, ramp to 800 MWe in 25 MWe steps using
   the ↑ key. Observe integral windup and how long the controller takes to settle
   at each step.

3. **Xenon pit** — SCRAM the reactor via the browser, wait 30 seconds (normal
   difficulty), then set a 600 MWe setpoint. The integral will accumulate as the
   controller fights xenon suppression.

4. **Manual vs AUTO** — Switch to MANUAL mode (m), adjust a rod manually in the
   browser, then switch back to AUTO. The controller will smoothly return all
   rods to the calculated setpoints.

5. **Pump fault injection** — In the browser, declare a pump fault while the
   client is in AUTO. The coolant controller will automatically increase remaining
   pump speeds to compensate for the flow loss.
