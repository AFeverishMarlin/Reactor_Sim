# RBMK-1000 Simulator — Installation & Operations Manual

---

## 1. System Requirements

### Minimum
- Windows 10 / Windows 11 (64-bit) or Ubuntu 20.04+ / macOS 12+
- Python 3.11 or later
- 4 GB RAM
- 500 MB free disk space
- Network interface (Ethernet recommended for PLC connectivity)

### Recommended
- Windows 11 64-bit (for best PLC software compatibility)
- 8 GB RAM
- Dedicated Ethernet port for the PLC training network (separate from any internet-facing NIC)
- Static IP address on the training network

---

## 2. Installation

### 2.1 Install Python

Download Python 3.11+ from https://python.org/downloads

**Windows**: During installation, tick **"Add Python to PATH"**. Use the default installation directory.

**Linux**: `sudo apt install python3 python3-pip python3-venv`

Verify installation: open a terminal and run:
```
python --version
```
Expected output: `Python 3.11.x` or later.

### 2.2 Install the Simulator

Copy the `reactor-simulator` folder to the training PC. Suggested locations:
- Windows: `C:\Training\reactor-simulator\`
- Linux: `/opt/reactor-simulator/`

No registry entries, no admin installation required (except for Modbus on port 502 — see Section 2.3).

### 2.3 Modbus Port Permissions (Windows)

Standard Modbus TCP uses port 502, which is a privileged port on Windows. Two options:

**Option A — Run as Administrator** (simpler):
Right-click `start.bat` → "Run as administrator". Required each launch.

**Option B — Change Modbus port** (recommended for regular use):
Edit `config/network.json`, change `"port": 502` to `"port": 5020`. Configure the PLC to connect to port 5020. No admin rights needed.

### 2.4 First Launch

**Windows**: Double-click `start.bat`

**Linux/Mac**: Open a terminal, navigate to the folder, run `bash start.sh`

The launcher will:
1. Check Python version
2. Create a Python virtual environment (`venv/` folder) — internet required for first run only
3. Install all dependencies from `requirements.txt`
4. Start the simulator server
5. Automatically open the browser at `http://localhost:8080`

**First run internet requirement**: The pip dependency installation requires internet access. For offline-only installations, run the first launch on a networked PC, then copy the entire folder (including the `venv/` directory) to the offline training PC. The offline PC will not need to download anything.

### 2.5 Antivirus Considerations (Trellix/McAfee)

If Trellix flags the virtual environment creation or Python execution:
1. Add the `reactor-simulator` folder to the Trellix exclusion list
2. Or, request IT to whitelist `python.exe` within the `venv\Scripts\` subfolder
3. The simulator does not connect to the internet during normal operation — only local LAN traffic

---

## 3. Network Configuration

### 3.1 Training Station Network Setup

Typical setup for a single training station:

```
Training PC (Simulator)
  NIC 1: 192.168.10.1/24 — Training LAN (to PLC)
  NIC 2: (optional) facility network / internet

PLC
  IP: 192.168.10.2 — or any address on 192.168.10.x
  Modbus TCP port: 502 (default)
  Unit ID: 1 (default)
```

### 3.2 Multi-Station Setup

Each simulator is an independent instance. For multiple training stations, each PC runs its own copy of the simulator with its own Modbus server. PLCs connect to their local training PC only.

If all stations share the same LAN subnet, change the Modbus port to avoid conflicts:
- Station 1: port 502
- Station 2: port 503
- Station 3: port 504
- etc.

### 3.3 Editing Network Configuration

Edit `config/network.json`:

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

`"host": "0.0.0.0"` means listen on all network interfaces. To restrict to a specific interface, replace with that interface's IP (e.g. `"192.168.10.1"`).

Restart the simulator after editing this file.

---

## 4. PLC Connection Setup

### 4.1 Siemens S7-1200 / S7-1500 (TIA Portal)

1. Add a "MB_CLIENT" instruction in a cyclic OB
2. Set `REQ` = TRUE (always requesting)
3. Set `IP_ADDR` = IP of the training PC
4. Set `IP_PORT` = 502 (or configured port)
5. For reading Input Registers (FC04): set `MB_MODE` = 4, `MB_DATA_ADDR` starting address (1-based in Siemens)
6. For writing Holding Registers (FC16): set `MB_MODE` = 1

Note: Siemens uses 1-based Modbus addressing. Add 1 to all addresses shown in this document when configuring MB_DATA_ADDR.

### 4.2 Allen-Bradley CompactLogix / Studio 5000

1. Add a "MSG" instruction
2. Set Service Type: "Modbus Read Input Registers" (FC04) or "Modbus Write Multiple Registers" (FC16)
3. Set path to an Ethernet card with the training PC's IP
4. Instance number = Modbus unit ID (1)
5. Element number = register address (0-based)

### 4.3 Schneider Electric (Unity Pro / EcoStruxure)

1. Use the ADDM or READ_VAR/WRITE_VAR blocks
2. Configure for Modbus TCP
3. Set the channel address to the training PC IP and port
4. Object type: `%MW` (Holding Registers) or `%IW` (Input Registers equivalent)

### 4.4 Mitsubishi (GX Works)

1. Use MELSEC communication library or built-in MC protocol
2. If using OpenPLC on the Mitsubishi: configure Modbus TCP client in OpenPLC settings
3. Address mapping: consult OpenPLC documentation for Mitsubishi I/O mapping

### 4.5 OpenPLC

1. Open the OpenPLC Runtime web interface
2. Go to Slave Devices → Add New Device
3. Set IP: training PC IP, Port: 502, Slave ID: 1
4. Map I/O registers to PLC internal memory (%IW, %QW, %IX, %QX)

---

## 5. Instructor Operation Guide

### 5.1 Starting a Session

1. Launch the simulator (`start.bat`)
2. Browser opens automatically at `http://localhost:8080`
3. Select a game mode or use Free Operation
4. For game modes: choose difficulty and shift duration before pressing Start

