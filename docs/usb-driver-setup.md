# Scope USB Driver Setup (Windows)

Applies to every scope used with this repo.

| Instrument | VID hex / dec | PID hex / dec | Verified resource string |
|---|---|---|---|
| Rigol MSO5104 | `0x1AB1` / `6833` | `0x0515` / `1301` | `USB0::6833::1301::MS5A00000000::0::INSTR` |
| Keysight DSO-X 3024A | `0x0957` / `2391` | `0x17A6` / `6054` | `USB0::2391::6054::MY00000000::0::INSTR` |

> **pyvisa-py reports VID/PID in decimal.** Discovery prints
> `USB0::2391::6054::MY00000000::0::INSTR`, not `0x0957::0x17A6`. The hex form
> also opens successfully, but use whichever form `list_resources()` reports so
> the config matches reality.

> **Plug into the correct port.** On the Keysight DSO-X 3024A the **rear** USB
> *device* port (USB-B) is USBTMC. The **front** panel port is a USB *host* port
> for flash drives and will never enumerate as an instrument.

## Problem

On Windows, pyvisa-py (via pyusb/libusb) cannot access oscilloscopes because
Windows has bound a driver to the device that blocks libusb. There are two
distinct failure modes:

1. **Wrong driver bound** — Windows bound its own USBTMC driver, which works for
   NI-VISA/Keysight VISA but not for libusb.
2. **No driver bound at all** — the device enumerates and reports its name, but
   has no `Service` assigned, so libusb still cannot claim it.

Both produce the same symptoms:

- `pyvisa.ResourceManager().list_resources()` returning an empty list `()`
- `usb.core.NoBackendError: No backend available` from pyusb
- pyvisa-shell showing `???` for serial number and failing with "No device found"

## Diagnose which failure mode you have

```powershell
# Devices Windows has flagged as problematic (Problem Code 28 = FAILED_INSTALL)
pnputil /enum-devices /problem
```

Then inspect the registry node for your scope — substitute the VID/PID:

```powershell
$node = "HKLM:\SYSTEM\CurrentControlSet\Enum\USB\VID_0957&PID_17A6"
Get-ChildItem $node | ForEach-Object {
    Get-ItemProperty $_.PSPath | Select-Object DeviceDesc, Service, Mfg
}
```

- `Service : WinUSB` → already correct, nothing to do
- `Service : usbtmc` (or blank/empty) → run the fix below

A useful cross-check is whether libusb itself loads, independent of the device:

```powershell
python -c "import usb.backend.libusb1 as l1; print(l1.get_backend())"
```

A non-`None` backend means the Python side is healthy and the driver is the
only blocker — no `pip install` will help.

## Solution: Replace driver with WinUSB using Zadig

### Step 1: Download Zadig

Get the latest version from: <https://zadig.akeo.ie/>

### Step 2: Run Zadig

1. **Connect the scope** via its rear USB device port and power it on
2. Run Zadig **as Administrator** (right-click → *Run as administrator*)
3. In Zadig, click **Options → List All Devices**
4. Find your scope in the dropdown by its reported name (`MSO5104`, `DSO-X 3024A`)
   and confirm the IDs match the table at the top of this document
5. Select **WinUSB** as the target driver (the green arrow)
6. Click **Replace Driver**

> **Only replace the driver for the scope.** Binding WinUSB to the wrong entry
> (keyboard, mouse, Bluetooth adapter) can disable that device. Check the VID/PID
> before clicking.

### Step 3: Verify

```powershell
python -c "import pyvisa; rm = pyvisa.ResourceManager('@py'); print(rm.list_resources())"
```

You should see something like:

```
('USB0::6833::1301::MS5A00000000::0::INSTR',)
('USB0::2391::6054::MY00000000::0::INSTR',)
```

Then confirm the scope actually talks:

```python
import pyvisa
rm = pyvisa.ResourceManager("@py")
inst = rm.open_resource(rm.list_resources()[0])
inst.timeout = 5000
inst.read_termination = "\n"
inst.write_termination = "\n"
print(inst.query("*IDN?"))
inst.close()
```

A healthy Keysight DSO-X 3024A replies:

```
AGILENT TECHNOLOGIES,DSO-X 3024A,MY00000000,02.38.2014110300
```

## Pointing `scope_config.json` at the right scope

`scope_server.py` reads the `resource` field from `scope_config.json`. Use
`set_resource.py` to update it rather than editing by hand:

```powershell
# Interactive: lists every detected instrument and lets you pick
python set_resource.py

# Update a different config file
python set_resource.py -c keysight.json

# Just show the list (with model identification) and exit
python set_resource.py --list --identify

# Skip the prompt by targeting a device directly -- hex or decimal both work
python set_resource.py --vid 0x0957
python set_resource.py --match MY00000000
python set_resource.py --index 2

# Preview the change without writing
python set_resource.py --vid 0x0957 --dry-run
```

With no flags it shows a numbered list and prompts: press **Enter** to keep the
currently configured instrument (marked `<- current`), a number to switch, or
`q` to quit. In a non-interactive context (piped input, CI) it will not prompt —
it auto-selects only when exactly one instrument is present and otherwise exits
non-zero telling you which flags to use.

The write is atomic, so a running `scope_server.py` picks up the change on its
next poll and reconnects automatically. Other keys in the config (the
`settings` block) are preserved, and a missing parent directory is created.

## Alternative: NI-VISA

Instead of Zadig/WinUSB, you can install **NI-VISA** from NI.com. This provides a proper USBTMC driver that pyvisa can use via the `ni` backend:

```powershell
python -c "import pyvisa; rm = pyvisa.ResourceManager(); print(rm.list_resources())"
```

## Undoing a WinUSB binding

Device Manager → right-click the scope → **Uninstall device** → then
**Action → Scan for hardware changes**. The device returns to the unbound state,
and you can re-bind a different driver.

## Notes

- The WinUSB approach uses the `@py` (pure Python) backend — no NI/Keysight software needed
- Binding WinUSB means Keysight IO Libraries / BenchVue will **not** see the scope;
  this repo deliberately standardises on `@py`, so that tradeoff is intended
- After changing the driver, the `???` serial number warning in pyvisa-shell will be resolved
- If you have multiple scopes attached, the serial number uniquely identifies each one in the VISA resource string
