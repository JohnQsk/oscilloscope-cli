# AGENTS.md

This file provides guidance to the AI agent when working with code in this repository.

## Project

SCPI command reference and AI-agent-driven control for a Rigol MSO5104 oscilloscope. No custom application code — the AI agent uses `pyvisa-shell` or inline Python+PyVISA to send SCPI commands directly.

## How to control the scope

### Via pyvisa-shell (interactive)

```powershell
pyvisa-shell.exe
# (visa) list
# (visa) open 0
# (open) write :STOP
# (open) query *IDN?
# (open) write :TIM:SCAL 10e-3
# (open) write :SAVE:CSV D:\test.csv
# (open) close
# (visa) quit
```

### Via Python (programmatic, when the agent needs to process data)

```python
import pyvisa
rm = pyvisa.ResourceManager("@py")
inst = rm.open_resource(rm.list_resources()[0])
inst.timeout = 5000
inst.read_termination = "\n"
inst.write_termination = "\n"
inst.write(":STOP")
inst.close()
```

## Key constraints

- **Always use `@py` backend**: `pyvisa.ResourceManager("@py")`. Never use the default NI-VISA backend.
- **Termination**: `\n` for both read and write.
- **Timeout**: 5000ms.
- **USB resource**: discovered via `rm.list_resources()` — do not hardcode.
- **`D:\` in SCPI commands** refers to the USB drive plugged into the scope's front panel. Use forward slashes (`D:/file.csv`) to avoid backslash escape issues.
- **`:STOP` before `:SAVE:CSV`** — the scope must be stopped to save waveform data.

## SCPI command reference

Full MSO5000 SCPI command list is in `commands/Rigol_MSO5000_SCPI_Commands.txt` and `commands/Rigol_MSO5000_SCPI_Indexes.txt`. Use these as the authoritative source for command syntax and valid parameters.

### Common commands

| Command | Description |
|---|---|
| `*IDN?` | Identity query |
| `:RUN` / `:STOP` | Start / stop acquisition |
| `:TIM:SCAL <s>` | Horizontal scale (s/div) |
| `:CHANn:SCAL <v>` | Vertical scale (V/div) |
| `:CHANn:PROBe <ratio>` | Probe attenuation, `10` = 10:1 (DSO-X 3024A accepts 0.1–10000, clamps outside that with only a `-222` in the error queue) |
| `:CHANn:DISPlay <ON\|OFF\|1\|0>` | Show/hide a channel; reads back as `1`/`0` |
| `:MEAS:VPP? CHANn` | Peak-to-peak voltage |
| `:MEAS:FREQ? CHANn` | Frequency |
| `:SAVE:CSV D:/file.csv` | Save waveform to USB drive (scope must be stopped) |
| `:WAV:SOUR CHANn` / `:WAV:FORM BYTE` / `:WAV:MODE NORM` / `:WAV:DATA?` | Waveform readout pipeline |
| `:DISP:DATA? ON,OFF,PNG` | Screenshot capture (binary) |

## Dependencies

```powershell
pip install -r requirements.txt
```

On Windows, the Rigol scope may need its driver replaced with WinUSB via Zadig — see `docs/usb-driver-setup.md`.
