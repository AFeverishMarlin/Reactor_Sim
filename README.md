# RBMK-1000 Reactor Control — PLC Training Simulator

A realistic nuclear reactor process simulator built for training first-year apprentices in PLC fundamentals — analogue I/O, discrete I/O, PID control, alarm management, and Modbus TCP communications. The reactor physics are simplified for education but retain enough fidelity to demonstrate real process control challenges including thermal lag, positive void coefficient feedback, and xenon poisoning.

> **This is a training tool only.** It has no connection to any real nuclear installation.

---

## What it does

| Feature | Details |
|---|---|
| **18 × 22 RBMK-1000 core** | Spatial neutron flux, per-channel temperature, two-pool xenon/iodine model |
| **30 individually controllable rods** | Auto, Manual, and Fault modes; right-click context menu in the UI |
| **4 independent coolant pumps** | Individual speed control, fault injection, flow model |
| **Modbus TCP server** | All 30 rods + 4 pumps + alarms + sensors exposed as standard Modbus registers |
| **Configurable IO map** | Every register address and engineering-unit scaling is editable in the UI |
| **Game modes** | Free Operation, Dispatch Operator (hit MWe targets), Shift Supervisor (respond to faults) |
| **Scenario engine** | Scripted and randomised fault injection; JSON authoring |
| **Modbus control client** | Demo PLC client with PI power controller and spatial rod flattening |
| **Score history** | Last 10 scores per mode/difficulty stored in SQLite |

---

## Screenshots

```
┌─────────────────────────────────────────────────────────────────────────┐
│  V.I. LENIN NUCLEAR POWER PLANT — CHERNOBYL UNIT 4                      │
│  RBMK-1000 REACTOR CONTROL SYSTEM        ● CONNECTED  ■ MODBUS: PLC     │
├─────────────────┬───────────────────────┬───────────────────────────────┤
│  CORE LATTICE   │  PLANT INSTRUMENTATION │  CONTROL ROD OPERATIONS       │
│  18×22 channel  │  REACTOR POWER  58.2% │  12 RODS SELECTED             │
│  canvas with    │  AVG CORE TEMP  284°C  │  ▼ INSERT  ▲ WITHDRAW        │
│  live thermal   │  STEAM DRUM    6.52MPa │  ┌──────────────────────┐    │
│  and flux       │  TOTAL FLOW    77.5%  │  │ rod position slider  │    │
│  visualisation  │  OUTPUT MWe    594MWe  │  └──────────────────────┘    │
│                 │  TURBINE EFF   30.8%  │                               │
│                 ├───────────────────────┤  ⚠  AZ-5  SCRAM  ⚠           │
│  IN-CORE SENSOR │  MCP PUMP CONTROL     │                               │
│  ARRAY          │  MCP-1 76% [RUN]      │  OPERATING GUIDE              │
│  TS-01..TS-30   │  MCP-2 78% [RUN]      │  OPERATIONAL LOG              │
│  FS-01..FS-20   │  MCP-3 74% [RUN]      │                               │
│                 │  MCP-4 77% [RUN]      │                               │
│                 ├───────────────────────┤                               │
│                 │  ANNUNCIATOR ALARMS   │                               │
└─────────────────┴───────────────────────┴───────────────────────────────┘
```

---

## Requirements