### 5.2 Instructor Controls During a Session

**Core canvas (right-click any cell)**:
- Fuel channel: Remove or restore a fuel assembly
- Control rod: Set mode to AUTO / MANUAL / FAULT
- Temperature sensor: Declare or clear fault
- Flux sensor: Declare or clear fault

**Pump panel**:
- FLT/CLR button next to each pump: declare or clear pump fault
- ON/OFF: manually trip or restart pump

**Simulation speed** (top controls):
- PAUSE: freeze physics, PLC continues polling (sees static state)
- ×0.25: quarter speed (useful for demonstrating fast transients slowly)
- ×1: normal
- ×2, ×4: accelerated (useful for demonstrating xenon build-up)

### 5.3 Scenario Management

**Loading a scripted scenario**:
1. Click the SCENARIO button in the header
2. Select a scenario from the list
3. Click LOAD, then START
4. Events fire automatically; messages appear in the operational log

**Stopping a scenario**: Click STOP. The physics continues; only the automated event firing stops.

**Creating scenarios**: Edit or create JSON files in `config/scenarios/`. See the Scenario Format section in the Technical Design Document. The simulator picks up new files without restarting.

### 5.4 Resetting Between Sessions

Press the COLD RESTART button in the right panel. This returns:
- All rods to 0% (fully inserted)
- All pumps to ON at 80% speed
- All temperatures to 40°C
- All xenon and iodine to zero
- All faults cleared
- All alarms reset

Scores are preserved in the database. Settings (network, IO map) are preserved.

### 5.5 Adjusting IO Addresses

If a PLC program uses different Modbus addresses than the defaults:
1. Open `config/io_map.json` in any text editor (Notepad, VS Code, etc.)
2. Change the `"address"` value for each signal
3. Save the file
4. Restart the simulator
5. The new addresses take effect immediately on next start

Alternatively: the REST API at `http://localhost:8080/api/config/iomap` accepts a POST request with the updated map.

---

## 6. Maintenance

### 6.1 Updating Dependencies

If a new version of a dependency needs installing:
```
cd reactor-simulator
venv\Scripts\activate.bat   (Windows)
pip install -r requirements.txt --upgrade
```

### 6.2 Resetting the Database

Delete `data/simulator.db`. It will be recreated on next launch. This clears:
- All scores
- Saved settings (network and IO config)
- Scenario metadata index

The scenario JSON files in `config/scenarios/` are NOT deleted.

### 6.3 Log Files

The simulator logs to the console (the terminal window). To capture logs to a file, modify the last line of `start.bat`:
```
python backend\main.py >> logs\simulator.log 2>&1
```
Create the `logs\` directory first.

### 6.4 Common Issues

**Browser does not open automatically**:
Navigate to `http://localhost:8080` manually.

**Modbus not connecting from PLC**:
1. Check Windows Firewall — add inbound rule for TCP port 502 (or configured port)
2. Verify the training PC IP is reachable from the PLC (ping test)
3. Check `config/network.json` — ensure `"host": "0.0.0.0"` and port matches PLC config
4. Try changing to a non-privileged port (5020) and run without administrator rights

**"Port already in use" error**:
Another application is using port 8080 or 502. Change ports in `config/network.json`.

**Physics behaves differently than expected after a reload**:
Some browsers cache the old JavaScript. Press Ctrl+Shift+R (hard refresh) to clear the cache.

**Pip install fails (no internet)**:
See Section 2.4 for offline deployment instructions.

---

## 7. File Structure Reference

```
reactor-simulator/
├── start.bat                    Windows launch script
├── start.sh                     Linux/Mac launch script
├── requirements.txt             Python package list
│
├── backend/
│   ├── main.py                  FastAPI server (entry point)
│   ├── physics.py               Reactor physics engine
│   ├── io_bridge.py             Modbus TCP server
│   ├── scenario_engine.py       Scenario runner
│   ├── config_manager.py        Config file management
│   └── persistence.py           SQLite database layer
│
├── config/
│   ├── network.json             Server addresses and ports
│   ├── io_map.json              Modbus register addresses and scaling
│   └── scenarios/
│       ├── iodine_pit_recovery.json
│       ├── random_standard.json
│       └── (add your own .json files here)
│
├── data/
│   └── simulator.db             SQLite database (auto-created)
│
├── frontend/
│   └── index.html               Browser UI
│
├── docs/
│   ├── INSTALLATION.md          This document
│   ├── TECHNICAL_DESIGN.md      Architecture and API reference
│   ├── REACTOR_PHYSICS_REFERENCE.md   Physics guide for apprentices
│   └── SCENARIO_AUTHORING.md    Scenario JSON format guide
│
└── venv/                        Python virtual environment (auto-created)
```

---

## 8. Safety and Usage Notes

This simulator is a **training tool only**. It does not represent, control, or connect to any real nuclear installation. The physics are simplified for educational purposes. Do not use this software for any safety case, engineering analysis, or regulatory submission.

The simulator deliberately allows the reactor to be destroyed to demonstrate the consequences of incorrect process control. This is an intentional design choice for educational purposes.
