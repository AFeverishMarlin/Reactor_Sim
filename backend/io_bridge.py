"""
io_bridge.py — Modbus TCP server bridging the physics engine to PLCs.

Register layout (all addresses user-configurable via io_map.json):

COILS (read/write discrete):
  scram_command, pump_1-4_run

DISCRETE INPUTS (read-only discrete):
  reactor_running, scram_active, meltdown, alarm_*, pump_* status,
  CR-01..CR-30 status bits (at_min, at_max, manual, fault),
  fuel channel installed bits

INPUT REGISTERS (read-only analog, 0-32767):
  plant-wide values, T-sensors, F-sensors, CR positions, pump speeds

HOLDING REGISTERS (read/write analog, 0-32767):
  CR setpoints (ignored if rod in manual/fault), pump speed setpoints

All engineering→raw scaling uses io_map.json scale_min/max or scale_factor.
"""

import asyncio
import time
import logging
import struct
from typing import TYPE_CHECKING

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusSlaveContext,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer
from pymodbus.device import ModbusDeviceIdentification
from physics import CH_FUEL, ROWS, COLS, N  # noqa: E402

if TYPE_CHECKING:
    from physics import ReactorPhysics
    from config_manager import ConfigManager

log = logging.getLogger(__name__)

MAX_COILS  = 1000
MAX_DI     = 2000
MAX_IR     = 1000
MAX_HR     = 1000


class ReactorDataBlock(ModbusSequentialDataBlock):
    """
    Custom data block that intercepts writes and routes them to
    the physics engine. Reads always return current physics state
    (updated each tick by update_from_physics).
    """

    def __init__(self, address, values, physics, cfg, block_type):
        super().__init__(address, values)
        self._physics   = physics
        self._cfg       = cfg
        self._block_type = block_type   # 'coil' | 'hr'
        self._pending_writes = asyncio.Queue()

    def setValues(self, address, values):
        """Called by pymodbus when PLC writes to this block."""
        super().setValues(address, values)
        # Queue for processing in the physics loop
        self._pending_writes.put_nowait((address, values))  # already 0-based
        # Stamp activity time so the bridge knows a client is connected
        if hasattr(self, "_bridge"): self._bridge.last_request_time = time.monotonic()

    def getValues(self, address, count=1):
        """Called by pymodbus when PLC reads from this block."""
        if hasattr(self, "_bridge"): self._bridge.last_request_time = time.monotonic()
        return super().getValues(address, count)

    async def process_pending_writes(self):
        """Drain the write queue and apply to physics. Call from asyncio task."""
        while not self._pending_writes.empty():
            addr, values = await self._pending_writes.get()
            if self._block_type == "coil":
                self._handle_coil_write(addr, values)
            elif self._block_type == "hr":
                self._handle_hr_write(addr, values)

    # ── Coil writes ───────────────────────────────────────────────────

    def _handle_coil_write(self, addr: int, values: list):
        io = self._cfg.io_map
        coils = io.get("coils", {})

        def _matches(name):
            return coils.get(name, {}).get("address") == addr

        if _matches("scram_command") and values[0]:
            self._physics.cmd_scram()
            log.info("Modbus: SCRAM command received")
            return

        for i, pump_name in enumerate(["pump_1_run","pump_2_run","pump_3_run","pump_4_run"]):
            if _matches(pump_name):
                ok, msg = self._physics.cmd_set_pump(i, bool(values[0]))
                if not ok:
                    log.warning("Modbus pump write rejected: %s", msg)
                return

    # ── Holding register writes ───────────────────────────────────────

    def _handle_hr_write(self, addr: int, values: list):
        """Handle FC16 multi-register write. values may contain multiple
        registers written in one transaction — iterate and apply each."""
        io  = self._cfg.io_map
        hrs = io.get("holding_registers", {})

        cr_base  = hrs.get("cr_setpoint_base",   {}).get("address", 0)
        cr_def   = hrs.get("cr_setpoint_base",   {})
        pmp_base = hrs.get("pump_setpoint_base", {}).get("address", 100)
        pmp_def  = hrs.get("pump_setpoint_base", {})

        for offset, raw_val in enumerate(values):
            a = addr + offset

            # Control rod setpoints (cr_base .. cr_base+29)
            if cr_base <= a <= cr_base + 29:
                rod_idx = a - cr_base
                if rod_idx < len(self._physics.rods):
                    rod = self._physics.rods[rod_idx]
                    eng = self._cfg.raw_to_eng(raw_val, cr_def)
                    ok, msg = self._physics.cmd_set_rod_target(rod.id, eng)
                    if not ok:
                        log.debug("Modbus rod write rejected [%s]: %s", rod.id, msg)
                continue

            # Pump speed setpoints (pmp_base .. pmp_base+3)
            if pmp_base <= a <= pmp_base + 3:
                pump_idx = a - pmp_base
                eng = self._cfg.raw_to_eng(raw_val, pmp_def)
                ok, msg = self._physics.cmd_set_pump_speed(pump_idx, eng)
                if not ok:
                    log.debug("Modbus pump speed write rejected: %s", msg)
                continue