- **Python 3.11 or later** — [python.org/downloads](https://python.org/downloads)
- **Windows 10/11** (64-bit) recommended, or Ubuntu 20.04+ / macOS 12+
- **Network** — a local Ethernet connection if connecting real PLC hardware

The simulator and Modbus client each use their own isolated Python virtual environment. No system-wide package installation is required.

---

## Installation

### 1 — Download

**Option A — Download the ZIP** (no Git required):

1. Click the green **Code** button on this page
2. Select **Download ZIP**
3. Extract to a folder, e.g. `C:\Training\reactor-simulator\`

**Option B — Clone with Git:**

```bash
git clone https://github.com/your-org/rbmk-reactor-simulator.git
cd rbmk-reactor-simulator
```

### 2 — Install Python

If Python 3.11+ is not already installed:

1. Download the installer from [python.org/downloads](https://python.org/downloads)
2. Run the installer
3. **Tick "Add Python to PATH"** on the first screen — this is required
4. Complete the installation

Verify in a terminal:
```
python --version
```
Expected: `Python 3.11.x` or later.

### 3 — First Launch

**Windows:**
```
Double-click start.bat
```

**Linux / macOS:**
```bash
bash start.sh
```

The launcher will:
- Detect your Python installation automatically (including Python 3.14 when newer versions are installed alongside older ones)
- Create a `venv/` virtual environment inside the project folder
- Install all dependencies (`fastapi`, `uvicorn`, `pymodbus`, etc.)
- Start the server
- Open the browser at `http://localhost:8080` automatically

Dependencies are only downloaded on the first launch. Subsequent starts are instant.

> **Trellix / McAfee antivirus:** If the antivirus blocks `venv` creation, add the project folder to the exclusion list, or see `docs/INSTALLATION.md` for detailed instructions.

---

## Quick Start

### Running the simulator

1. Launch `start.bat` (Windows) or `bash start.sh` (Linux/Mac)
2. The browser opens at `http://localhost:8080`
3. Select a mode from the start screen:
   - **FREE OPERATION** — no objectives, full sandbox
   - **DISPATCH OPERATOR** — match electrical output targets from the grid operator
   - **SHIFT SUPERVISOR** — respond to random equipment faults during your shift
4. Choose a difficulty level and click **BEGIN**

### Basic reactor operation

| Control | How |
|---|---|
| **Select control rods** | Click rod cells (green border) in the core canvas |
| **Select all rods** | Click **ALL** in the right panel |
| **Set rod position** | Use the vertical slider or INSERT / WITHDRAW buttons |
| **Move rods step by step** | Use the **−5% / +5%** buttons |
| **Set rod to Manual mode** | Right-click the rod → MANUAL |
| **Trip / restore a pump** | Click the ON/OFF button in the pump panel |
| **Declare a sensor fault** | Right-click a T-sensor or F-sensor in the core → DECLARE FAULT |
| **Emergency SCRAM** | Click the red **AZ-5 SCRAM** button |
| **Cold restart** | Click **COLD RESTART** after a SCRAM or meltdown |

**Typical startup sequence:**
1. Select all rods → Withdraw to ~65%
2. All four pumps ON at ~75% speed
3. Wait for power to rise and stabilise (~280°C core temperature)
4. Trim rod withdrawal to hit the desired MWe output
5. Monitor xenon, void fraction, and temperature continuously

### Normal operating targets

| Parameter | Target | Alarm |
|---|---|---|
| Reactor power | 50–85% | > 88% |
| Average core temperature | 270–305°C | > 310°C (hottest channel) |
| Steam pressure | 6.0–8.0 MPa | > 8.0 MPa |
| Void fraction | 0–15% | > 20% |
| Total coolant flow | 55–100% | < 35% |
| Electrical output | 400–900 MWe | — |

---

## Instructor Controls

All instructor functions are accessible via the browser UI — no separate interface is needed.

| Action | How |
|---|---|
| **Inject a rod fault** | Right-click a control rod cell → FAULT |
| **Trip a pump** | Click the **FLT** button on any pump row |
| **Fail a temperature sensor** | Right-click a T-sensor (diamond) → DECLARE FAULT |
| **Remove a fuel assembly** | Right-click a fuel channel → REMOVE FUEL ASSEMBLY |
| **Pause / slow down physics** | Use the **PAUSE / 0.25× / 1× / 2× / 4×** buttons in the header |
| **Load a scenario** | Click **▶ SCN** in the header |
| **Edit Modbus addresses** | Click **⚙ CFG** in the header |
| **Return to main menu** | Click **← MAIN MENU** in the right panel |

---

## Modbus TCP Interface

The simulator exposes all process variables as a Modbus TCP server on port **502** (configurable).

### Connecting a PLC

| PLC Platform | Function Code | Notes |
|---|---|---|
| Siemens S7-1200/1500 (TIA Portal) | MB_CLIENT instruction | Use 1-based addressing (add 1 to addresses below) |
| Allen-Bradley CompactLogix (Studio 5000) | MSG instruction | Use 0-based addressing |
| Schneider Modicon (Unity Pro) | ADDM / READ_VAR | Configure for Modbus TCP |
| Mitsubishi / OpenPLC | Modbus TCP client | 0-based addressing |

### Key register addresses (0-based)

**Input Registers — FC04 (read only)**

| Address | Signal | Range | Scale |
|---|---|---|---|
| 0 | Reactor thermal power | 0–100% | 0–32767 |
| 1 | Electrical output | 0–1100 MWe | 0–32767 |
| 2 | Steam drum pressure | 0–15 MPa | 0–32767 |
| 3 | Total coolant flow | 0–100% | 0–32767 |
| 4 | Core void fraction | 0–100% | 0–32767 |
| 5 | Average core temp | 0–500°C | 0–32767 |
| 6 | Max channel temp | 0–500°C | 0–32767 |
| 10 | Grid demand target *(shift modes only)* | 0–1100 MWe | 0–32767 (0 = free play) |
| 100–129 | T-sensors TS-01..TS-30 | 0–500°C | 0–32767 (32767 = fault) |
| 150–169 | F-sensors FS-01..FS-20 | 0–1.5 | 0–32767 (32767 = fault) |
| 200–229 | Control rod positions CR-01..30 | 0–100% | 0–32767 |
| 250–253 | Pump actual speeds MCP-1..4 | 0–100% | 0–32767 |

**Holding Registers — FC03/FC16 (read/write)**

| Address | Signal | Notes |
|---|---|---|
| 0–29 | CR-01..CR-30 setpoints | Write rejected if rod is in Manual or Fault mode |
| 100–103 | MCP-1..4 speed setpoints | Write rejected if pump is faulted |

**Coils — FC01/FC05 (read/write)**

| Address | Signal |
|---|---|
| 0 | AZ-5 SCRAM command (write 1 to trigger) |
| 10–13 | MCP-1..4 run command |

**Discrete Inputs — FC02 (read only)**

| Address | Signal |
|---|---|
| 0 | Reactor running (power > 5%) |
| 1 | SCRAM active |
| 10–18 | Alarm bits (HI POWER, HI TEMP, VOID, LO FLOW, PUMP TRIP, SCRAM, DAMAGE, HI PRESS, Xe PIT) |
| 30, 33, 36, 39 | MCP-1..4 running status |
| 100–219 | CR-01..CR-30 status bits (4 per rod: at_min, at_max, manual, fault) |

All addresses are configurable in the **⚙ CFG** panel in the browser without restarting.

---

## Modbus Training Client

A ready-made Python control client demonstrates closed-loop PLC-style control of the reactor via Modbus TCP. It implements:

- **PI power controller** — adjusts average rod withdrawal to hit a MWe setpoint
- **Spatial rod flattening** — proximity-weighted T-sensor feedback trims individual rods to suppress hot spots
- **Pump management** — all four pumps controlled independently with flow based on setpoint and thermal conditions
- **Safety interlocks** — automatic SCRAM on high power, temperature, void, or pressure

### Running the client

**Windows:**
```
Double-click start_client.bat
```
The script creates a separate `client_venv\` virtual environment and installs `pymodbus` automatically.

**Command line (after `start_client.bat` has run once to set up the venv):**
```bash
# Windows
client_venv\Scripts\activate
python modbus_client.py --host 127.0.0.1 --port 502 --setpoint 600

# Linux / Mac
source client_venv/bin/activate
python modbus_client.py --host 127.0.0.1 --port 502 --setpoint 600
```

### Client controls

| Key | Action |
|---|---|
| `↑` or `+` | Increase setpoint +25 MWe |
| `↓` or `-` | Decrease setpoint -25 MWe |
| `a` | AUTO mode (controller active) |
| `m` | MANUAL mode (suspend outputs) |
| `s` | Send AZ-5 SCRAM |
| `q` | Quit |

> The client uses native Windows console input (`msvcrt`) so keypresses are always detected reliably regardless of terminal type.

---

## Configuration

### Network settings (`config/network.json`)

```json
{
  "modbus_tcp": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 502,
    "unit_id": 1
  },
  "web_ui": {
    "host": "0.0.0.0",
    "port": 8080
  }
}
```

Change the Modbus port to `5020` or higher if you cannot run on port 502 without administrator rights.

### IO address map (`config/io_map.json`)

Every Modbus register address and engineering-unit scaling is configurable without code changes. Edit via the **⚙ CFG** panel in the browser, or edit the file directly. Changes take effect after restarting the simulator.

### Scenarios (`config/scenarios/`)

Add `.json` files to this folder to create training scenarios. Two examples are included:

- `iodine_pit_recovery.json` — scripted exercise demonstrating xenon poisoning
- `random_standard.json` — randomised fault injection for shift supervisor training

See `docs/SCENARIO_AUTHORING.md` for the full JSON format reference.

---

## Documentation

| File | Contents |
|---|---|
| `docs/INSTALLATION.md` | Detailed install guide, Python PATH fixes, PLC connection steps for all platforms, Trellix notes |
| `docs/TECHNICAL_DESIGN.md` | Architecture, full Modbus register map, WebSocket/REST API reference |
| `docs/REACTOR_PHYSICS_REFERENCE.md` | Operating limits, destruction thresholds, PID tuning guide, common student mistakes |
| `docs/SCENARIO_AUTHORING.md` | Complete JSON scenario format with all trigger and action types |
| `docs/MODBUS_CLIENT.md` | Control algorithm description, tuning guide, training exercises |

---

## Project Structure

```
reactor-simulator/
├── start.bat                   Windows: launch the simulator
├── start.sh                    Linux/macOS: launch the simulator
├── start_client.bat            Windows: launch the Modbus training client
├── modbus_client.py            Modbus PLC training control client
├── requirements.txt            Simulator Python dependencies
├── requirements_client.txt     Client Python dependencies
│
├── backend/
│   ├── main.py                 FastAPI server (entry point)
│   ├── physics.py              RBMK-1000 reactor physics engine
│   ├── io_bridge.py            Modbus TCP server and register mapping
│   ├── scenario_engine.py      Scripted and random fault injection
│   ├── config_manager.py       JSON config management
│   └── persistence.py          SQLite — scores, settings, scenario metadata
│
├── frontend/
│   └── index.html              Browser UI (visualisation + instructor controls)
│
├── config/
│   ├── network.json            Server addresses and ports
│   ├── io_map.json             Modbus register addresses and scaling
│   └── scenarios/              Training scenario JSON files
│
├── data/
│   └── simulator.db            SQLite database (auto-created on first run)
│
└── docs/
    ├── INSTALLATION.md
    ├── TECHNICAL_DESIGN.md
    ├── REACTOR_PHYSICS_REFERENCE.md
    ├── SCENARIO_AUTHORING.md
    └── MODBUS_CLIENT.md
