# oscilloscope-cli

A **headless** SCPI service for USB oscilloscopes.

There is no GUI and no CLI client. One long-running process watches a JSON file,
and *how* you change that file is how you drive the instrument — an editor, a
shell script, a notebook, a test rig, another program. The scope never knows the
file exists.

```
            you                    scope_server.py              instrument
   ┌──────────────────┐          ┌──────────────────┐          ┌────────────┐
   │ scope_config.json│  watch   │ diff, apply,     │   SCPI   │            │
   │  (state, yours)  │ ───────► │ verify, report   │ ───────► │ USB USBTMC │
   │ scope_actions.json│ ◄────── │ reset to idle    │          │            │
   │  (requests)      │          └──────────────────┘          └────────────┘
   └──────────────────┘                    │
                                           ▼
                                    stdout is the UI
```

## Why a file instead of a command line

- **No session state to lose.** The JSON file *is* the desired state. Stop the
  server, edit, start it again — the scope is brought to whatever the file says.
- **Any language, any tool.** Writing JSON is the whole API. No Python import, no
  socket protocol, no SDK.
- **Only changes travel.** The settings block is diffed against what was last
  applied, so re-saving a file you barely touched sends one command, not twenty.
- **Mistakes are reported, not sent.** Values are checked before they go out, and
  settings that can be silently clamped are read back afterwards. A typo shows up
  in the console instead of looking like it worked.
- **The scope is never a dependency.** If the instrument is unplugged, the server
  keeps watching and reconnects on its own once the file changes again.

The console is the only interface, so it narrates everything: what it applied,
what it read back, what it refused, and what it ignored.

## Quick start

```powershell
pip install -r requirements.txt

# 1. point the config at a connected scope (interactive picker)
python set_resource.py

# 2. start the service
python scope_server.py
```

Then edit `scope_config.json` in any editor and save. The server notices within
a fraction of a second and applies the change.

```json
{
  "resource": "USB0::2391::6054::MY00000000::0::INSTR",
  "settings": {
    "channels": {
      "2": {"display": true, "scale": 1, "probe": 10}
    },
    "timebase": {"scale": 1e-7},
    "trigger": {"mode": "EDGE", "source": 2, "slope": "POS", "level": 2},
    "run": 1
  }
}
```

Stop with **Ctrl+C**.

### Command line

```powershell
python scope_server.py                            # ./scope_config.json
python scope_server.py D:\rig\keysight.json       # a different config
python scope_server.py --requests other.json      # a different request panel
```

| argument | meaning |
|---|---|
| `config` | config file to watch (default `scope_config.json` beside the script) |
| `--requests PATH` | request file (default `scope_actions.json` beside the config) |

### What you should see

```
Watching   C:\...\scope_config.json
Requests   C:\...\scope_actions.json
Connecting to USB0::2391::6054::MY00000000::0::INSTR ...
Connected: AGILENT TECHNOLOGIES,DSO-X 3024A,MY00000000,02.38.2014110300
Applying settings:
  -> :CHANnel2:PROBe 10
  -> :CHANnel2:DISPlay 1
  -> :CHANnel2:SCALe 1
  -> :TIMebase:SCALe 1e-07
  -> :TRIGger:MODE EDGE
  -> :TRIGger:EDGE:SOURce CHANnel2
  -> :TRIGger:EDGE:SLOPe POS
  -> :TRIGger:EDGE:LEVel 2
  -> :RUN
  ** capture_png -> captures/hit_20260923-174501.png (48213 bytes)
```

## The two files, and why they are separate

| file | role | who writes it |
|---|---|---|
| `scope_config.json` | **state** — `resource` plus the `settings` you want | **you only** — the server never opens it for writing |
| `scope_actions.json` | **requests** — a panel of one-shot actions | you change a value; the server puts the `0` back |

`settings` is level-based: it describes what the instrument should look like, and
the server diffs it, so an unchanged value sends nothing.