class ModbusBridge:
    """
    Manages the Modbus TCP server and keeps its registers synchronised
    with the physics engine every tick.
    """

    def __init__(self, physics, cfg):
        self._physics: ReactorPhysics = physics
        self._cfg:     ConfigManager  = cfg
        self._server   = None
        self._context  = None
        self._coil_block = None
        self._hr_block   = None
        # Track recent Modbus activity to show connection status in UI
        self.last_request_time: float = 0.0   # monotonic time of last request
        self.client_active: bool = False       # True if request in last 5s

    def _build_context(self):
        co = ReactorDataBlock(0, [False] * MAX_COILS, self._physics, self._cfg, "coil")
        di = ModbusSequentialDataBlock(0, [False] * MAX_DI)
        ir = ModbusSequentialDataBlock(0, [0]     * MAX_IR)
        hr = ReactorDataBlock(0, [0]     * MAX_HR, self._physics, self._cfg, "hr")
        # Give reactive blocks a reference to bridge so they can stamp activity
        co._bridge = self
        hr._bridge = self

        self._coil_block = co
        self._hr_block   = hr
        self._di_block   = di
        self._ir_block   = ir

        slave = ModbusSlaveContext(co=co, di=di, ir=ir, hr=hr, zero_mode=True)
        return ModbusServerContext(slaves=slave, single=True)

    def update_client_status(self):
        """Update client_active flag — True if a request arrived in last 5s."""
        self.client_active = (
            self.last_request_time > 0 and
            (time.monotonic() - self.last_request_time) < 5.0
        )

    def update_from_physics(self):
        """
        Synchronise all read-only register values from current physics state.
        Called after each physics tick.
        """
        ph  = self._physics
        cfg = self._cfg
        io  = cfg.io_map
        st  = ph.state

        di  = self._di_block
        ir  = self._ir_block

        def _s(reg_def, value):
            """Scale engineering value to raw int."""
            return cfg.eng_to_raw(value, reg_def)

        # ── Discrete Inputs ───────────────────────────────────────────
        defs = io.get("discrete_inputs", {})

        def _di(name, val):
            addr = defs.get(name, {}).get("address")
            if addr is not None:
                di.setValues(addr, [bool(val)])

        _di("reactor_running",  st.power > 5)
        _di("scram_active",     st.scramming or st.scram_done)
        _di("meltdown",         st.meltdown)
        _di("alarm_hipower",    st.alarms.get("hipower", False))
        _di("alarm_hitemp",     st.alarms.get("hitemp",  False))
        _di("alarm_void",       st.alarms.get("void",    False))
        _di("alarm_locool",     st.alarms.get("locool",  False))
        _di("alarm_pumptrip",   st.alarms.get("pumptrip",False))
        _di("alarm_scram",      st.alarms.get("scram",   False))
        _di("alarm_damage",     st.alarms.get("damage",  False))
        _di("alarm_hipress",    st.alarms.get("hipress", False))
        _di("alarm_xenonpit",   st.alarms.get("xenonpit",False))

        # Pump status (3 DI per pump)
        pump_names = ["pump_1", "pump_2", "pump_3", "pump_4"]
        for i, p in enumerate(ph.pumps):
            _di(f"pump_{i+1}_running", p.on and not p.fault)
            _di(f"pump_{i+1}_fault",   p.fault)

        # Control rod status (4 DI per rod)
        cr_base_addr = defs.get("cr_status_base", {}).get("address", 100)
        for idx, rod in enumerate(ph.rods):
            base = cr_base_addr + idx * 4
            di.setValues(base + 0, [rod.pos < 1.0])      # at_min
            di.setValues(base + 1, [rod.pos > 99.0])     # at_max
            di.setValues(base + 2, [rod.mode == "manual"])
            di.setValues(base + 3, [rod.mode == "fault"])

        # Fuel channel installed bits
        fuel_base = defs.get("fuel_base", {}).get("address", 300)
        fuel_idx = 0
        for i in range(N):
            if ph.ch_type[i] in (CH_FUEL, 3, 4):  # fuel or sensor channels
                di.setValues(fuel_base + fuel_idx, [not ph.ch_removed[i]])
                fuel_idx += 1

        # ── Input Registers ───────────────────────────────────────────
        r_defs = io.get("input_registers", {})

        def _ir(name, value):
            rd = r_defs.get(name, {})
            addr = rd.get("address")
            if addr is not None:
                ir.setValues(addr, [_s(rd, value)])

        _ir("reactor_power",  st.power)
        _ir("output_mw",      ph.current_output_mw())
        _ir("steam_pressure", st.pressure)
        _ir("total_flow",     ph.total_flow())
        _ir("void_fraction",  st.void_fraction * 100)
        _ir("avg_core_temp",  st.temp)
        _ir("max_chan_temp",   st.max_chan_t)
        _ir("turbine_eff",    ph.turbine_efficiency())
        _ir("avg_iodine",     st.iodine)
        _ir("avg_xenon",      st.xenon)

        # T-sensors
        ts_base = r_defs.get("tsensor_base", {}).get("address", 100)
        ts_def  = r_defs.get("tsensor_base", {})
        tsensors = [s for s in ph.sensors if s.type == "T"]
        for idx, s in enumerate(tsensors):
            raw = 32767 if s.fault or s.value is None else _s(ts_def, s.value)
            ir.setValues(ts_base + idx, [raw])

        # F-sensors
        fs_base = r_defs.get("fsensor_base", {}).get("address", 150)
        fs_def  = r_defs.get("fsensor_base", {})
        fsensors = [s for s in ph.sensors if s.type == "F"]
        for idx, s in enumerate(fsensors):
            raw = 32767 if s.fault or s.value is None else _s(fs_def, s.value)
            ir.setValues(fs_base + idx, [raw])

        # Control rod positions
        cr_pos_base = r_defs.get("cr_pos_base", {}).get("address", 200)
        cr_pos_def  = r_defs.get("cr_pos_base", {})
        for idx, rod in enumerate(ph.rods):
            ir.setValues(cr_pos_base + idx, [_s(cr_pos_def, rod.pos)])

        # Pump actual speeds
        ps_base = r_defs.get("pump_speed_base", {}).get("address", 250)
        ps_def  = r_defs.get("pump_speed_base", {})
        for idx, p in enumerate(ph.pumps):
            speed = 0.0 if p.fault else p.speed
            ir.setValues(ps_base + idx, [_s(ps_def, speed)])

        # ── Sync Holding Register readbacks ──────────────────────────
        # Allow PLC to read back the setpoints it wrote
        hr  = self._hr_block
        h_defs = io.get("holding_registers", {})
        cr_sp_base = h_defs.get("cr_setpoint_base", {}).get("address", 0)
        cr_sp_def  = h_defs.get("cr_setpoint_base", {})
        for idx, rod in enumerate(ph.rods):
            hr.values[cr_sp_base + idx] = _s(cr_sp_def, rod.target)

        pm_sp_base = h_defs.get("pump_setpoint_base", {}).get("address", 100)
        pm_sp_def  = h_defs.get("pump_setpoint_base", {})
        for idx, p in enumerate(ph.pumps):
            hr.values[pm_sp_base + idx] = _s(pm_sp_def, p.speed)

    async def run(self):
        """Start the Modbus TCP server."""
        net = self._cfg.network.get("modbus_tcp", {})
        if not net.get("enabled", True):
            log.info("Modbus TCP disabled in config")
            return

        host = net.get("host", "0.0.0.0")
        port = net.get("port", 502)
        uid  = net.get("unit_id", 1)

        self._context = self._build_context()

        identity = ModbusDeviceIdentification()
        identity.VendorName       = "RBMK Training Simulator"
        identity.ProductCode      = "RBMK-1000"
        identity.ProductName      = "Reactor Control Training System"
        identity.ModelName        = "RBMK-1000 Unit 4"
        identity.MajorMinorRevision = "1.0"

        # Register the tick callback so registers update each physics tick
        self._physics.on_tick(self.update_from_physics)

        log.info("Starting Modbus TCP server on %s:%d (unit %d)", host, port, uid)
        try:
            await StartAsyncTcpServer(
                context=self._context,
                identity=identity,
                address=(host, port),
            )
        except PermissionError:
            log.error(
                "Cannot bind Modbus on port %d — try a port >1024 or run as admin", port
            )
        except Exception as e:
            log.error("Modbus server error: %s", e)

    async def write_drain_loop(self):
        """Drain pending PLC writes into the physics engine at each tick."""
        while True:
            await asyncio.sleep(0.050)
            if self._coil_block:
                await self._coil_block.process_pending_writes()
            if self._hr_block:
                await self._hr_block.process_pending_writes()
