# Trigger Command Reference

Two different instruments, two different trigger command sets. **Do not copy
commands from one to the other.**

| Section | Source of truth |
|---|---|
| Keysight DSO-X 3024A | **Measured** on the connected unit, firmware `02.38.2014110300` |
| Rigol MSO5000 | Extracted from `Rigol_MSO5000_SCPI_Commands.txt` / `_Indexes.txt` |

The repo's `AGENTS.md` points at the Rigol files as authoritative. That is
correct for the Rigol, and misleading for the Keysight: of the 23 trigger-mode
names accepted by the Rigol, only 2 exist under the same spelling here.

---

# Keysight DSO-X 3024A (measured)

## Supported trigger modes: 9

Method: write `:TRIGger:MODE <name>`, then read the error queue. Accepted
leaves it clean; rejected answers `-224,"Illegal parameter value"` and
**quietly keeps the previous mode**, which is why an unknown mode must never be
sent blind.

| Mode | Long spelling | Reads back as | Mode-specific parameters |
|---|---|---|---|
| EDGE | `EDGE` | `EDGE` | `source` `slope` `level` `coupling` |
| GLITch | `GLITch` | `GLIT` | `source` `polarity` `qualifier` `level` |
| PATTern | `PATTern` | `PATT` | `qualifier` |
| RUNT | `RUNT` | `RUNT` | `source` `polarity` `qualifier` `time` |
| TRANsition | `TRANsition` | `TRAN` | `source` `slope` `qualifier` `time` |
| TV | `TV` | `TV` | `source` `polarity` `mode` `standard` `line` |
| USB | `USB` | `USB` | none identified (see note) |
| DELay | `DELay` | `DEL` | none identified (see note) |
| SHOLd | `SHOLd` | `SHOL` | `slope` |

Notes:

- **This means pulse-width triggering is `GLITch`, not `PULSe`.** `PULSe` does
  not exist here at all, so `:TRIGger:PULSe:*` returns `-113`.
- `USB` and `DELay` are accepted as modes, but none of the 18 parameter names
  probed for them responded, so their parameter names remain unidentified.
- Each mode keeps **its own level**: changing `TRIGger:MODE` does not carry the
  EDGE level over to GLIT.
- The canonical SCPI node for each parameter is the JSON item name
  capitalised in the usual SCPI way: `source` → `SOURce`, `level` → `LEVel`,
  `qualifier` → `QUALifier`, `standard` → `STANdard`, and so on. Prefix it with
  the mode, e.g. `:TRIGger:EDGE:LEVel`.

## Mode-independent commands

All verified present:

| Command | Notes |
|---|---|
| `:TRIGger:MODE` | see the table above |
| `:TRIGger:SWEep` | **only `AUTO` and `NORMal`** — `SINGle` is rejected with `-224` |
| `:TRIGger:COUPling` | read back `DC` |
| `:TRIGger:HOLDoff` | read back `+4.00000000E-008` |
| `:TRIGger:NREJect` | read back `0` |
| `:TRIGger:HFReject` | read back `0` |
| `:TRIGger:LEVel` | the active mode's level |
| `:TRIGger:SOURce` | the active mode's source |

## Status and event registers

`:TRIGger:STATus?` **does not exist here** (it does on the Rigol). Use the
event registers instead:

| Command | Meaning | Read back |
|---|---|---|
| `:AER?` | arm event register | `+1` |
| `:TER?` | trigger event register | `+1` |

This is the change to watch for if you port a "wait for the trigger" loop: the
Rigol polls `:TRIGger:STATus?`, this instrument does not have it.

## Action commands

Verified by sending them (they change state, so restore afterwards):

| Command | Result |
|---|---|
| `:RUN` / `:STOP` | accepted — the front-panel Run/Stop key |
| `:SINGle` | accepted — **this is how you arm one acquisition**, and it sets `SWEep` to `NORM` |
| `:DIGitize` | accepted — acquire once and stop, in one command |
| `:TRIGger:FORCe` | header exists; forces a trigger, and works once armed (in sweep AUTO it answers `-221,"Settings conflict"`) |
| `:TFORce` | **`-113`** — that is the Rigol spelling, not this one |

There are **no query forms** for the acquisition actions: `:SINGle?`, `:RUN?`,
`:STOP?`, `:DIGitize?` and `:WAVeform:STATus?` are all unavailable. To learn
what the acquisition is doing you have to use the event registers below.

### Arming a single acquisition (the front-panel Single key)

The command is **`:SINGle`**.

**`TRIGger:SWEep` cannot do it.** It accepts only `AUTO` and `NORMal`;
`:TRIGger:SWEep SINGle` is rejected with `-224` and leaves the previous value in
place. This is a genuine difference from the Rigol, where sweeping once is the
normal route, and it is exactly the kind of thing that gets ported by mistake.

