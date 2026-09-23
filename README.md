
#

## how to use

```python
python .\scope_server.py .\scope_config.json
```

then you can control the osci device by editing the json file.

The `settings` block is grouped into objects -- one per channel, plus
`timebase` and `run`:

```json
{
  "resource": "USB0::2391::6054::MY00000000::0::INSTR",
  "settings": {
    "channels": {
      "1": {"scale": 20, "offset": 0, "probe": 10},
      "2": {"scale": 2, "probe": 10, "display": true}
    },
    "timebase": {"scale": 0.001},
    "trigger": {"mode": "EDGE", "source": 2, "slope": "POS", "level": 2.4},
    "run": 1
  }
}
```

- **channel items**: `scale` (V/div), `offset` (V), `probe` (attenuation,
  `10` means a 10:1 probe), `display` (`true`/`1` shows the channel,
  `false`/`0` hides it)
- **timebase items**: `scale` (s/div), `offset` (s)
- **trigger**: `mode` plus that mode's parameters, see below
- **run**: `1` to start acquisition, `0` to stop

Only the values that changed are sent. Unknown sections and items are reported
in the console rather than silently ignored.

## trigger

`mode` is **required** whenever trigger parameters are given, because the SCPI
prefix depends on it (`:TRIGger:EDGE:LEVel` versus `:TRIGger:GLIT:LEVel`), and
it is applied first whatever order the JSON uses. `source` is a channel number
`1`-`4` and is written as `CHANnel2`.

The modes and parameters below were **measured on the connected DSO-X 3024A** --
this instrument has no bus-decode triggers, and its pulse-width mode is called
`GLITch`, not `PULSe`. Anything unavailable is reported and not sent, because
the instrument answers an unknown mode with `-224` and quietly keeps the old
one, so a blind write would look like it worked. Full details, including the
Rigol command set for comparison, are in `commands/trigger_reference.md`.

| mode | parameters |
|---|---|
| `EDGE` | `source` `slope` `level` `coupling` |
| `GLITch` | `source` `polarity` `qualifier` `level` |
| `PATTern` | `qualifier` |
| `RUNT` | `source` `polarity` `qualifier` `time` |
| `TRANsition` | `source` `slope` `qualifier` `time` |
| `TV` | `source` `polarity` `mode` `standard` `line` |
| `USB`, `DELay` | mode only; parameter names not identified |
| `SHOLd` | `slope` |

Values are verified by readback, comparing SCPI mnemonics long or short, so a
typo like `"slope": "RISING"` is reported rather than silently ignored.

## requests

There are **two files**, and the split is the point:

| file | role | who writes it |
|---|---|---|
| `scope_config.json` | state: `resource` + `settings` | **you only** -- the server never writes it |
| `scope_actions.json` | a panel of one-shot requests | you change a value, the server puts it back to idle |

`settings` is state: it is diffed against what was last applied, so an unchanged
value sends nothing. Requests are one-shot side effects, and they run after the
settings, so what you capture is the state you just asked for.

`scope_actions.json` is a **control panel**. Every request the server knows sits
in it at the idle value `0`:

```json
{"single": 0, "save_png": 0, "capture_png": 0}
```

Replace a `0` with something real to ask for it, and the server puts the `0`
back once it has acted -- so the panel stays put, tells you what is available,
and asking a second time is a one-character edit:

```json
{"single": 1, "save_png": 0, "capture_png": 0}
```

| request | what it does |
|---|---|
| `single` | arms one acquisition and returns -- the front-panel **Single** key |
| `save_png` | saves the screen as it is right now |
| `capture_png` | arms one acquisition, waits for the trigger, then saves the screen |

Anything that spells "nothing" counts as idle: `0`, `false`, `null`, `""`. Any
other value asks for the request, so `1`, `true` and `"go"` all work for
`single`. If you delete a name it comes back on the next server start, so the
panel is always complete.

`save_png` and `capture_png` read the screen over VISA (`:DISPlay:DATA? PNG`) and
write it straight to your PC, so no USB stick is needed. Relative paths resolve
against the config file's folder, and missing folders are created. `single`
writes nothing, so it takes no path. A path replaces the idle value:

```json
{"save_png": "captures/shot_{timestamp}.png"}
```

`capture_png` uses the object form because it has options:

```json
{"capture_png": {"path": "captures/hit_{timestamp}.png",
                 "timeout_s": 5.0, "resume": false}}
```

There is no counter field to bump: the server resets the whole entry, so the
object carries only real options.

- **`timeout_s`** (default `5`) -- how long to wait for the trigger. An armed
  scope with no trigger waits *forever*, so this matters: when it expires the
  request is reported as failed and **nothing is written**, rather than saving a
  stale screen and pretending it was the event.
- **`resume`** (default `false`) -- leave the scope stopped on the captured
  frame, exactly like the front-panel Single key. Set `true` to go back to
  continuous acquisition after saving.

It arms with `:SINGle` after a `*CLS`, then polls `:TER?` for the trigger.
`commands/trigger_reference.md` explains why that combination: on this
instrument `:TRIGger:SWEep SINGle` is rejected, `*OPC?` returns immediately
without waiting, and reading `:TER?` clears it.

### Why a separate file

The server writes the request file, and a file that is both edited by you and
written by the server is a hazard: your editor holds a stale copy, and saving it
overwrites whatever the server last wrote. Keeping requests in their own file
means the blast radius is a request -- never one of your settings. The config
file is never opened for writing at all.

That blast radius is worth spelling out, because the panel has one sharp edge:
if your editor still shows `{"single": 1}` after the server has reset it to `0`,
saving that buffer asks for `single` a second time and re-arms the scope. It
cannot corrupt a setting, but it can do something you did not ask for. Let the
editor reload the file after the server has handled it (most do, once the buffer
has no unsaved edits), or keep the file closed while you are not editing it.

Three consequences worth knowing:

- **A request left non-idle when the server starts is reset, not run**, so a
  request from an earlier session cannot act now. The console names them.
- **Every request is attempted once and then reset to idle**, successful or not.
  A failure is reported and does not linger, so it cannot fire later when an
  unrelated edit happens to change the file.
- **Only the value that was acted on is reset.** If you set `single` to `2` while
  `1` is still being carried out, the `2` stays and fires on the next pass.

Pass `--requests PATH` to keep the request file somewhere else.