Requests are edge-based: they are *events*, not state. Keeping them in a separate
file means a stale editor buffer can only ever lose a request — never one of your
settings. The config file is not opened for writing at all.

## `scope_config.json` — state

### `resource` (required)

The VISA resource string of the instrument. **Required** — a config without it
stops the server. Do not hand-write it: run `python set_resource.py`, which lists
what is actually connected and writes the choice into the file. Serial numbers
appear here, so this file shows your hardware — keep that in mind before
publishing a fork.

### `settings` (optional)

Everything the server knows how to control lives under here. Four sections:

#### `channels` — one object per channel, keyed `"1"`–`"4"`

| item | unit | meaning |
|---|---|---|
| `scale` | V/div | vertical scale |
| `offset` | V | vertical offset |
| `probe` | ratio | probe attenuation; `10` means a 10:1 probe |
| `display` | bool | `true`/`1` shows the channel, `false`/`0` hides it |

`probe` is always written **before** the other items on its channel, because
changing the attenuation makes the instrument rescale the vertical scale — a
probe write landing after a scale write would silently undo it.

`display` accepts booleans and numbers only. The strings `"ON"`/`"OFF"` are
**rejected** rather than guessed at: `1 if "OFF" else 0` is `1`, so accepting
`"OFF"` would switch the channel *on* while the file said off. You get a warning
instead.

#### `timebase`

| item | unit | meaning |
|---|---|---|
| `scale` | s/div | horizontal scale |
| `offset` | s | horizontal position |

#### `trigger`

`mode` is required as soon as any parameter is given: the SCPI prefix depends on
it (`:TRIGger:EDGE:LEVel` versus `:TRIGger:GLIT:LEVel`), and the mode is applied
first whatever order the JSON uses. `source` is a channel number `1`–`4` and is
written as `CHANnel2`.

```json
{
  "settings": {
    "trigger": {"mode": "EDGE", "source": 2, "slope": "POS", "level": 2.4}
  }
}
```

| mode | parameters |
|---|---|
| `EDGE` | `source` `slope` `level` `coupling` |
| `GLIT` | `source` `polarity` `qualifier` `level` |
| `PATT` | `qualifier` |
| `RUNT` | `source` `polarity` `qualifier` `time` |
| `TRAN` | `source` `slope` `qualifier` `time` |
| `TV` | `source` `polarity` `mode` `standard` `line` |
| `SHOL` | `slope` |
| `USB`, `DEL` | mode only; parameter names not identified |

Only modes measured to exist on a real instrument are accepted. An unknown mode
or parameter is **reported, not sent** — the scope answers an unknown mode with
`-224` and quietly keeps the previous one, so a blind write would look like it
succeeded. See [`commands/trigger_reference.md`](commands/trigger_reference.md)
for what was measured, including the Rigol command set for comparison.

#### `run`

`1` starts acquisition (`:RUN`), `0` stops it (`:STOP`).

### Getting told about mistakes

The server reports what it does not understand, with a pointer to where you are
looking in the file:

```
  ! ignoring unknown top-level key 'trigger' (did you mean settings.trigger?)
  ! ignoring unknown item 'channels.2.scal' (valid: scale, offset, probe, display)
  ! bad value for 'channels.2.display': expected true/false or 1/0, got 'OFF'
  ! trigger.mode: mode 'EGDE' is not available on this instrument (valid: DEL, EDGE, GLIT, PATT, RUNT, SHOL, TRAN, TV, USB)
  ! channels.2.probe=0.01 not applied as asked (scope reports 1.000000E+01, wrote 0.01)
```

That last one is the readback check. Rules that follow from it:

- **Only what changed is sent.** Values are diffed against the last apply, and
  the whole settings block is re-applied when the server connects to a
  *different* instrument.
- **A rejected value is never retried in a loop.** A clamped write is reported
  but still recorded as applied, so it is said once rather than on every poll.
