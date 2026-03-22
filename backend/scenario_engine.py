"""
scenario_engine.py — Scenario runner for scripted and random fault injection.

Scenario JSON format (see docs/scenario_format.md for full reference):

SCRIPTED:
{
  "name": "Iodine Pit Recovery",
  "type": "scripted",
  "description": "...",
  "initial_state": { "rod_positions_pct": 70, "pump_speeds_pct": 80 },
  "events": [
    { "trigger": {"type": "time", "seconds": 30},
      "action":  {"type": "note", "message": "Reduce power now"} },
    { "trigger": {"type": "time", "seconds": 120},
      "action":  {"type": "fault", "target": "pump", "id": 1, "fault": true} },
    { "trigger": {"type": "condition", "variable": "xenon", "operator": ">", "value": 0.35},
      "action":  {"type": "note", "message": "Xenon pit active"} }
  ]
}

RANDOM:
{
  "name": "Random Faults",
  "type": "random",
  "description": "...",
  "min_interval_s": 60,
  "max_interval_s": 180,
  "max_concurrent_faults": 2,
  "fault_weights": {
    "pump_trip":     0.40,
    "rod_fault":     0.25,
    "sensor_fault_T":0.20,
    "sensor_fault_F":0.10,
    "pump_fault":    0.05
  }
}

Trigger types: "time" (seconds since start), "condition" (physics variable comparison)
Action types:  "note" (log message), "fault" (inject fault), "set_rod" (set rod target),
               "set_pump" (set pump on/off), "scram" (trigger AZ-5)
Condition operators: ">", "<", ">=", "<=", "=="
Condition variables: "power", "temp", "pressure", "xenon", "iodine", "void_fraction",
                     "max_chan_t", "total_flow", "output_mw"
"""

import asyncio
import logging
import random
import time
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from physics import ReactorPhysics

log = logging.getLogger(__name__)


