# Changelog

## unrelease

## 1.0

- use `.\set_resource.py` Updated id of osci device
- **Breaking**: one-shot requests moved out of the config into a separate
  `scope_actions.json` (`--requests PATH` overrides the location), and the config
  is now never written by the server, so a stale editor buffer over it can no
  longer lose a setting. The request file is a **panel**: every request the
  server knows sits in it at the idle value `0`, replacing that value fires the
  request, and the server puts the `0` back once it has acted -- so the panel
  stays put, doubles as a menu, and repeating a request is a one-character edit.
  Only the value that was acted on is reset, a name you delete comes back on the
  next start, and a request left non-idle at startup is reset without being run
- add `commands/trigger_reference.md` and Trigger JSON support (see below)
- add a `single` request: the front-panel Single key (`*CLS` then `:SINGle`),
  arming one acquisition without waiting for anything or writing a file, so it
  needs no `path`
- `scope_server.py` now reports unknown **top-level** config keys, with a hint
  when a section was put outside `"settings"` (a top-level `trigger` block read
  like it configured the trigger but was silently ignored)
- add a `capture_png` request: arms one acquisition (`:SINGle` after `*CLS`),
  waits for the trigger by polling `:TER?`, then saves the screen. Options are
  `timeout_s` (default 5) and `resume` (default false, i.e. hold the captured
  frame like the Single key). No trigger within the timeout is reported as a
  failure and writes nothing, instead of saving a stale screen
- `commands/trigger_reference.md`: the measured Keysight trigger command set next
  to the Rigol inventory, with the differences that bite when porting, plus how
  to arm a single acquisition and how to tell it finished -- `:TER?` latches on
  the trigger and clears when read, `*OPC?` does not wait, `:TRIGger:SWEep
  SINGle` is rejected
- add a `trigger` section to the JSON config: `mode` plus that mode's
  parameters. Only modes measured to exist on the connected DSO-X 3024A are
  accepted (EDGE, GLIT, PATT, RUNT, TRAN, TV, USB, DEL, SHOL), the mode is
  applied first whatever the JSON order, and an unsupported mode or parameter is
  reported rather than sent -- the instrument keeps the old value and answers an
  unknown mode with -224, so a blind write would look like it succeeded
- verify enum settings by readback too, comparing SCPI mnemonics long or short
  (`POSitive` reads back as `POS`), so a typo'd value is caught
- **fix**: `timebase.offset` sent `:TIMebase:OFFSet`, which does not exist on
  this instrument and left the query unterminated (hanging the next read). The
  node is `POSition`; both timebase items now have readbacks
- add a `save_png` request: reads the screen over VISA (`:DISPlay:DATA? PNG`) and
  writes it straight to the PC, so the front panel USB stick is not needed. Use
  `{timestamp}` in the path to keep more than one shot
- **Breaking**: `scope_server.py` settings are now grouped into objects instead
  of flat keys -- one object per channel, plus `timebase` and `run`
  (`channel2_scale` becomes `channels.2.scale`). `scope_config.json` migrated;
  `README.md` documents the new shape
- add channel display on/off (`channels.<n>.display`, `:CHANnel<n>:DISPlay`).
  Accepts true/false or 1/0; string forms like `"OFF"` are rejected rather than
  coerced, because `1 if "OFF" else 0` is 1 -- a truthy string would switch the
  channel on when the config meant to switch it off
- add channel probe ratio control (`:CHANnel<n>:PROBe`), written before
  scale/offset and verified by readback, because the instrument silently clamps
  an out-of-range attenuation instead of rejecting it
- channel controls live in a `Channel` class (`CH1`..`CH4`) over a shared
  `ScpiGroup` base: the SCPI prefix lives only in the instance, so a control
  cannot be aimed at the wrong channel, and adding an item publishes it on all
  four channels at once

## 0.3

- support use json file as interface to control device: .\scope_server.py

## 0.2.0 (2026-06-18)

- **Breaking**: Removed `scope.py` — no more custom CLI application code
- New approach: AI-agent-driven control via `pyvisa-shell` and inline Python+PyVISA
- Rewrote `AGENTS.md`, `README.md`, and `.github/copilot-instructions.md` for the new architecture
- Added MSO5000 SCPI command reference (`commands/Rigol_MSO5000_SCPI_Commands.txt`, `Rigol_MSO5000_SCPI_Indexes.txt`)
- Updated for Rigol MSO5104 (previously targeted DS1104Z Plus)
- Documented `:SAVE:CSV D:/file.csv` workflow for USB drive waveform export
- Documented `:STOP` requirement before CSV save

## 0.1.0 (2026-05-29)

- Initial release
- USB (USBTMC) connectivity via PyVISA-py + libusb-package
- LAN (TCP/IP port 5555) connectivity via PyVISA-py
- Screenshot capture (`:DISP:DATA?`)
- Waveform data read (`:WAV:DATA?`)
- Quick measurements: Vpp, Vrms, Frequency (`:MEAS:VPP?`, `:MEAS:VRMS?`, `:MEAS:FREQ?`)
- Vertical scale control (`:CHANn:SCAL`)
- Horizontal scale control (`:TIM:SCAL`)
- Run/stop acquisition control
- CLI with `--usb` / `--ip` flags
- Importable Python module
- Tested with DS1104Z Plus, firmware 00.04.04.SP4
