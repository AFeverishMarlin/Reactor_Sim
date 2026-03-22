# Scenario Authoring Guide

---

## Overview

Scenarios are JSON files stored in `config/scenarios/`. The simulator picks them up automatically — no restart needed. Two types exist: **scripted** (deterministic event sequences) and **random** (probabilistic fault injection).

---

## Scripted Scenario Template

```json
{
  "name": "Human-readable name",
  "type": "scripted",
  "description": "What this exercise teaches",

  "initial_state": {
    "rod_positions_pct": 65,
    "pump_speeds_pct": 80
  },

  "events": [
    {
      "trigger": {"type": "time", "seconds": 30},
      "action":  {"type": "note", "message": "Message shown in operational log"}
    }
  ]
}
```

## Random Scenario Template

```json
{
  "name": "Human-readable name",
  "type": "random",
  "description": "What this exercise teaches",
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

---

## All Trigger Types

### Time trigger
```json
{"type": "time", "seconds": 120}
```
Fires exactly 120 seconds after scenario start.

### Condition trigger
```json
{"type": "condition", "variable": "xenon", "operator": ">", "value": 0.30}
```
Fires when the named physics variable meets the condition. Only fires once.

Available variables:

| Variable | Description | Typical Range |
|---|---|---|
| `power` | Reactor thermal power % | 0–100 |
| `temp` | Average core temperature °C | 40–420 |
| `pressure` | Steam drum pressure MPa | 0–15 |
| `xenon` | Average Xenon-135 (normalised) | 0–0.8 |
| `iodine` | Average Iodine-135 (normalised) | 0–1.0 |
| `void_fraction` | Core void fraction (0=none, 1=full) | 0–1 |
| `max_chan_t` | Hottest fuel channel °C | 40–500 |
| `total_flow` | Total coolant flow % | 0–100 |
| `output_mw` | Electrical output MWe | 0–1100 |

Operators: `>`, `<`, `>=`, `<=`, `==`

---

## All Action Types

### Note (log message)
```json
{"type": "note", "message": "Your instructor message here"}
```

### Fault — pump trip (temporary, resettable by PLC)
```json
{"type": "fault", "target": "pump", "id": 2, "fault": true}
```
`id`: 1–4 (MCP-1 through MCP-4). Set `"fault": false` to clear.

### Fault — pump seal failure (requires FLT button to clear)
```json
{"type": "fault", "target": "pump_fault", "id": 1, "fault": true}
```

### Fault — control rod drive
```json
{"type": "fault", "target": "rod", "id": 5, "fault": true}
```
`id`: 1–30 (CR-01 through CR-30). Sets the rod to FAULT mode (locked, no writes).

### Fault — temperature sensor
```json
{"type": "fault", "target": "sensor_T", "id": 3, "fault": true}
```
`id`: 1–30 (TS-01 through TS-30). Sensor reads 32767 in Modbus (fault indicator).

### Fault — flux sensor
```json
{"type": "fault", "target": "sensor_F", "id": 2, "fault": true}
```
`id`: 1–20 (FS-01 through FS-20).

### Set rod target
```json
{"type": "set_rod", "id": "CR-01", "target": 80.0}
```
Has no effect if the rod is in MANUAL or FAULT mode.

### Set pump state
```json
{"type": "set_pump", "id": 0, "on": false}
```
`id`: 0–3 (MCP-1 = 0, MCP-4 = 3). Has no effect if pump is in FAULT.

### Trigger SCRAM
```json
{"type": "scram"}
```

### Set rod mode
```json
{"type": "set_rod_mode", "id": "CR-15", "mode": "fault"}
```
`mode`: `"auto"`, `"manual"`, or `"fault"`

---

## Example: Pump Failure During Power Increase

```json
{
  "name": "Pump Failure at Power",
  "type": "scripted",
  "description": "Student must recover from a pump trip while increasing power to target. Tests alarm response and interlock logic.",
  "initial_state": {"rod_positions_pct": 50, "pump_speeds_pct": 75},
  "events": [
    {
      "trigger": {"type": "time", "seconds": 10},
      "action":  {"type": "note", "message": "TASK: Bring reactor to 65% power and hold stable."}
    },
    {
      "trigger": {"type": "condition", "variable": "power", "operator": ">=", "value": 60},
      "action":  {"type": "fault", "target": "pump", "id": 3, "fault": true}
    },
    {
      "trigger": {"type": "condition", "variable": "temp", "operator": ">", "value": 320},
      "action":  {"type": "note", "message": "WARNING: Core temperature rising. Check pump status and reduce power if necessary."}
    },
    {
      "trigger": {"type": "time", "seconds": 180},
      "action":  {"type": "note", "message": "DEBRIEF: Did your PLC interlock respond correctly? Was MCP-3 restarted promptly?"}
    }
  ]
}
```

---

## Tips for Good Scenarios

1. **Start simple**: Use a single time-triggered fault before introducing condition triggers
2. **Allow warm-up time**: Give 10–15 seconds before the first event so the reactor can reach a state
3. **Use notes liberally**: Log messages appear in the UI and help apprentices follow along
4. **Pair faults with recoveries**: If you inject a fault at t=60, consider clearing it at t=120 to test that the PLC also handles the return to normal
5. **Test scenarios yourself** before using them in training — run through the expected PLC response manually to ensure timing is achievable
6. **Version your scenarios**: Include a version number in the description field (`"description": "v2.1 — ..."`)