`:RUN` returns to continuous acquisition; `:STOP` halts it.

### Waiting for a single acquisition to finish

Measured on this unit:

- **`:TER?` latches when the trigger occurs, so polling it works.** After
  `:SINGle` followed by `:TRIGger:FORCe`, `:TER?` returned `+1` within 0.02 s.
- **Reading `:AER?`/`:TER?` clears them.** Right after `:SINGle`, `:AER?` read
  `+1` once and `+0` on the very next read. So use a poll-until-`+1` loop, and
  `*CLS` before arming so a stale latch cannot be mistaken for a new trigger.
- **`:AER?` reads `+1` immediately after `:SINGle`** — that is the arming event,
  and it is the closest thing to a "now armed" confirmation.
- **`*OPC?` is useless for this.** It answered immediately after `:SINGle`; it
  does not wait for the acquisition to complete.
- **`:TRIGger:SWEep?` is not a "done" indicator.** It reads `NORM` right after
  `:SINGle`, but it flipped back to `AUTO` at inconsistent points across runs,
  so it cannot be relied on.
- **An armed scope with no trigger waits indefinitely.** With this setup a plain
  `:SINGle` never completed in 1 s -- `:TER?` stayed `+0` -- because the signal
  never crossed the EDGE level on CH2. Any wait loop needs a timeout.
- `:TRIGger:STATus?` is **not available here** (the Rigol has it); `:AER?`,
  `:TER?` and `:OPERegister?` all exist.

Practical shape of a capture-on-trigger loop:

```python
inst.write("*CLS")            # drop stale latches
inst.write(":SINGle")         # arm; SWEep becomes NORM
deadline = time.time() + timeout
while time.time() < deadline:
    if inst.query(":TER?").strip() == "+1":
        break                 # trigger occurred, waveform is ready
    time.sleep(0.02)
else:
    ...                       # still armed, no trigger: give up loudly
inst.write(":RUN")            # back to continuous
```

## Verified-absent

Every bus / serial-decode trigger mode was rejected:

`CAN` `LIN` `I2C` `SPI` `UART` `FLEXray` `MIL1553` `SEQuence` `EBUS`
`ARINC429` `SENT` `AUDio` `I2S` `RS232` `IIS` `M1553`

**This unit has no bus-decode triggers at all.** The corresponding option is
evidently not licensed, so the Rigol's eight bus-trigger families have no
counterpart here. Also absent: `:TRIGger:EXTernal:*`, `:TRIGger:PULSe:*`,
`:TRIGger:VIDeo:*`, `:TRIGger:DURation:*`, `:TRIGger:TIMeout:*`,
`:TRIGger:NEDGe:*`, `:TRIGger:WINDows:*`, `:TRIGger:SLOPe:*`, and
`:SYSTem:HELP:HEADers?` (which would have listed everything at once; it is not
implemented and hangs the read until timeout).

## How these were measured

Read-only where possible; anything that changes state is restored in a
`finally` block and verified.

```python
i.write("*CLS")                       # clear the queue first
i.write(":TRIGger:MODE PULSe")        # candidate
print(i.query(":SYSTem:ERRor?"))      # "+0,\"No error\"" = accepted, else rejected
```

Two traps worth remembering:

1. **Run state-changing probes as a background job with `python -u`.** A
   foreground sweep that times out gets killed, the `finally` never runs, and
   the instrument is left in whatever mode was last written. That happened once
   here: the unit was left in `USB` mode.
2. **A rejected query leaves the bus unterminated.** After `-113`, the *next*
   read hangs until it times out. Send `*CLS` and drain before moving on.
3. Never put `:AUToscale` in a "probe which commands exist" list — it is
   accepted, and it rewrites your channel scales, timebase and offsets.

---

# Rigol MSO5000 (from the repo reference)

139 trigger tokens in `Rigol_MSO5000_SCPI_Commands.txt`: **137 settable**, 139
queryable. Only two exist as queries alone: `:TRIGger:POSition?` and
`:TRIGger:STATus?`.

`Rigol_MSO5000_SCPI_Indexes.txt` is a **value dictionary** — every parameter
lists its legal values with their numeric codes, e.g. `:TRIGger:SWEep` =
`AUTO (0), NORMal (1), SINGle (2)`. Use it for valid values.

## Global (5 settable, 2 query-only)

