# Rigol MSO5104 + PyVISA on Windows — Troubleshooting Notes

Date: 2026-08-19
Machine: Windows 10, Python 3.12.10 (`C:\Python312`)

## Symptom

`pyvisa-shell` found no instruments even though a Rigol driver had been installed:

```
(visa) list
...\pyvisa_py\tcpip.py:122: UserWarning: TCPIP::hislip resource discovery requires
the zeroconf package to be installed... try 'pip install zeroconf'
(visa) list
(visa)        <-- empty
```

## Diagnosis

`pyvisa-info` showed two problems:

1. **IVI backend not available** — `Binary library: Not found`.
   The installed "driver" did not include a VISA library (`visa64.dll`).
   Checked and confirmed missing:
   - `C:\Windows\System32\visa64.dll` / `visa32.dll`
   - `C:\Program Files\IVI Foundation` (not present)
   - No VISA/Rigol/UltraSigma entries in the registry Uninstall keys

   With no IVI library, PyVISA silently falls back to the pure-Python
   **pyvisa-py** backend (the `pyvisa_py\tcpip.py` warning path in the shell
   output was the visible clue).

2. **pyvisa-py had no USB support** — PyUSB was not installed:

   ```
   USB INSTR:
      Please install PyUSB to use this resource type.
      No module named 'usb'
   ```

   The `zeroconf` warning was unrelated noise — it only affects HiSLIP
   instrument discovery over LAN.

## Fix

Scope connected via USB; decided to stay on the pyvisa-py backend.
Installed two packages:

```
python -m pip install pyusb libusb-package
```

- `pyusb` (1.3.1) — USB transport for pyvisa-py
- `libusb-package` (1.0.30.0) — bundles `libusb-1.0.dll`, the backend PyUSB
  needs on Windows (no separate driver/Zadig step was needed; the Rigol scope
  was already openable by libusb)

## Verification

Enumerate raw USB devices (Rigol VID = `0x1AB1`):

```
python -c "import libusb_package; [print(hex(d.idVendor), hex(d.idProduct), d.manufacturer) for d in libusb_package.find(find_all=True)]"
# -> 0x1ab1 0x515 Rigol
```

List VISA resources:

```
python -c "import pyvisa; rm = pyvisa.ResourceManager('@py'); print(rm.list_resources())"
# -> ('USB0::6833::1301::MS5A00000000::0::INSTR',)
```

(6833 hex = 0x1AB1.) Query the scope:

```
python -c "import pyvisa; rm = pyvisa.ResourceManager('@py'); \
i = rm.open_resource('USB0::6833::1301::MS5A00000000::0::INSTR'); \
print(i.query('*IDN?')); i.close()"
# -> RIGOL TECHNOLOGIES,MSO5104,MS5A00000000,00.01.03.02.02
```

`pyvisa-shell` → `list` now shows the instrument; open it with:

```
(visa) open USB0::6833::1301::MS5A00000000::0::INSTR
(visa) query *IDN?
```

## Caveats

- If Rigol UltraSigma or NI-VISA is installed later, its USBTMC driver may
  claim the device and block libusb access. In that case use the default
  `ivi` backend (drop the `@py`) instead.
- Alternative route not taken: install NI-VISA runtime (or Rigol UltraSigma)
  so PyVISA's `ivi` backend works — the more standard Windows setup.
- The resource string contains the scope's serial number; if the scope is
  replaced, the resource address changes.
