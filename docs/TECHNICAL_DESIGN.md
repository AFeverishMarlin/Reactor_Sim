# RBMK-1000 Reactor Training Simulator — Technical Design Document

Version 1.0 | Control Systems Training Platform

---

## 1. System Overview

The RBMK-1000 Reactor Training Simulator is a software-based process simulation platform designed to teach first-year apprentices fundamental PLC concepts — PID control, analogue I/O, discrete I/O, and process interlocks — using a nuclear reactor as the training process. The reactor physics are simplified for educational purposes but retain enough realism to demonstrate genuine process control challenges including thermal lag, positive feedback loops, and xenon poisoning.

The system consists of three integrated layers:

1. **Physics Engine** (Python) — simulates reactor behaviour at 150ms per tick
2. **Communication Server** (Python/FastAPI) — exposes process variables via Modbus TCP and a WebSocket API
3. **Visualisation Interface** (Browser) — provides real-time core visualisation for instructors and apprentices

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Training Station PC                    │
│                                                          │
│  ┌─────────────┐  WebSocket  ┌──────────────────────┐   │
│  │  Browser UI │◄───────────►│    Python Backend     │   │
│  │ (Instructor │             │                      │   │
│  │  & Debug)   │             │  ┌────────────────┐  │   │
│  └─────────────┘             │  │ Physics Engine │  │   │
│                              │  │  (physics.py)  │  │   │
│                              │  └───────┬────────┘  │   │
│                              │          │            │   │
│                              │  ┌───────▼────────┐  │   │
│                              │  │   IO Bridge    │  │   │
│                              │  │ (io_bridge.py) │  │   │
│                              │  └───────┬────────┘  │   │
│                              │          │            │   │
│                              │  ┌───────▼────────┐  │   │
│                              │  │  Modbus TCP    │  │   │
│                              │  │    Server      │  │   │
│                              │  └───────┬────────┘  │   │
│                              └──────────┼────────────┘   │
│                                         │                │
└─────────────────────────────────────────┼────────────────┘
                                          │ Ethernet / LAN
                                          │
                               ┌──────────▼──────────┐
                               │    PLC Hardware      │
                               │  (Siemens / AB /     │
                               │   Schneider / etc.)  │
                               └─────────────────────┘
```

### Component Responsibilities

| Component | File | Responsibility |
|---|---|---|
| Physics Engine | `backend/physics.py` | All reactor simulation — neutronics, thermal, xenon, void |
| IO Bridge | `backend/io_bridge.py` | Modbus TCP server, register mapping, PLC write handling |
| Scenario Engine | `backend/scenario_engine.py` | Scripted and random fault injection |
| Config Manager | `backend/config_manager.py` | JSON config load/save, IO address management |
| Persistence | `backend/persistence.py` | SQLite — scores, settings, scenario metadata |
| API Server | `backend/main.py` | FastAPI WebSocket + REST, browser comms |

---

## 3. Physics Engine

### 3.1 Reactor Grid

The simulated reactor core is an 18×22 channel lattice (396 positions). Every even-sum position `(r+c) % 2 == 0` is an active channel; odd-sum positions are graphite moderator blocks.

Active channel types:
- **Type 1**: Fuel channel (fission, heat generation)
- **Type 2**: Control rod guide tube
- **Type 3**: Temperature sensor (occupies fuel position)
- **Type 4**: Flux sensor (occupies fuel position)

### 3.2 Neutron Flux Model

Each fuel channel computes its local raw flux independently:

```
rawFlux = localRodFactor × radialImportance × voidBoost × xenonMult
```

**localRodFactor**: Weighted average of nearby rod withdrawals, using spatial weights `w = exp(-distance/3.2)` for all rods within 9 cell-widths. Each channel "sees" a different effective rod environment.

**radialImportance**: `max(0.70, cos(d × 0.55))` where d is the normalised distance from core centre. Ranges from 1.0 at centre to ~0.72 at edge — the graphite reflector flattens the profile compared to a water-moderated reactor.

**voidBoost**: `1 + v×k + v²×k×1.5` where v is local void fraction and `k = 0.35 + (1−flux)×0.45`. The quadratic term makes the positive void coefficient disproportionately dangerous at high void fractions. At low power (low flux), k increases, making the reactor less stable — historically accurate for the RBMK design.

**xenonMult**: `max(0, 1 − Xe × 1.2)` — xenon absorbs neutrons. At steady state Xe≈0.15, giving 18% suppression. During an iodine pit, Xe can reach 0.50, giving 60% suppression.

Global reactor power is the flux-lagged average of all channel raw fluxes:
```
flux += (avgRaw − flux) × fluxLag   # fluxLag = 0.040 at normal difficulty
power = flux × 100
```

### 3.3 Thermal Model

Each channel tracks its own temperature using an explicit heat balance:

```
inlet   = 40 + min(230, power × 2.5)       # coolant preheating
heatIn  = chPow × (power/100) × 48         # fission heat
coolEff = (flow/100) × 0.45 + 0.015        # 0.015 = natural convection floor
dQ      = heatIn − coolEff × max(0, T − inlet)