| Command | Values |
|---|---|
| `:TRIGger:MODE` | EDGE, PULSe, SLOPe, VIDeo, PATTern, DURation, TIMeout, RUNT, WINDow, DELay, SETup, NEDGe, RS232, IIC, SPI, CAN, FLEXray, LIN, IIS, M1553 |
| `:TRIGger:COUPling` | DC (0), AC (1), LFReject (3), HFReject (4) |
| `:TRIGger:SWEep` | AUTO (0), NORMal (1), SINGle (2) |
| `:TRIGger:HOLDoff` | holdoff time |
| `:TRIGger:NREJect` | noise rejection |
| `:TRIGger:POSition?` | query only |
| `:TRIGger:STATus?` | query only |

`MODE` lists `SETup (11)` but no `:TRIGger:SETup` family appears in the command
file — a gap in the reference.

## Channel and signal modes (11 families, 63 commands)

| Mode | Commands |
|---|---|
| EDGE (3) | `SOURce` `SLOPe` `LEVel` |
| PULSe (6) | `WIDTh` `UWIDth` `LWIDth` `LEVel` `SOURce` `WHEN` |
| SLOPe (8) | `TIME` `TUPPer` `TLOWer` `ALEVel` `BLEVel` `WHEN` `SOURce` `WINDow` |
| RUNT (7) | `WUPPer` `WLOWer` `ALEVel` `BLEVel` `SOURce` `POLarity` `WHEN` |
| WINDows (6) | `TIME` `ALEVel` `BLEVel` `SOURce` `SLOPe` `POSition` |
| DELay (9) | `TUPPer` `TLOWer` `ALEVel` `BLEVel` `SA` `SB` `SLOPa` `SLOPb` `TYPE` |
| NEDGe (5) | `IDLE` `LEVel` `SOURce` `SLOPe` `EDGE` |
| TIMeout (4) | `TIME` `SLOPe` `LEVel` `SOURce` |
| DURation (6) | `TUPPer` `TLOWer` `LEVel` `SOURce` `TYPE` `WHEN` |
| PATTern (3) | `LEVel` `SOURce` `PATTern` |
| VIDeo (6) | `LEVel` `SOURce` `POLarity` `MODE` `LINE` `STANdard` |

`SOURce` takes `CHANnel1..4` and `D0..D15` throughout. `SLOPe` is usually
`POSitive (0), NEGative (1), RFALl`.

## Bus decode modes (8 families, 69 commands)

`SPI` 12 · `IIC` 9 · `RS232` 9 · `SHOLd` 9 · `IIS` 8 · `LIN` 7 · `CAN` 6 ·
`M1553` 5 · `FLEXray` 4

Examples: `:TRIGger:IIC:WHEN` = `STARt, STOP, RESTart, NACKnowledge, ADDRess,
DATA, ...`; `:TRIGger:SPI:MODE` = `HIGH (0), LOW (1)`.

## Action commands

`:TFORce` (force a trigger), `:AUToscale`. `:RUN` / `:STOP` / `:SINGle` are not
listed in the reference files (see `AGENTS.md` for those).

---

# Differences that matter

| Topic | Rigol MSO5000 | Keysight DSO-X 3024A |
|---|---|---|
| Pulse width | `PULSe` family | **`GLITch`**; `PULSe` does not exist |
| Video | `VIDeo` | `TV` |
| Serial decode | `IIC`, `RS232` | would be `I2C`, `UART` — **absent entirely** |
| "Did it trigger?" | `:TRIGger:STATus?` | `:AER?` / `:TER?` |
| Single sweep | `:TRIGger:SWEep SINGle` | **rejected**; arm with `:SINGle` |
| Force a trigger | `:TFORce` | `:TRIGger:FORCe` |
| Horizontal offset | — | `:TIMebase:POSition`, **not** `:TIMebase:OFFSet` |
| Bus-decode triggers | 8 families | none |

That last `:TIMebase` row is not a trigger command but belongs here: sending
`:TIMebase:OFFSet` is answered with `-113` and leaves the query unterminated,
which makes the *next* read hang until timeout.

---

# Wiring it into the JSON config

`scope_server.py`'s `Trigger` class carries the mode and parameter tables above,
so the config never sends a command this instrument cannot honour:

```json
"trigger": { "mode": "EDGE", "source": 2, "slope": "POS", "level": 2.4 }
```

- `mode` is **required** whenever parameters are given, because the SCPI prefix
  depends on it, and it is applied first whatever order the JSON uses.
- `source` is written as a channel number `1`–`4` and becomes `CHANnel2`.
- An unsupported mode, a parameter that mode does not have, or an out-of-range
  source is **reported and not sent** — the instrument would otherwise keep the
  old value and the write would look like it worked.
- Values are verified by readback, comparing SCPI mnemonics long-or-short, so
  `"slope": "RISING"` is caught even though the instrument accepts enums
  silently.
- Modes whose parameters were not identified (`USB`, `DEL`) can be selected;
  any parameter for them is reported.