- **A JSON syntax error keeps the old state** and changes nothing on the scope.
- **A bad file edit is not fatal.** Fix the JSON, save again, and the next poll
  picks it up. The server never exits because of a malformed config.

## `scope_actions.json` — requests

Requests are events, so they live in their own file and are shaped as a **control
panel**: every request the server knows sits in it at the idle value `0`. Replace
a `0` with something real to ask for it, and the server edits the `0` back once
it has acted.

```json
{
  "single": 0,
  "save_png": 0,
  "capture_png": 0
}
```

| request | what it does | needs a path |
|---|---|---|
| `single` | arms one acquisition and returns — the front-panel **Single** key | no |
| `save_png` | saves the screen as it is right now | yes |
| `capture_png` | arms one acquisition, waits for the trigger, then saves the screen | yes |

Replace exactly one value at a time — the panel is already a complete, valid
file, so a request is a one-character edit:

- `"single": 1` — arm one acquisition
- `"save_png": "captures/shot_{timestamp}.png"` — grab the screen now
- `"capture_png": {"path": "captures/hit_{timestamp}.png", "timeout_s": 5.0, "resume": false}`

Anything that spells "nothing" is idle: `0`, `false`, `null`, `""`. Any other
value asks for the request, so `1`, `true` and `"go"` all fire `single`.

`save_png` and `capture_png` read the screen over VISA (`:DISPlay:DATA? PNG`) and
write it straight to disk, so no USB stick is involved. Relative paths resolve
against the request file's folder, missing folders are created, and
`{timestamp}` expands to `YYYYmmdd-HHMMSS`. There is no counter field to bump:
the whole entry is reset, so the object form carries only real options.

| option | default | meaning |
|---|---|---|
| `timeout_s` | `5` | how long `capture_png` waits for a trigger |
| `resume` | `false` | `true` returns to continuous acquisition after saving; the default holds the captured frame, exactly like the Single key |

An armed scope with no trigger waits **forever**, so the timeout matters: when it
expires the request is reported as failed and **nothing is written**, rather than
saving a stale screen and pretending it was the event.

### The sharp edge, and what protects you

The panel is a file you edit while the server writes it, so the rules are
deliberately conservative:

- Every request is attempted **once** and then reset to idle, successful or not.
  A failure is reported and does not linger, so it cannot fire later when some
  unrelated edit happens to change the file.
- Only the value that was actually acted on is reset. Set `single` to `2` while
  `1` is still being carried out and the `2` survives to the next pass.
- The file is re-read before the reset, so a request you added while the last one
  ran is not written away.
- **A request left non-idle when the server starts is reset, not run** — nobody
  is asking for it now — and the console names it.
- Requests are dropped, with a message, when no instrument is connected.
- Writes are atomic (temp file + `os.replace`), so the watcher never reads a
  half-written file.

One consequence is worth spelling out: if your editor still shows
`{"single": 1}` after the server has reset it to `0`, saving that buffer asks for
`single` a second time and re-arms the scope. It cannot corrupt a setting, but it
can do something you did not ask for. Most editors reload the file once the
buffer has no unsaved edits; otherwise keep it closed while you are not editing.

## Pointing at the right instrument

`set_resource.py` is the only part of this repo that talks to hardware without
the server running. It discovers instruments through the `@py` backend and writes
the one you pick into the config.

```powershell
python set_resource.py                            # interactive picker
python set_resource.py --list --identify          # show what is connected, exit
python set_resource.py -c keysight.json           # a different config file
python set_resource.py --match MY00000000         # skip the prompt
python set_resource.py --vid 0x0957 --dry-run     # preview, write nothing
```

With no flags it shows a numbered list and prompts: **Enter** keeps the currently
configured instrument (marked `<- current`), a number switches, `q` quits. In a
non-interactive context it will not prompt — it auto-selects only when exactly one
instrument is present, and otherwise exits non-zero telling you which flags to use.