rate    = rate_heat if dQ > 0 else rate_cool   # asymmetric inertia
T_new   = max(20, T + dQ × rate)
```

Thermal rates by difficulty:
- `rate_heat = 0.015 + (flow/100) × 0.040` (base + forced-convection bonus)
- `rate_cool = 0.003 + (flow/100) × 0.050` (very slow without pumps)

This asymmetry is physically motivated: water has high thermal mass and loses heat slowly by natural convection alone. With no pump flow, a channel at 300°C takes approximately 4 hours to cool to ambient.

### 3.4 Void Fraction

Void (steam) fraction is computed per channel from its temperature:

```
voidFraction = clamp((T − 285) / 90, 0, 1)
```

Boiling onset: 285°C. Full void: 375°C. At nominal operating conditions (T≈280°C), void fraction is near zero. Void begins appearing above 80% reactor power or when coolant flow drops below ~50%.

### 3.5 Pressure

Steam drum pressure is derived from core-average temperature and void fraction:

```
pressure = max(0.1, 6.5 + (avgTemp − 285) × 0.042 + voidFraction × 2.0)  [MPa]
```

Nominal operating pressure: ~6.5 MPa. High pressure alarm: 8.0 MPa.

### 3.6 Xenon-135 / Iodine-135 Two-Pool Model

Per-channel ODEs solved with 4 Euler sub-steps per physics tick:

```
dI/dt  =  γI × pwr  −  λI × I
dXe/dt =  γXe × pwr + λI × I  −  (λXe + σXe × pwr) × Xe
```

Constants (at normal difficulty, TC=40):
| Parameter | Value | Description |
|---|---|---|
| TC | 40 | Time compression factor |
| λI | 1.167×10⁻³ /comp-s | I-135 decay constant |
| λXe | 8.37×10⁻⁴ /comp-s | Xe-135 decay constant |
| σXe | 0.040 | Neutron burnout cross-section |
| γI | = λI | I-135 fission yield (keeps I_eq = pwr) |
| γXe | 0.00496 | Xe-135 direct fission yield |
| XENON_WORTH | 1.2 | Reactivity suppression factor |

Real half-lives at TC=40: I-135 ≈ 15s, Xe-135 ≈ 21s.

**Iodine pit sequence** after a SCRAM from full power:
1. pwr → 0: I production stops, Xe burnout stops
2. I continues decaying → feeds Xe production
3. Xe peaks at ~0.50 after ~22s real time
4. Maximum suppression: 60% — power recovery to >50% impossible even with all rods withdrawn
5. Xe decays naturally over ~30-40s, recovery gradually becomes possible

### 3.7 Meltdown Triggers

Two independent conditions trigger core damage:
1. **Prompt criticality**: power exceeds 112% (positive void coefficient runaway)
2. **Channel overtemperature**: any fuel channel exceeds 420°C (Zircaloy oxidation)

---

## 4. Modbus TCP Register Map

All addresses are **0-based** unless noted. Add 1 for tools that use 1-based addressing. All addresses are configurable in `config/io_map.json`.

### 4.1 Coils (Function Codes 01/05) — Read/Write Discrete

| Name | Default Address | Description |
|---|---|---|
| scram_command | 0 | Write 1 to trigger AZ-5 SCRAM |
| pump_1_run | 10 | MCP-1 run command |
| pump_2_run | 11 | MCP-2 run command |
| pump_3_run | 12 | MCP-3 run command |
| pump_4_run | 13 | MCP-4 run command |

**Write protection**: Pump writes are rejected if the pump is in FAULT state. Rod setpoint writes are rejected if the rod is in MANUAL or FAULT mode. The Modbus server rejects the write silently (the register reverts to the physics value on next update). Write-protection is NOT lifted during a SCRAM — the SCRAM command proceeds independently.

### 4.2 Discrete Inputs (Function Code 02) — Read Only

| Name | Default Address | Description |
|---|---|---|
| reactor_running | 0 | 1 if power > 5% |
| scram_active | 1 | SCRAM in progress or complete |
| meltdown | 2 | Core damage active |
| alarm_hipower | 10 | Power > 88% |
| alarm_hitemp | 11 | Max channel > 310°C |
| alarm_void | 12 | Void > 20% |
| alarm_locool | 13 | Flow < 35% |
| alarm_pumptrip | 14 | Any pump off or faulted |
| alarm_scram | 15 | SCRAM alarm |
| alarm_damage | 16 | Core damage |
| alarm_hipress | 17 | Pressure > 8.0 MPa |
| alarm_xenonpit | 18 | Xenon > 0.30 (restart difficult) |
| pump_1_running | 30 | MCP-1 running (on AND not faulted) |
| pump_1_fault | 31 | MCP-1 in fault state |
| pump_2_running | 33 | MCP-2 running |
| pump_2_fault | 34 | MCP-2 fault |
| pump_3_running | 36 | MCP-3 running |
| pump_3_fault | 37 | MCP-3 fault |
| pump_4_running | 39 | MCP-4 running |
| pump_4_fault | 40 | MCP-4 fault |
| CR-01 at_min | 100 | CR-01 fully inserted (pos < 1%) |
| CR-01 at_max | 101 | CR-01 fully withdrawn (pos > 99%) |
| CR-01 manual | 102 | CR-01 in MANUAL mode |
| CR-01 fault | 103 | CR-01 in FAULT mode |
| CR-02 at_min | 104 | ... (4 bits per rod, 30 rods total) |
| ... | ... | CR-30 uses addresses 216–219 |
| Fuel ch. 1 | 300 | 1=fuel installed, 0=empty |
| ... | ... | One bit per active channel |

### 4.3 Input Registers (Function Code 04) — Read Only Analogue

All values are 0–32767 mapped from engineering units as configured in io_map.json.

**Plant-wide (default base address 0):**

| Name | Addr | Eng. Min | Eng. Max | Description |
|---|---|---|---|---|
| reactor_power | 0 | 0% | 100% | Reactor thermal power |
| output_mw | 1 | 0 MWe | 1100 MWe | Electrical output |
| steam_pressure | 2 | 0 MPa | 15 MPa | Steam drum pressure |
| total_flow | 3 | 0% | 100% | Total coolant flow |
| void_fraction | 4 | 0% | 100% | Core void fraction |
| avg_core_temp | 5 | 0°C | 500°C | Average core temperature |
| max_chan_temp | 6 | 0°C | 500°C | Hottest fuel channel |
| turbine_eff | 7 | 0% | 35% | Turbine efficiency |
| avg_iodine | 8 | 0 | 1 | Average Iodine-135 (normalised) |
| avg_xenon | 9 | 0 | 1 | Average Xenon-135 (normalised) |
| target_mw | 10 | 0 MWe | 1100 MWe | Grid demand target (0 when not in a shift game mode) |

**Temperature sensors (default base 100):** TS-01 → address 100, TS-02 → 101, etc.
Range: 0–500°C → 0–32767. Value 32767 indicates sensor FAULT/no data.

**Flux sensors (default base 150):** FS-01 → address 150, FS-02 → 151, etc.
Range: 0–1.5 → 0–32767. Value 32767 indicates sensor FAULT/no data.

**Control rod positions (default base 200):** CR-01 → address 200, CR-02 → 201, etc.
Range: 0–100% → 0–32767.

**Pump speeds (default base 250):** MCP-1 → 250, MCP-2 → 251, etc.
Range: 0–100% → 0–32767. Returns 0 if pump is in FAULT state.

### 4.4 Holding Registers (Function Codes 03/06/16) — Read/Write Analogue

**Control rod setpoints (default base 0):** CR-01 → address 0, CR-02 → 1, etc.
Range: 0–32767 → 0–100%. Write is rejected (silently reverted) if rod is in MANUAL or FAULT mode.

**Pump speed setpoints (default base 100):** MCP-1 → 100, MCP-2 → 101, etc.
Range: 0–32767 → 0–100%. Clamped to 20–100% internally (pumps have minimum speed). Write rejected if pump is in FAULT state.

### 4.5 Scaling

All analogue registers use integer scaling. The scale is defined in `config/io_map.json` per register group. Two scaling methods are supported:

**Method 1 — Engineering range interpolation** (default):
```
raw = round((value - scale_min) / (scale_max - scale_min) × 32767)
value = scale_min + (raw / 32767) × (scale_max - scale_min)
```

**Method 2 — Direct scale factor**:
```json
"scale_factor": 100
```
```
raw = round(value × scale_factor)
value = raw / scale_factor
```

The `scale_factor` field in io_map.json overrides the min/max interpolation when present. Trainers can specify any scaling to match their PLC's expected range (e.g., 4000–20000 for 4–20mA representation).

---

## 5. Configuration Files

### 5.1 `config/network.json`

Controls server binding addresses and ports.

```json
{
  "modbus_tcp": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 502,
    "unit_id": 1
  },
  "opc_ua": {
    "enabled": false,
    "endpoint": "opc.tcp://0.0.0.0:4840/reactor"
  },
  "web_ui": {
    "host": "0.0.0.0",
    "port": 8080
  }
}
```

Note: Port 502 is the standard Modbus port and requires administrator/root privileges on most operating systems. If running without admin rights, change the port to 5020 or higher and configure the PLC to connect to that port.

### 5.2 `config/io_map.json`

Defines all Modbus register addresses and scaling. Every address is adjustable. See the full file for all available parameters. After editing, restart the simulator for changes to take effect.

### 5.3 `config/scenarios/*.json`

Scenario files. See Section 7 for format details.

---

## 6. Database

SQLite database at `data/simulator.db`. Three tables:

**scores** — Last 10 scores per mode/difficulty combination (40 entries max).
Columns: id, timestamp, mode, difficulty, shift_duration, score, grade, on_target_pct, alarms_fired, scrams, mwh_generated.

**settings** — Key-value store for configuration data. Persists network config across sessions.

**scenario_meta** — Metadata index for scenario files (name, description, type). The actual scenario content lives in the JSON files.

The database is created automatically on first run. It can be safely deleted to reset all scores and settings.

---

## 7. Scenario Format

### 7.1 Scripted Scenario

```json
{
  "name": "Scenario Name",
  "type": "scripted",
  "description": "What this teaches",

  "initial_state": {
    "rod_positions_pct": 65,
    "pump_speeds_pct": 80
  },

  "events": [
    {
      "trigger": {"type": "time", "seconds": 30},
      "action":  {"type": "note", "message": "Instructor note text"}
    },
    {
      "trigger": {"type": "condition", "variable": "xenon", "operator": ">", "value": 0.30},
      "action":  {"type": "fault", "target": "pump", "id": 2, "fault": true}
    }
  ]
}
```

**Trigger types:**
- `{"type": "time", "seconds": N}` — fires N seconds after scenario start
- `{"type": "condition", "variable": "V", "operator": "OP", "value": X}` — fires when physics variable meets condition

**Condition variables:** `power`, `temp`, `pressure`, `xenon`, `iodine`, `void_fraction`, `max_chan_t`, `total_flow`, `output_mw`

**Operators:** `>`, `<`, `>=`, `<=`, `==`

**Action types:**
- `{"type": "note", "message": "text"}` — logs a message to the operational log
- `{"type": "fault", "target": "pump", "id": N, "fault": true/false}` — inject/clear a pump trip (id: 1–4)
- `{"type": "fault", "target": "rod", "id": N, "fault": true/false}` — inject/clear a rod fault (id: 1–30)
- `{"type": "fault", "target": "sensor_T", "id": N, "fault": true/false}` — T-sensor fault (id: 1–30)
- `{"type": "fault", "target": "sensor_F", "id": N, "fault": true/false}` — F-sensor fault (id: 1–20)
- `{"type": "set_rod", "id": "CR-01", "target": 75.0}` — set rod target position
- `{"type": "set_pump", "id": 0, "on": false}` — set pump state (id: 0–3)
- `{"type": "scram"}` — trigger AZ-5 SCRAM
- `{"type": "set_rod_mode", "id": "CR-01", "mode": "fault"}` — set rod mode

### 7.2 Random Scenario

```json
{
  "name": "Random Faults",
  "type": "random",
  "description": "...",
  "min_interval_s": 60,
  "max_interval_s": 180,
  "max_concurrent_faults": 2,
  "fault_weights": {
    "pump_trip":      0.35,
    "rod_fault":      0.25,
    "sensor_fault_T": 0.20,
    "sensor_fault_F": 0.15,
    "pump_fault":     0.05
  }
}
```

Weights are relative probabilities — they do not need to sum to 1. Set a weight to 0 to disable that fault type entirely.

---

## 8. Difficulty System

| Level | Xenon TC | Thermal speed | Void coefficient | Fault interval |
|---|---|---|---|---|
| Easy | 20× | 0.4× baseline | 0.55× | 75–180s |
| Normal | 40× | 1.0× baseline | 1.0× | 30–90s |
| Hard | 80× | 2.0× baseline | 1.5× | 15–38s |
| Extreme | 160× | 4.0× baseline | 2.2× | 9–21s |

Thermal speed affects how quickly the reactor responds to control actions. At Extreme, a pump failure can cause meltdown within 10 seconds.

---

## 9. WebSocket API

The browser communicates with the backend via WebSocket at `ws://host:port/ws`.

**Server → Client** (after every tick): Full state JSON object (see physics.py `to_dict()`).

**Client → Server** (commands):

```json
{"cmd": "scram"}
{"cmd": "reset"}
{"cmd": "set_sim_speed", "speed": 0.5}
{"cmd": "set_rod_target", "rod_id": "CR-01", "target": 75.0}
{"cmd": "set_rod_mode",   "rod_id": "CR-01", "mode": "manual"}
{"cmd": "set_pump",       "pump_id": 0, "on": false}
{"cmd": "set_pump_speed", "pump_id": 0, "speed": 70.0}
{"cmd": "set_pump_fault", "pump_id": 0, "fault": true}
{"cmd": "set_sensor_fault", "label": "TS-01", "fault": true}
{"cmd": "set_fuel", "ch_idx": 44, "removed": true}
{"cmd": "set_difficulty", "difficulty": "hard"}
{"cmd": "scenario_load",  "filename": "iodine_pit_recovery.json"}
{"cmd": "scenario_start"}
{"cmd": "scenario_stop"}
{"cmd": "save_score", "data": { ... }}
```

---

## 10. REST API

Base URL: `http://host:port/api/`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/status` | Server status and Modbus info |
| GET | `/api/state` | Current reactor state (same as WebSocket tick) |
| GET | `/api/config/network` | Current network config |
| POST | `/api/config/network` | Save network config |
| GET | `/api/config/iomap` | Current IO map |
| POST | `/api/config/iomap` | Save IO map |
| GET | `/api/scores` | All scores (leaderboard) |
| GET | `/api/scenarios` | List available scenarios |
| GET | `/api/scenarios/{filename}` | Get scenario content |
| POST | `/api/scenarios/{filename}` | Save scenario |
| DELETE | `/api/scenarios/{filename}` | Delete scenario |