class ScenarioEngine:

    def __init__(self, physics):
        self._physics: ReactorPhysics = physics
        self._scenario: Optional[dict] = None
        self._start_time: float = 0.0
        self._running: bool = False
        self._fired_events: set = set()      # indices of already-fired scripted events
        self._fault_cooldown: float = 0.0    # seconds until next random fault
        self._active_faults: List[dict] = [] # [{id, desc, type, ...}]
        self._on_note_callbacks = []

    def on_note(self, cb):
        self._on_note_callbacks.append(cb)

    def _emit_note(self, msg: str):
        self._physics._log(f"[SCENARIO] {msg}", "info")
        for cb in self._on_note_callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    # ── Load & start ──────────────────────────────────────────────────

    def load(self, scenario: dict):
        self._scenario  = scenario
        self._fired_events = set()
        self._active_faults = []
        log.info("Scenario loaded: %s", scenario.get("name", "unnamed"))

    def start(self):
        if not self._scenario:
            return
        self._start_time = time.monotonic()
        self._running = True

        # Apply initial state
        init = self._scenario.get("initial_state", {})
        rod_pct = init.get("rod_positions_pct")
        if rod_pct is not None:
            for r in self._physics.rods:
                r.target = float(rod_pct)
        pump_spd = init.get("pump_speeds_pct")
        if pump_spd is not None:
            for p in self._physics.pumps:
                p.speed = float(pump_spd)

        stype = self._scenario.get("type", "scripted")
        if stype == "random":
            self._schedule_next_random()

        self._emit_note(f"Scenario started: {self._scenario.get('name','')}")

    def stop(self):
        self._running = False
        self._scenario = None
        self._active_faults = []

    # ── Per-tick update ───────────────────────────────────────────────

    def tick(self):
        """Call once per physics tick (150ms). Checks triggers and fires actions."""
        if not self._running or not self._scenario:
            return

        elapsed = time.monotonic() - self._start_time
        stype   = self._scenario.get("type", "scripted")

        if stype == "scripted":
            self._tick_scripted(elapsed)
        elif stype == "random":
            self._tick_random(elapsed)

    def _tick_scripted(self, elapsed: float):
        events = self._scenario.get("events", [])
        for i, event in enumerate(events):
            if i in self._fired_events:
                continue
            trigger = event.get("trigger", {})
            if self._check_trigger(trigger, elapsed):
                self._fired_events.add(i)
                self._fire_action(event.get("action", {}))

    def _tick_random(self, elapsed: float):
        # Check and resolve active faults
        still_active = []
        for f in self._active_faults:
            if not self._check_fault_resolved(f):
                still_active.append(f)
            else:
                self._emit_note(f"Fault resolved: {f.get('desc','?')}")
        self._active_faults = still_active

        max_faults = self._scenario.get("max_concurrent_faults", 2)
        if elapsed >= self._fault_cooldown and len(self._active_faults) < max_faults:
            self._inject_random_fault()
            self._schedule_next_random()

    def _schedule_next_random(self):
        lo = float(self._scenario.get("min_interval_s", 60))
        hi = float(self._scenario.get("max_interval_s", 180))
        delay = random.uniform(lo, hi)
        self._fault_cooldown = (time.monotonic() - self._start_time) + delay

    # ── Trigger evaluation ────────────────────────────────────────────

    def _check_trigger(self, trigger: dict, elapsed: float) -> bool:
        ttype = trigger.get("type")
        if ttype == "time":
            return elapsed >= float(trigger.get("seconds", 0))
        if ttype == "condition":
            return self._eval_condition(trigger)
        return False

    def _eval_condition(self, trigger: dict) -> bool:
        var = trigger.get("variable", "")
        op  = trigger.get("operator", ">")
        val = float(trigger.get("value", 0))
        st  = self._physics.state

        var_map = {
            "power":        st.power,
            "temp":         st.temp,
            "pressure":     st.pressure,
            "xenon":        st.xenon,
            "iodine":       st.iodine,
            "void_fraction":st.void_fraction,
            "max_chan_t":   st.max_chan_t,
            "total_flow":   self._physics.total_flow(),
            "output_mw":    self._physics.current_output_mw(),
        }
        actual = var_map.get(var)
        if actual is None:
            return False

        ops = {">": actual > val, "<": actual < val,
               ">=": actual >= val, "<=": actual <= val, "==": actual == val}
        return ops.get(op, False)

    # ── Action execution ──────────────────────────────────────────────

    def _fire_action(self, action: dict):
        atype = action.get("type")
        if atype == "note":
            self._emit_note(action.get("message", ""))
        elif atype == "fault":
            self._apply_fault(action)
        elif atype == "set_rod":
            rod_id = action.get("id")
            target = float(action.get("target", 50))
            self._physics.cmd_set_rod_target(rod_id, target)
        elif atype == "set_pump":
            pump_id = int(action.get("id", 0))
            on      = bool(action.get("on", True))
            self._physics.cmd_set_pump(pump_id, on)
        elif atype == "scram":
            self._physics.cmd_scram()
        elif atype == "set_rod_mode":
            rod_id = action.get("id")
            mode   = action.get("mode", "auto")
            self._physics.cmd_set_rod_mode(rod_id, mode)
        else:
            log.warning("Unknown action type: %s", atype)

    def _apply_fault(self, action: dict):
        target = action.get("target")
        fault  = bool(action.get("fault", True))
        obj_id = action.get("id")
        if target == "pump":
            self._physics.cmd_set_pump_fault(int(obj_id) - 1, fault)
            desc = f"MCP-{obj_id} {'FAULT' if fault else 'FAULT CLEARED'}"
        elif target == "rod":
            rod_id = f"CR-{int(obj_id):02d}"
            self._physics.cmd_set_rod_mode(rod_id, "fault" if fault else "auto")
            desc = f"CR-{obj_id:02d} {'FAULT' if fault else 'FAULT CLEARED'}"
        elif target == "sensor_T":
            label = f"TS-{int(obj_id):02d}"
            self._physics.cmd_set_sensor_fault(label, fault)
            desc = f"{label} {'FAULT' if fault else 'FAULT CLEARED'}"
        elif target == "sensor_F":
            label = f"FS-{int(obj_id):02d}"
            self._physics.cmd_set_sensor_fault(label, fault)
            desc = f"{label} {'FAULT' if fault else 'FAULT CLEARED'}"
        else:
            log.warning("Unknown fault target: %s", target)
            return
        self._emit_note(f"FAULT INJECTED: {desc}")

    # ── Random fault injection ────────────────────────────────────────

    FAULT_TYPES = ["pump_trip", "rod_fault", "sensor_fault_T", "sensor_fault_F", "pump_fault"]

    def _inject_random_fault(self):
        weights_map = self._scenario.get("fault_weights", {})
        pool = [ft for ft in self.FAULT_TYPES if weights_map.get(ft, 0) > 0]
        if not pool:
            return
        weights = [weights_map.get(ft, 0) for ft in pool]
        ft = random.choices(pool, weights=weights, k=1)[0]

        ph = self._physics
        fault_record = {"type": ft}

        if ft == "pump_trip":
            available = [p for p in ph.pumps if p.on and not p.fault]
            if not available:
                return
            p = random.choice(available)
            ph.cmd_set_pump(p.id, False)
            fault_record["desc"] = f"MCP-{p.id+1} AUTO TRIP"
            fault_record["pump_id"] = p.id

        elif ft == "pump_fault":
            available = [p for p in ph.pumps if not p.fault and p.on]
            if not available:
                return
            p = random.choice(available)
            ph.cmd_set_pump_fault(p.id, True)
            fault_record["desc"] = f"MCP-{p.id+1} SEAL FAILURE"
            fault_record["pump_id"] = p.id

        elif ft == "rod_fault":
            available = [r for r in ph.rods if r.mode == "auto" and 5 < r.pos < 95]
            if not available:
                return
            rod = random.choice(available)
            ph.cmd_set_rod_mode(rod.id, "fault")
            fault_record["desc"] = f"{rod.id} DRIVE FAULT"
            fault_record["rod_id"] = rod.id

        elif ft == "sensor_fault_T":
            available = [s for s in ph.sensors if s.type == "T" and not s.fault]
            if not available:
                return
            sen = random.choice(available)
            ph.cmd_set_sensor_fault(sen.label, True)
            fault_record["desc"] = f"{sen.label} SIGNAL LOST"
            fault_record["sensor_label"] = sen.label

        elif ft == "sensor_fault_F":
            available = [s for s in ph.sensors if s.type == "F" and not s.fault]
            if not available:
                return
            sen = random.choice(available)
            ph.cmd_set_sensor_fault(sen.label, True)
            fault_record["desc"] = f"{sen.label} DETECTOR FAILURE"
            fault_record["sensor_label"] = sen.label

        self._active_faults.append(fault_record)
        desc = fault_record.get("desc", ft)
        self._emit_note(f"INCIDENT: {desc}")
        log.info("Random fault injected: %s", desc)

    def _check_fault_resolved(self, f: dict) -> bool:
        ph = self._physics
        ft = f.get("type")
        if ft == "pump_trip":
            p = ph.pumps[f.get("pump_id", 0)]
            return p.on
        if ft == "pump_fault":
            p = ph.pumps[f.get("pump_id", 0)]
            return not p.fault
        if ft == "rod_fault":
            rod = next((r for r in ph.rods if r.id == f.get("rod_id")), None)
            return not rod or rod.mode != "fault"
        if ft in ("sensor_fault_T", "sensor_fault_F"):
            lbl = f.get("sensor_label", "")
            sen = next((s for s in ph.sensors if s.label == lbl), None)
            return not sen or not sen.fault
        return False

    @property
    def active_fault_count(self) -> int:
        return len(self._active_faults)

    @property
    def elapsed(self) -> float:
        if not self._running:
            return 0.0
        return time.monotonic() - self._start_time
