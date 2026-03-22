"""
config_manager.py — Configuration management for the reactor simulator.
Handles network settings, IO address maps, and scenario persistence.
All settings are stored as JSON files and also mirrored in SQLite.
"""

import json
import os
import logging
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).parent.parent
CONFIG_DIR    = BASE_DIR / "config"
SCENARIO_DIR  = CONFIG_DIR / "scenarios"
DATA_DIR      = BASE_DIR / "data"

# ── Default configurations ────────────────────────────────────────────

DEFAULT_NETWORK = {
    "modbus_tcp": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 502,
        "unit_id": 1
    },
    "opc_ua": {
        "enabled": False,
        "endpoint": "opc.tcp://0.0.0.0:4840/reactor"
    },
    "web_ui": {
        "host": "0.0.0.0",
        "port": 8080
    }
}

# Default IO map — all addresses are user-configurable.
# scale_min/max define the engineering unit range mapped to 0–32767.
# A scale_factor overrides this: raw = round(eng_value * scale_factor)
# If scale_factor is null, linear interpolation between scale_min and scale_max is used.
DEFAULT_IO_MAP = {
    "_comment": "All Modbus addresses are 0-based (add 1 for display in some tools). Scale: raw = round(value * scale_factor) OR linear interp between scale_min/max → 0-32767.",

    "coils": {
        "scram_command":    {"address": 0,  "description": "AZ-5 SCRAM command (write 1 to trigger)"},
        "pump_1_run":       {"address": 10, "description": "MCP-1 run command"},
        "pump_2_run":       {"address": 11, "description": "MCP-2 run command"},
        "pump_3_run":       {"address": 12, "description": "MCP-3 run command"},
        "pump_4_run":       {"address": 13, "description": "MCP-4 run command"},
    },

    "discrete_inputs": {
        "reactor_running":  {"address": 0,  "description": "Reactor running (power > 5%)"},
        "scram_active":     {"address": 1,  "description": "SCRAM active"},
        "meltdown":         {"address": 2,  "description": "Core damage"},
        "alarm_hipower":    {"address": 10, "description": "HI REACTOR POWER alarm"},
        "alarm_hitemp":     {"address": 11, "description": "HI CORE TEMP alarm"},
        "alarm_void":       {"address": 12, "description": "HIGH VOID FRACTION alarm"},
        "alarm_locool":     {"address": 13, "description": "LO COOLANT FLOW alarm"},
        "alarm_pumptrip":   {"address": 14, "description": "PUMP TRIP alarm"},
        "alarm_scram":      {"address": 15, "description": "AZ-5 SCRAM alarm"},
        "alarm_damage":     {"address": 16, "description": "CORE DAMAGE alarm"},
        "alarm_hipress":    {"address": 17, "description": "HI STEAM PRESSURE alarm"},
        "alarm_xenonpit":   {"address": 18, "description": "XENON/IODINE PIT alarm"},
        # Pump status (3 bits per pump)
        "pump_1_running":   {"address": 30, "description": "MCP-1 running"},
        "pump_1_fault":     {"address": 31, "description": "MCP-1 fault"},
        "pump_2_running":   {"address": 33, "description": "MCP-2 running"},
        "pump_2_fault":     {"address": 34, "description": "MCP-2 fault"},
        "pump_3_running":   {"address": 36, "description": "MCP-3 running"},
        "pump_3_fault":     {"address": 37, "description": "MCP-3 fault"},
        "pump_4_running":   {"address": 39, "description": "MCP-4 running"},
        "pump_4_fault":     {"address": 40, "description": "MCP-4 fault"},
        # Control rod status (4 bits per rod × 30 rods) — base address 100
        # Pattern: base+0=at_min, base+1=at_max, base+2=manual, base+3=fault
        "cr_status_base":   {"address": 100, "description": "CR-01 status bits (4 per rod, 30 rods = addresses 100-219)"},
        # Fuel channel installed (1 per channel, 396 positions) — base 300
        "fuel_base":        {"address": 300, "description": "Fuel channel installed bits (1=fuel present, 0=empty)"},
    },

    "input_registers": {
        # Plant-wide analog reads
        "reactor_power":    {"address": 0,  "scale_min": 0,   "scale_max": 100,   "description": "Reactor thermal power [%]"},
        "output_mw":        {"address": 1,  "scale_min": 0,   "scale_max": 1100,  "description": "Electrical output [MWe]"},
        "steam_pressure":   {"address": 2,  "scale_min": 0,   "scale_max": 15,    "description": "Steam drum pressure [MPa]"},
        "total_flow":       {"address": 3,  "scale_min": 0,   "scale_max": 100,   "description": "Total coolant flow [%]"},
        "void_fraction":    {"address": 4,  "scale_min": 0,   "scale_max": 100,   "description": "Core void fraction [%]"},
        "avg_core_temp":    {"address": 5,  "scale_min": 0,   "scale_max": 500,   "description": "Average core temperature [°C]"},
        "max_chan_temp":     {"address": 6,  "scale_min": 0,   "scale_max": 500,   "description": "Max channel temperature [°C]"},
        "turbine_eff":      {"address": 7,  "scale_min": 0,   "scale_max": 35,    "description": "Turbine efficiency [%]"},
        "avg_iodine":       {"address": 8,  "scale_min": 0,   "scale_max": 1,     "description": "Average Iodine-135 [0–1]"},
        "avg_xenon":        {"address": 9,  "scale_min": 0,   "scale_max": 1,     "description": "Average Xenon-135 [0–1]"},
        "target_mw":        {"address": 10, "scale_min": 0,   "scale_max": 1100,  "description": "Grid demand target MWe (0 when not in a shift game mode)"},
        # Temperature sensors base address 100 (30 sensors max)
        "tsensor_base":     {"address": 100, "scale_min": 0,  "scale_max": 500,   "description": "T-sensors base (TS-01..TS-30), 0°C–500°C → 0–32767, 32767=fault"},
        # Flux sensors base address 150 (20 sensors max)
        "fsensor_base":     {"address": 150, "scale_min": 0,  "scale_max": 1.5,   "description": "F-sensors base (FS-01..FS-20), 0–1.5 → 0–32767, 32767=fault"},
        # Control rod positions base address 200 (30 rods)
        "cr_pos_base":      {"address": 200, "scale_min": 0,  "scale_max": 100,   "description": "CR positions base (CR-01..CR-30), 0–100% → 0–32767"},
        # Pump speeds base address 250 (4 pumps)
        "pump_speed_base":  {"address": 250, "scale_min": 0,  "scale_max": 100,   "description": "Pump actual speeds (MCP-1..4), 0–100% → 0–32767"},
    },

    "holding_registers": {
        # Control rod setpoints base address 0 (30 rods)
        "cr_setpoint_base": {"address": 0,  "scale_min": 0,  "scale_max": 100,   "description": "CR setpoints base (CR-01..CR-30), 0–32767 → 0–100%"},
        # Pump speed setpoints base address 100 (4 pumps)
        "pump_setpoint_base":{"address": 100,"scale_min": 0,  "scale_max": 100,   "description": "Pump speed setpoints (MCP-1..4), 0–32767 → 0–100%"},
    }
}