The write is atomic, so a running `scope_server.py` picks up the change on its
next poll and reconnects by itself. Other keys in the config — your whole
`settings` block — are preserved.

Because the resource string carries the serial number, `scope_config.json` is
necessarily hardware-specific. A cleaned-up example is committed here.

## Repository layout

| path | what it is |
|---|---|
| `scope_server.py` | the service: watches, diffs, applies, verifies, reports |
| `set_resource.py` | pick a connected scope and store it in a config file |
| `scope_config.json` | example state file |
| `scope_actions.json` | the request panel |
| `analyser/` | FFT / CSV analysis for waveform exports — see [`analyser/README.md`](analyser/README.md) |
| `commands/` | MSO5000 SCPI reference and measured trigger notes |
| `docs/usb-driver-setup.md` | Windows driver setup (Zadig / WinUSB, NI-VISA) |
| `rigol-mso5104-pyvisa-setup.md` | troubleshooting notes for a Rigol MSO5104 |
| `AGENTS.md` | conventions for AI agents working in this repo |
| `.githooks/prepare-commit-msg` | adds the AI co-author trailer — run `git config core.hooksPath .githooks` once |
| `LICENSE` | MIT — covers the code, not the vendor references in `commands/` |
| `requirements.txt` | `pyvisa` + `pyvisa-py` + `pyusb` + `libusb-package` |

## Connection conventions

- **`@py` backend only** — `pyvisa.ResourceManager("@py")`; no NI-VISA install
  needed.
- **USB (USBTMC)** is the tested path. Resource strings are discovered, never
  hardcoded.
- **Timeout** 5000 ms, **termination** `\n` in both directions.
- On Windows the scope may need its driver replaced with WinUSB via Zadig —
  see [`docs/usb-driver-setup.md`](docs/usb-driver-setup.md).
- `D:/` in a SCPI path refers to the USB stick in the scope's **front panel**
  (a USB *host* port). Use forward slashes. The scope must be stopped before
  `:SAVE:CSV`.

## Extending it

Adding a control item means adding one method and naming it in the group's
`ITEMS` — it then appears as a JSON key on every channel (or on the timebase) at
once. The SCPI prefix lives only in the group instance, so no command string is
hand-written and a control cannot be aimed at the wrong channel by a copy-paste
slip. Add a `<item>_readback()` next to it when the instrument can silently
clamp or reject a value. Adding a request means adding a handler to `REQUESTS`.

## Compatibility

Developed and verified against a **Keysight DSO-X 3024A** and a **Rigol
MSO5104**; the SCPI reference in `commands/` covers the Rigol MSO5000 series.
Other MSO5000/DS1000Z-family instruments should work, but the trigger modes in
particular were measured on one instrument — see
[`commands/trigger_reference.md`](commands/trigger_reference.md).

## Commit conventions

Commits carrying AI assistance record it as a trailer:

```
Co-authored-by: DeepSeek <noreply@deepseek.com>
```

`.githooks/prepare-commit-msg` writes that line automatically and leaves a
message that already has it alone. Hooks are not cloned with a repository, so
enable it once per clone:

```powershell
git config core.hooksPath .githooks
```

It is a hook rather than a `commit.template` because a template is ignored as
soon as `git commit -m` is passed, and `-m` is how most commits are made.

## Credits

**DeepSeek** wrote most of this repository, working as an AI coding agent under
human direction — the requirements, the review and the final say are JohnQsk's.

## License

[MIT](LICENSE) © 2026 JohnQsk — use it, modify it, ship it; keep the copyright
notice.

That grant covers the code in this repository. It does **not** cover the Rigol
command references under `commands/`, which are vendor material:

### Bundled vendor material

`commands/Rigol_MSO5000_SCPI_Commands.txt` and
`commands/Rigol_MSO5000_SCPI_Indexes.txt` are references extracted from Rigol's
published MSO5000 programming documentation, included here for convenience. They
remain the property of their respective owner and are not covered by the MIT
license granted above.
