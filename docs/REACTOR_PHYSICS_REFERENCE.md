# RBMK-1000 Reactor Physics Reference Guide

For Control Systems Training — Apprentice Reference Document

---

## 1. Normal Operating Parameters

Understanding these values is essential for effective reactor control via PLC.

| Parameter | Safe Operating Range | Alarm Threshold | Danger Zone |
|---|---|---|---|
| Reactor Power | 40–88% | >88% | >100% (prompt criticality risk) |
| Average Core Temp | 200–305°C | >310°C (hottest channel) | >380°C |
| Steam Pressure | 5.5–8.0 MPa | >8.0 MPa | >10 MPa |
| Void Fraction | 0–20% | >20% | >50% (positive feedback) |
| Total Coolant Flow | 55–100% | <35% | <20% |
| Electrical Output | 350–900 MWe | — | >1008 MWe (rated max) |
| Average Xenon-135 | 0–0.20 | >0.30 (pit) | >0.45 (restart very difficult) |

**Target operating point for exercises:** 65–80% rod withdrawal, 4 pumps at 70–80% speed.
This gives approximately 600–820 MWe electrical output.

---

## 2. Core Destruction Conditions

These values trigger an irreversible meltdown — the simulation cannot be continued without a COLD RESTART.

### 2.1 Prompt Criticality (Power Excursion)
- **Trigger**: Reactor power exceeds **112%**
- **Cause**: Positive void coefficient feedback loop — void fraction rises, boosting flux, which raises temperature, which increases void
- **Typical scenario**: Low power operation with most rods withdrawn and coolant flow reduced
- **Real-world analogy**: Chernobyl Unit 4, 01:23:45, 26 April 1986
- **Warning signs**: Power rising rapidly despite rod insertion, void fraction >40%, all alarms active

### 2.2 Zircaloy Channel Overtemperature
- **Trigger**: Any single fuel channel exceeds **420°C**
- **Cause**: Loss of coolant flow while reactor remains at power
- **Typical scenario**: All pumps tripped or faulted, reactor not scramming
- **Warning signs**: Max channel temp rising on HI CORE TEMP alarm, rising pressure, void = 100%
- **Time to meltdown**: Approximately 60–90 seconds after total flow loss at 75% power (varies with difficulty)

---

## 3. Key Process Relationships

### 3.1 The Control Rod — Power Relationship

Control rods absorb neutrons. Withdrawing a rod (increasing position %) increases local flux. The relationship is **non-linear** due to spatial effects:

- A rod at 50% withdrawal has more effect than a rod at 10% (moving from 10% to 15% has less impact than from 50% to 55%)
- Each rod influences a roughly 9-cell radius around it (Gaussian spatial decay, L=3.2 cells)
- Average rod withdrawal needed for power levels:
  - 30% power: ~40–50% average withdrawal
  - 60% power: ~60–70% average withdrawal
  - 80% power: ~75–85% average withdrawal
  - >85% power: requires >90% average (xenon permitting)

**PLC control implication**: A proportional (P) controller on average rod position vs. power will work but will have steady-state error. A PI controller eliminates the offset. Avoid pure derivative (D) control on power — neutron flux responds very quickly and derivative action causes hunting.

### 3.2 The Coolant Flow — Temperature Relationship

Increasing pump speed (more flow) cools the core:

```
Equilibrium temperature ≈ coolant_inlet + (power × heat_coefficient) / flow_fraction
```

At normal difficulty:
- 75% power, 80% flow → T ≈ 281°C (approaching boiling onset)
- 75% power, 50% flow → T ≈ 312°C (29% void)
- 75% power, 35% flow → T ≈ 344°C (66% void — dangerous)

**PLC control implication**: A PID controller on core temperature by adjusting pump speed is a classic cascade control exercise. The inner loop (flow → temperature) responds faster than the outer loop (temperature → power). Integral windup becomes a problem if the controller saturates (e.g. pumps already at maximum speed but temperature still rising due to excessive rod withdrawal).

### 3.3 The Void Fraction — Power Feedback (Positive Void Coefficient)

The RBMK has a **positive void coefficient** — unlike most western reactor designs. When coolant boils and void fraction increases, neutron absorption by water decreases. More neutrons are available for fission, so power increases. This is a **positive feedback loop**:

```
↑ void → ↑ flux → ↑ power → ↑ heat → ↑ temperature → ↑ void ...
```

This loop is normally controllable at high power (where the void coefficient is smaller). At **low power (<20%)** the void coefficient becomes significantly larger (approximately double). This means small changes in flow produce large power swings.

The simulator uses a power-dependent void coefficient: `k = 0.35 + (1 − flux) × 0.45`. At low flux, k = 0.80; at full flux, k = 0.35.

**PLC control implication**: A simple P controller on power by rod position is not sufficient at low power — the positive feedback makes the process gain change sign depending on void state. This is an excellent demonstration of why gain scheduling or adaptive control may be needed for inherently unstable processes.

### 3.4 The Turbine Efficiency — Void Relationship

Electrical output is not simply proportional to reactor power:

```
MWe = Thermal_Power_MW × Turbine_Efficiency
Turbine_Efficiency = 31.5% × (1 − void_fraction × 0.35)
```

At 0% void: 31.5% efficiency → max 1008 MWe at rated power
At 50% void: 26.0% efficiency → only 830 MWe at rated power
At 100% void: 20.5% efficiency → only 655 MWe at rated power

This means chasing higher reactor power by allowing void to increase can actually *reduce* electrical output. The optimal operating point balances rod withdrawal and flow rate to minimise void while maintaining sufficient power.

**PLC control implication**: The cascade control objective should be to maximise MWe, not power%. The controller must account for turbine efficiency. This introduces a non-trivial optimisation problem — good for demonstrating feedforward and ratio control concepts.

---

## 4. Xenon Poisoning and the Iodine Pit

### 4.1 How Xenon Builds Up

Fission creates Iodine-135, which decays into Xenon-135. Xenon absorbs neutrons and suppresses reactor power — this is called **xenon poisoning**. At steady state, burnout (xenon absorbing a neutron and becoming a non-absorbing isotope) balances production.

At equilibrium (normal difficulty settings):
- Iodine-135: normalised value ≈ reactor_power (0–1)
- Xenon-135: normalised value ≈ 0.15 at full power
- Power suppression from xenon: ~18%

### 4.2 The Iodine Pit

When reactor power is rapidly reduced (or the reactor SCRAMS), the sequence is:
1. Xenon burnout stops (no flux to destroy xenon)
2. Iodine inventory continues decaying into xenon (~15s half-life at normal difficulty)
3. Xenon rises even though the reactor is shut down
4. Peak xenon ≈ 0.50 at approximately 22 seconds after SCRAM
5. Maximum power suppression: **60%** — restart to more than 40% power is nearly impossible
6. Xenon decays naturally — full recovery takes ~30–40 seconds (real time, normal difficulty)

**Xenon pit severity depends on how long the reactor was at power before shutdown.** A reactor that ran at full power for several minutes will have more iodine inventory than one that just started up, so the subsequent xenon peak will be higher.

### 4.3 Burnout at High Power

At high flux, xenon is destroyed by neutron absorption faster than it is produced. This "burns out" the xenon:
- At >70% flux, xenon decreases significantly
- At <30% flux, xenon can actually increase even with the reactor running
- This is why the PLC must maintain sufficient power to keep xenon at bay — repeatedly dipping to low power builds up xenon that is increasingly difficult to overcome

**PLC exercise**: Program a minimum power setpoint interlock that prevents the operator from reducing power below 25% once xenon is above 0.20. This simulates a real plant safety function.

---

## 5. Alarm Reference

| Alarm ID | Condition | Response |
|---|---|---|
| HI REACTOR POWER | power > 88% | Insert control rods, check void fraction |
| HI CORE TEMP | max channel > 310°C | Increase coolant flow, reduce power |
| HIGH VOID FRACTION | void > 20% | Increase coolant flow immediately |
| LO COOLANT FLOW | total flow < 35% | Check pump status, restart tripped pumps |
| PUMP TRIP | any pump off or faulted | Restore pump, monitor temperature |
| AZ-5 SCRAM | SCRAM active | Monitor xenon buildup, await clearance |
| CORE DAMAGE | meltdown occurred | Cold restart required |
| HI STEAM PRESSURE | pressure > 8.0 MPa | Reduce power or increase flow |
| XENON/IODINE PIT | xenon > 0.30 | Do not reduce power — wait for xenon to decay |