class ConfigManager:
    """Loads, validates, and saves configuration files."""

    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.network  = self._load_or_create("network.json",  DEFAULT_NETWORK)
        self.io_map   = self._load_or_create("io_map.json",   DEFAULT_IO_MAP)

    def _load_or_create(self, filename: str, default: dict) -> dict:
        path = CONFIG_DIR / filename
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                log.info("Loaded config: %s", filename)
                return data
            except Exception as e:
                log.warning("Failed to load %s (%s), using defaults", filename, e)
        with open(path, "w") as f:
            json.dump(default, f, indent=2)
        log.info("Created default config: %s", filename)
        return default

    def save_network(self, data: dict):
        self.network = data
        with open(CONFIG_DIR / "network.json", "w") as f:
            json.dump(data, f, indent=2)

    def save_io_map(self, data: dict):
        self.io_map = data
        with open(CONFIG_DIR / "io_map.json", "w") as f:
            json.dump(data, f, indent=2)

    def list_scenarios(self):
        return [p.name for p in SCENARIO_DIR.glob("*.json")]

    def load_scenario(self, filename: str) -> dict:
        path = SCENARIO_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Scenario not found: {filename}")
        with open(path) as f:
            return json.load(f)

    def save_scenario(self, filename: str, data: dict):
        if not filename.endswith(".json"):
            filename += ".json"
        path = SCENARIO_DIR / filename
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def delete_scenario(self, filename: str):
        path = SCENARIO_DIR / filename
        if path.exists():
            path.unlink()

    # ── IO address helpers ────────────────────────────────────────────

    def eng_to_raw(self, value: float, reg_def: dict) -> int:
        """Convert engineering value to raw 0–32767 integer."""
        sf = reg_def.get("scale_factor")
        if sf is not None:
            return max(0, min(32767, round(value * sf)))
        lo = reg_def.get("scale_min", 0)
        hi = reg_def.get("scale_max", 1)
        if hi == lo:
            return 0
        raw = (value - lo) / (hi - lo) * 32767
        return max(0, min(32767, round(raw)))

    def raw_to_eng(self, raw: int, reg_def: dict) -> float:
        """Convert raw 0–32767 integer to engineering value."""
        sf = reg_def.get("scale_factor")
        if sf is not None:
            return raw / sf if sf != 0 else 0.0
        lo = reg_def.get("scale_min", 0)
        hi = reg_def.get("scale_max", 1)
        return lo + (raw / 32767) * (hi - lo)

    def get_io_def(self, table: str, name: str) -> dict:
        return self.io_map.get(table, {}).get(name, {})