```

---

## Common Issues

**Simulator opens on Python 3.10 instead of 3.14**
The Windows Python Launcher (`py.exe`) is used automatically. If it resolves to an old version, `start.bat` will try `py -3.14`, `py -3.13`, etc. in order. If none work, re-run the Python 3.14 installer → Modify → tick **"Add Python to environment variables"**.

**Modbus connection refused from PLC**
1. Check Windows Firewall — add an inbound rule for TCP port 502
2. Verify the PC's IP address is reachable from the PLC (`ping` test)
3. Try changing the Modbus port to 5020 in `config/network.json` (avoids needing admin rights)

**Core canvas shows all graphite (empty grid)**
Clear the browser cache with `Ctrl+Shift+R` (hard refresh). This can happen if an old cached version of the page is loaded.

**Client `.bat` closes immediately**
The script prints step-by-step progress and pauses on every error — it should never close silently. If it does, right-click `start_client.bat` → **Run as administrator**, or open a Command Prompt manually and run `start_client.bat` from within it so the window stays open.

---

## Contributing

Pull requests welcome. Please test against Python 3.11 and 3.12+ before submitting.

Key areas for contribution:
- Additional scenario JSON files (training exercises)
- OPC-UA server support (the architecture is ready; `asyncua` library integration needed)
- Additional alarm interlocks and safety functions in the Modbus client
- Unit tests for the physics engine

---

## Licence

MIT — see `LICENSE` for details.

---

## Acknowledgements

Built as a control systems training platform for apprentice engineers. The reactor physics model is loosely based on the RBMK-1000 design for educational purposes. No classified or sensitive information is used. The positive void coefficient and xenon poisoning models are simplified approximations intended to demonstrate control system concepts, not to accurately model any real reactor.