---

## 6. Control Interlocks (Recommended for PLC Exercises)

These are safety functions that apprentices can implement on their PLC as exercises:

### 6.1 Minimum Flow Interlock
- **Function**: If total coolant flow drops below 40%, automatically increase pump speed setpoints to maximum
- **Modbus signals needed**: `total_flow` (IR addr 3), `pump_1-4_run` (coils), `pump_1-4_speed` (HR addr 100–103)
- **PLC language**: Ladder — one rung per pump using a normally-open contact on a flow limit switch (derived from the analog input)

### 6.2 High Temperature SCRAM
- **Function**: If max channel temperature exceeds 380°C and high temperature alarm is active, trigger AZ-5 SCRAM
- **Modbus signals needed**: `alarm_hitemp` (DI addr 11), `scram_command` (coil addr 0)
- **PLC language**: One rung — NO contact on alarm DI bit, coil output on SCRAM coil

### 6.3 Void Fraction Power Reduction
- **Function**: If void fraction exceeds 30%, automatically insert all controllable rods by 10%
- **Modbus signals needed**: `void_fraction` (IR addr 4), CR setpoints (HR addr 0–29)
- **PLC language**: Uses analogue comparator function block, then writes to holding registers on trigger

### 6.4 Low Power Xenon Guard
- **Function**: If xenon is above 0.25, prevent operator from reducing power below 30%
- **Modbus signals needed**: `avg_xenon` (IR addr 9), `reactor_power` (IR addr 0), CR setpoints (HR)
- **PLC language**: FBD — analogue comparator with a clamp on minimum CR setpoint

### 6.5 Pump Start-Up Interlock
- **Function**: Prevent a pump from starting if the reactor is at power >50% and only one pump is running (sudden flow surge could cause a power transient)
- **Modbus signals needed**: `pump_1-4_running` (DI addr 30, 33, 36, 39), `reactor_power` (IR addr 0), `pump_1-4_run` coils
- **PLC language**: Count running pumps using function block, compare with power level, gate the start coil

---

## 7. PID Tuning Guidelines

### 7.1 Temperature Control Loop
- **Process Variable**: `avg_core_temp` (IR addr 5)
- **Manipulated Variable**: Average pump speed setpoint (HR addr 100–103)
- **Typical gain settings** (for Normal difficulty):
  - Proportional: Kp = 0.8 (% speed / °C)
  - Integral: Ti = 15s
  - Derivative: Td = 0 (avoid — pump speed hunting)
- **Process deadtime**: ~1–2 seconds (thermal lag)
- **Process time constant**: ~8–15 seconds (depends on flow)

### 7.2 Power Control Loop
- **Process Variable**: `reactor_power` (IR addr 0)
- **Manipulated Variable**: Average CR withdrawal setpoint (HR addr 0–29)
- **Typical gain settings**:
  - Proportional: Kp = 2.0 (% rod / % power error)
  - Integral: Ti = 20s
  - Derivative: Td = 5s (helps with fast transients but risks instability)
- **Warning**: Positive void coefficient makes this loop conditionally unstable. Reduce Kp when void fraction is high.

### 7.3 Cascade Control: Power → Temperature → Flow
- Outer loop (power setpoint → temperature setpoint): slow (Ti = 30s, Kp = 0.5)
- Inner loop (temperature setpoint → pump speed): fast (Ti = 8s, Kp = 1.2)
- Ensure inner loop is 3–10× faster than outer loop to maintain cascade stability

---

## 8. Common Student Mistakes

1. **Using only P control on power** — integral windup at xenon equilibrium causes sustained offset
2. **Setting rod targets directly proportional to desired power** — ignores xenon suppression, spatial effects, and void feedback
3. **Not monitoring void fraction** — void rising causes positive feedback that overwhelms the controller
4. **Reducing power below 20% without adjusting controller gains** — the process gain doubles at low power (void coefficient stronger), causing instability with nominal gains
5. **Ignoring the iodine pit after a power reduction** — trying to recover power against rising xenon by withdrawing all rods leaves the reactor with no shutdown margin (equivalent to Chernobyl ORM condition)
6. **Integral windup on pump speed during flow alarms** — when pumps are tripped and integrator accumulates, restart causes sudden speed overshoot and power transient
