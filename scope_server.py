"""Watch a JSON config file and push changed settings to a VISA instrument.

Two files are involved, and the split is the point:

* the **config file** holds state only -- `resource` and `settings`. The server
  never writes it, so it is safe to keep open in an editor.
* the **request file** (`scope_actions.json` beside it, or `--requests PATH`)
  holds one-shot requests as a control panel: every request name sits there at
  its idle value, and the server puts a name back to idle as it handles it. It
  is the only file that gets written, so a stale editor buffer over it can only
  lose a request that has already been carried out.

Settings are diffed against what was last applied, so only changes are sent:

    config:  {
      "resource": "USB0::2391::6054::MY00000000::0::INSTR",
      "settings": {
        "channels": {
          "1": {"scale": 5, "offset": 0, "probe": 10},
          "2": {"scale": 2, "probe": 10}
        },
        "timebase": {"scale": 1e-3},
        "trigger": {"mode": "EDGE", "source": 2, "slope": "POS", "level": 2.4},
        "run": 1
      }
    }

The request file starts out as a panel of every request the server knows, each
at its idle value `0`. Replace a value with something real to ask for it, and
the server puts the idle value back once it has acted -- so the panel stays put
and asking twice is a one-character edit.

    requests: {"single": 0, "save_png": 0, "capture_png": 0}

              {"single": 1}                                    <- arm once
              {"save_png": "captures/shot_{timestamp}.png"}    <- grab screen
              {"capture_png": {"path": "captures/hit.png", "timeout_s": 5}}

`save_png` grabs the screen as it is, `capture_png` arms a single acquisition
and waits for the trigger before grabbing it, and `single` just arms one -- the
front-panel Single key. Anything left non-idle when the server starts is a
leftover from an earlier session, so it is put back to idle rather than run: a
request nobody is making now should not act now.

Which settings exist is defined by the ScpiGroup subclasses below (Channel,
Timebase) plus the Trigger class, which carries the per-mode parameter tables;
add a method there to add a control item.

Stop with Ctrl+C.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from functools import partial

import pyvisa

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "scope_config.json")
POLL_INTERVAL = 0.3  # seconds

# Screenshot readback. Verified on a DSO-X 3024A: both the bare form and the
# single-argument form return PNG, while ":DISPlay:DATA? PNG,ON,COLOR" is
# rejected with -224 and leaves the query unterminated, so the transfer hangs
# until timeout. Keep this exact form.
SCREENSHOT_QUERY = ":DISPlay:DATA? PNG"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Pause before each action, giving the instrument time to redraw after any
# settings applied in the same pass.
ACTION_SETTLE_SECONDS = 0.4

# Trigger event register: latches when a trigger occurs, and *clears when read*.
# Waiting for a trigger therefore means polling it until it reads non-zero --
# see arm_and_wait(). Verified on a DSO-X 3024A, where :TRIGger:STATus? does not
# exist and :OPC? does not wait for the acquisition.
# See commands/trigger_reference.md.
TRIGGER_EVENT_QUERY = ":TER?"
TRIGGER_POLL_INTERVAL = 0.02

# Default time a capture action waits for a trigger before giving up.
CAPTURE_TIMEOUT_S = 5.0

#: Name of the request file, looked for beside the config file. It is a control
#: panel of one-shot requests: the server lays it out at startup and puts each
#: request back to its idle value as it handles it. The config file is never
#: written.
DEFAULT_REQUESTS_FILENAME = "scope_actions.json"

#: Top-level keys the config may contain. Anything else is reported rather than
#: ignored in silence.
CONFIG_KEYS = ("resource", "settings")

#: Sections recognised inside "settings", used to hint when one is put at the
#: top level by mistake.
SETTINGS_SECTIONS = ("channels", "timebase", "trigger", "run")


def report_unknown_config_keys(config):
    """Report top-level keys the server never reads.

    A section placed at the top level instead of under "settings" would
    otherwise do nothing at all while looking perfectly reasonable: a top-level
    "trigger" block reads like it configures the trigger, but the server only
    ever looks inside "settings".
    """
    for key in config:
        if key in CONFIG_KEYS:
            continue
        if key == "actions":
            print("  ! 'actions' has moved out of the config into "
                  f"{DEFAULT_REQUESTS_FILENAME} (ignored here)", flush=True)
            continue
        hint = (f" (did you mean settings.{key}?)"
                if key in SETTINGS_SECTIONS else "")
        print(f"  ! ignoring unknown top-level key '{key}'{hint}", flush=True)

def to_on_off(value):
    """Normalise a boolean-ish JSON setting to 1 or 0.

    Accepts true/false and numbers (0 is off, anything else is on). Strings are
    rejected instead of guessed at: ``1 if "OFF" else 0`` is 1, so accepting
    "OFF" would silently do the opposite of what it says. Raising ValueError
    makes apply_settings report the bad value rather than send it.
    """
    if isinstance(value, (bool, int, float)):
        return 1 if value else 0
    raise ValueError(f"expected true/false or 1/0, got {value!r}")


class ScpiGroup:
    """A group of related settings, exposed as one object in the JSON.

    Subclasses declare control items as methods and list the method names in
    ``ITEMS``. The SCPI prefix lives only in the instance, so no command string
    is written out by hand and a control cannot be aimed at the wrong channel
    by a copy-paste slip.

    Two optional extras are declared next to the control they describe:

    * ``<item>_readback()`` -- a query used to check the value really took.
      Needed when the instrument may silently clamp instead of rejecting.
    * listing the item in ``FIRST`` -- for items that must be written first.
    """

    #: Control methods published as JSON keys; add the method name here.
    ITEMS = ()

    #: Items written before all others, for controls that interact.
    FIRST = ()

    def __init__(self, prefix):
        self._prefix = prefix

    def _write(self, node, value):
        """Build a write; the prefix comes from the instance."""
        return f"{self._prefix}:{node} {value}"

    def _query(self, node):
        """Build a readback query for this group."""
        return f"{self._prefix}:{node}?"

    def plan(self, item, value):
        """Everything needed to apply one control item.

        Returns (command, readback_query, apply_first), or None when `item` is
        not a control of this group. readback_query is None for items the
        instrument always stores as given.
        """
        if item not in self.ITEMS:
            return None
        command = getattr(self, item)(value)
        readback = None
        if hasattr(self, item + "_readback"):
            readback = getattr(self, item + "_readback")()
        return command, readback, item in self.FIRST


class Channel(ScpiGroup):
    """One vertical channel: the JSON object "channels": {"<n>": {...}}.

    Add a control item by adding a method and listing its name in ``ITEMS``;
    it then appears as a key inside every channel object at once.
    """

    ITEMS = ("scale", "offset", "probe", "display")

    #: Probe attenuation is written first: changing it makes the instrument
    #: rescale the vertical scale, so a probe write landing after a scale write
    #: would silently undo the scale.
    FIRST = ("probe",)

    def __init__(self, number):
        super().__init__(f":CHANnel{number}")
        self.number = number

    # --- control items -----------------------------------------------------

    def scale(self, value):
        """Vertical scale, V/div."""
        return self._write("SCALe", value)

    def offset(self, value):
        """Vertical offset, V."""
        return self._write("OFFSet", value)

    def probe(self, value):
        """Probe attenuation: 10 means a 10:1 probe."""
        return self._write("PROBe", value)

    def display(self, value):
        """Whether the channel is shown: true/1 on, false/0 off."""
        return self._write("DISPlay", to_on_off(value))

    def probe_readback(self):
        """Query used to confirm the probe ratio actually took effect.

        The instrument clamps out-of-range attenuation instead of rejecting it
        -- 0.01 becomes 0.1 on a DSO-X 3024A -- and records only a -222 in its
        error queue, which pyvisa never raises. Without this readback the JSON
        would look applied while the scope used a different value.
        """
        return self._query("PROBe")

    def display_readback(self):
        """Readback for display(), which the instrument reports as 1 or 0."""
        return self._query("DISPlay")


class Timebase(ScpiGroup):
    """Horizontal timebase: the JSON object "timebase": {...}."""

    ITEMS = ("scale", "offset")

    def __init__(self):
        super().__init__("TIMebase")

    def scale(self, value):
        """Horizontal scale, s/div."""
        return self._write("SCALe", value)

    def offset(self, value):
        """Horizontal offset, s.

        The instrument spells this node POSition, not OFFSet. Sending
        ':TIMebase:OFFSet' is answered with -113 and leaves the query
        unterminated, which hangs the following read until it times out -- so
        the readbacks below are what catch that class of mistake.
        """
        return self._write("POSition", value)

    def scale_readback(self):
        """Readback for scale()."""
        return self._query("SCALe")

    def offset_readback(self):
        """Readback for offset(), which the instrument calls POSition."""
        return self._query("POSition")


class Trigger:
    """Trigger settings: the JSON object "trigger": {...}.

    A mode plus that mode's parameters, for example:

        "trigger": {"mode": "EDGE", "source": 2, "slope": "POS", "level": 2.4}

    `mode` is required whenever parameters are given, because the SCPI prefix
    depends on it (:TRIGger:EDGE:LEVel versus :TRIGger:GLIT:LEVel), and a
    parameter sent under the wrong mode is a silent mistake.

    Only modes measured to exist on the connected DSO-X 3024A are listed, each
    with the parameters measured to exist for it. An unsupported mode or an
    unknown parameter is reported rather than sent, because the instrument
    answers an unknown mode with -224 and quietly keeps the previous one, so
    the write would look like it succeeded. See commands/trigger_reference.md.

    These are deliberately not ScpiGroup subclasses: the prefix varies with the
    mode, and the valid item set varies with the mode too.
    """

    #: mode -> (long spelling, {JSON item: SCPI node}). Measured on a
    #: DSO-X 3024A; the key is the short spelling the instrument reads back.
    MODES = {
        "EDGE": ("EDGE", {"source": "SOURce", "slope": "SLOPe",
                          "level": "LEVel", "coupling": "COUPling"}),
        "GLIT": ("GLITch", {"source": "SOURce", "polarity": "POLarity",
                            "qualifier": "QUALifier", "level": "LEVel"}),
        "PATT": ("PATTern", {"qualifier": "QUALifier"}),
        "RUNT": ("RUNT", {"source": "SOURce", "polarity": "POLarity",
                          "qualifier": "QUALifier", "time": "TIME"}),
        "TRAN": ("TRANsition", {"source": "SOURce", "slope": "SLOPe",
                                "qualifier": "QUALifier", "time": "TIME"}),
        "TV": ("TV", {"source": "SOURce", "polarity": "POLarity",
                      "mode": "MODE", "standard": "STANdard",
                      "line": "LINE"}),
        # Modes that exist but whose parameter names were not identified:
        # setting the mode alone works, any parameter is reported.
        "USB": ("USB", {}),
        "DEL": ("DELay", {}),
        "SHOL": ("SHOLd", {"slope": "SLOPe"}),
    }

    #: Items that name a channel, given as 1-4 in the JSON.
    CHANNEL_ITEMS = ("source",)

    def resolve_mode(self, value):
        """Canonical short mode name for a spelling the user wrote, or None."""
        text = str(value).strip().upper()
        for short, (long_name, _) in self.MODES.items():
            if text in (short, long_name.upper()):
                return short
        return None

    def format_value(self, item, value):
        """SCPI text for one value. Raises ValueError for a bad one."""
        if item in self.CHANNEL_ITEMS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"expected a channel number 1-4, got {value!r}")
            number = int(value)
            if not 1 <= number <= 4:
                raise ValueError(f"channel {number} is out of range 1-4")
            return f"CHANnel{number}"
        return value

    def plan_mode(self, value):
        """Plan for the "mode" item. Written before the mode's parameters."""
        if self.resolve_mode(value) is None:
            return (f"mode {value!r} is not available on this instrument "
                    f"(valid: {', '.join(sorted(self.MODES))})")
        return f":TRIGger:MODE {value}", ":TRIGger:MODE?", True

    def plan_param(self, mode_key, item, value):
        """Plan for one mode parameter.

        `mode_key` is None when the trigger object names no mode, which is
        reported rather than guessed at.
        """
        if mode_key is None:
            return ("trigger parameters need a \"mode\" "
                    f"(valid: {', '.join(sorted(self.MODES))})")
        nodes = self.MODES[mode_key][1]
        node = nodes.get(item)
        if node is None:
            valid = ", ".join(sorted(nodes)) if nodes else "none identified"
            return f"{item!r} is not a {mode_key} parameter (valid: {valid})"
        return (f":TRIGger:{mode_key}:{node} {self.format_value(item, value)}",
                f":TRIGger:{mode_key}:{node}?", False)


CH1 = Channel(1)
CH2 = Channel(2)
CH3 = Channel(3)
CH4 = Channel(4)
CHANNELS = {channel.number: channel for channel in (CH1, CH2, CH3, CH4)}
TIMEBASE = Timebase()
TRIGGER = Trigger()


def to_channel_number(key):
    """Channel number from a JSON object key, which is always a string."""
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


def plan_run(value):
    """Run/stop: a bare command, not the "prefix:node value" form."""
    return ("RUN" if value else "STOP"), None, False


def iter_settings(settings):
    """Flatten the nested settings block into (key, plan, value, group) entries.

    `plan(value)` returns (command, readback_query, apply_first), or None when
    the item is not a control of its group. `plan` itself is None for an
    unrecognised section, and `group` is the owning ScpiGroup where there is
    one, so the caller can name the valid items when reporting a typo.

    `key` is an internal name written as the path through the JSON, such as
    ``channels.2.probe`` or ``timebase.scale``. It never appears in the config;
    it identifies the setting for diffing and is echoed verbatim in warnings,
    so an "unknown setting 'channels.2.bogus'" message points at the JSON.
    """
    for section, contents in settings.items():
        if section == "channels" and isinstance(contents, dict):
            for number, items in contents.items():
                channel = CHANNELS.get(to_channel_number(number))
                if channel is None or not isinstance(items, dict):
                    yield f"channels.{number}", None, contents, None
                    continue
                for item, value in items.items():
                    yield (f"channels.{channel.number}.{item}",
                           partial(channel.plan, item), value, channel)
        elif section == "timebase" and isinstance(contents, dict):
            for item, value in contents.items():
                yield (f"timebase.{item}",
                       partial(TIMEBASE.plan, item), value, TIMEBASE)
        elif section == "trigger" and isinstance(contents, dict):
            # The mode is resolved first because it decides the SCPI prefix the
            # other parameters are built under, so it is applied first too.
            mode_value = contents.get("mode")
            mode_key = (TRIGGER.resolve_mode(mode_value)
                        if mode_value is not None else None)
            if mode_value is not None:
                yield "trigger.mode", TRIGGER.plan_mode, mode_value, TRIGGER
            for item, value in contents.items():
                if item == "mode":
                    continue
                yield (f"trigger.{item}",
                       partial(TRIGGER.plan_param, mode_key, item), value,
                       TRIGGER)
        elif section == "run":
            yield "run", plan_run, contents, None
        else:
            yield section, None, contents, None


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, config):
    """Rewrite a JSON file atomically.

    Used only for the request file: to lay out the request panel at startup and
    to put handled requests back to their idle value. A temp file plus
    `os.replace` keeps the watcher from ever reading a half-written file. It
    changes the mtime, which makes the server look at the file once more --
    harmless, because by then nothing in it is asking for anything.
    """
    directory = os.path.dirname(path) or "."
    handle_fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=".scope_config-", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def connect(resource_name):
    rm = pyvisa.ResourceManager("@py")
    inst = rm.open_resource(resource_name)
    inst.timeout = 5000
    return inst


def scpi_short(text):
    """Short SCPI mnemonic of a token: the spelling without its lowercase.

    SCPI canonical spellings mark the short form with capitals, so
    ``CHANnel2`` reads back as ``CHAN2`` and ``POSitive`` as ``POS``. Used to
    compare an enum we wrote against what the instrument reports.

    A token with no capitals at all is taken as already plain and merely
    upper-cased -- stripping lowercase from ``glitch`` would leave nothing,
    which would report a false mismatch.
    """
    text = str(text)
    if not any(c.isupper() for c in text):
        return text.upper()
    return "".join(c for c in text if not c.islower()).upper()


def verify_write(inst, query, command):
    """Read a setting back and describe any mismatch.

    Compares against the value actually written -- the token at the end of
    `command` -- rather than the requested value, so a control that normalises
    its input is checked against what the instrument was told: `display`
    accepts true/false and writes 1/0, `source` accepts 2 and writes CHANnel2.

    Numbers compare numerically; anything else compares as SCPI mnemonics, long
    or short, which is what catches a typo'd enum. The instrument keeps the old
    value when it rejects one, so without this the write would look applied.

    Returns None when it matches, otherwise a short message saying what the
    instrument actually stored.
    """
    try:
        actual = inst.query(query).strip()
    except Exception as e:
        return f"readback failed: {e}"
    written = command.rpartition(" ")[2]
    try:
        if abs(float(actual) - float(written)) < 1e-9:
            return None
        return f"scope reports {actual}, wrote {written!r}"
    except (TypeError, ValueError):
        pass
    expected, seen = scpi_short(written), scpi_short(actual)
    if expected and seen and (seen.startswith(expected)
                              or expected.startswith(seen)):
        return None
    return f"scope reports {actual}, wrote {written!r}"


def apply_settings(inst, settings, last):
    """Apply a nested settings block, sending only what changed.

    Items listed in a group's FIRST are written before the rest, so a probe
    change cannot undo a scale change by landing after it. A write the
    instrument silently clamped is reported but still recorded, so it is not
    retried on every poll.

    Returns the new last-applied dict, keyed by internal flat names.
    """
    if not isinstance(settings, dict):
        print(f"  ! 'settings' must be an object, found "
              f"{type(settings).__name__} (ignored)", flush=True)
        return dict(last)

    planned = []
    for key, plan, value, group in iter_settings(settings):
        if plan is None:
            print(f"  ! ignoring unknown setting '{key}'", flush=True)
            continue
        try:
            result = plan(value)
        except ValueError as e:
            print(f"  ! bad value for '{key}': {e}", flush=True)
            continue
        if isinstance(result, str):
            # A plan may reject its own value with an explanation, which beats
            # the generic "unknown item" message for things like trigger modes.
            print(f"  ! {key}: {result}", flush=True)
            continue
        if result is None:
            if group is None:
                print(f"  ! ignoring unknown setting '{key}'", flush=True)
            else:
                print(f"  ! ignoring unknown item '{key}' "
                      f"(valid: {', '.join(group.ITEMS)})", flush=True)
            continue
        planned.append((key, value, result))

    # sorted() is stable, so everything that is not a FIRST item keeps the
    # order it had in the JSON.
    planned.sort(key=lambda entry: 0 if entry[2][2] else 1)

    applied = dict(last)
    for key, value, (command, readback, _) in planned:
        if last.get(key, object()) == value:
            continue
        try:
            inst.write(command)
        except Exception as e:
            print(f"  ! failed '{command}': {e}", flush=True)
            continue
        print(f"  -> {command}", flush=True)
        if readback is not None:
            problem = verify_write(inst, readback, command)
            if problem:
                print(f"  ! {key}={value} not applied as asked ({problem})",
                      flush=True)
        applied[key] = value
    return applied


def action_path(value):
    """File path an action value names, or None if it names nothing usable.

    An action value is either a path string, or an object with a "path" key:

        "save_png": "captures/shot.png"
        "capture_png": {"path": "captures/hit_{timestamp}.png", "timeout_s": 5}

    No field exists to bump in order to fire the action again: the server puts
    the whole entry back to its idle value once it has acted, so the object form
    carries only real options.
    """
    path = value.get("path") if isinstance(value, dict) else value
    return path if isinstance(path, str) and path.strip() else None


def action_options(value):
    """Extra options for an action: {} for the plain string form.

    The object form carries settings besides the path, e.g. a capture timeout,
    and those differ per action so each handler reads the ones it knows.
    """
    return value if isinstance(value, dict) else {}


def expand_path(template):
    """Expand the {timestamp} placeholder in an action path."""
    return template.replace("{timestamp}", time.strftime("%Y%m%d-%H%M%S"))


def trigger_event_set(inst):
    """True when the trigger event register reads non-zero."""
    try:
        return float(inst.query(TRIGGER_EVENT_QUERY).strip()) != 0
    except ValueError:
        return False


def arm_single_acquisition(inst):
    """Arm one acquisition, like pressing the front-panel Single key.

    `*CLS` goes first so a trigger-event latch left over from an earlier
    acquisition cannot be mistaken for this one; see arm_and_wait().
    """
    inst.write("*CLS")
    inst.write(":SINGle")


def arm_and_wait(inst, timeout_s):
    """Arm one acquisition and wait for the trigger to actually fire.

    Raises TimeoutError when no trigger arrives, which is the common case when
    the signal never crosses the level -- an armed scope waits indefinitely, so
    a timeout is not optional.
    """
    arm_single_acquisition(inst)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if trigger_event_set(inst):
            return
        time.sleep(TRIGGER_POLL_INTERVAL)
    raise TimeoutError(
        f"no trigger within {timeout_s:g}s; the scope is still armed, so check "
        f"the trigger level, source and mode against the signal")


def arm_single(inst, path, options):
    """The front-panel Single key: arm one acquisition, then return.

    Unlike capture_png this waits for nothing and writes no file, so it takes
    no path. Set it to 1 again to arm again.
    """
    arm_single_acquisition(inst)
    return "armed, now waiting for a trigger"


def save_screenshot(inst, path, options):
    """Write a PNG screenshot of what is on screen to `path`.

    Returns a short description of what was written. Raising is fine: the caller
    reports it and puts the request back to idle, so a failure is said once
    rather than lingering to fire again later.
    """
    data = inst.query_binary_values(SCREENSHOT_QUERY, datatype="B",
                                    container=bytes)
    if not data.startswith(PNG_MAGIC):
        raise ValueError(f"expected PNG data, got {data[:8]!r}")
    with open(path, "wb") as handle:
        handle.write(data)
    return f"{len(data)} bytes"


def capture_png(inst, path, options):
    """Wait for a trigger, then save the screen.

    The acquisition is armed as a single, so the scope holds the captured event
    on screen -- the same thing the front-panel Single key does. It is left
    stopped there; pass "resume": true to go back to continuous acquisition
    after the screenshot.
    """
    timeout_s = float(options.get("timeout_s", CAPTURE_TIMEOUT_S))
    arm_and_wait(inst, timeout_s)
    detail = save_screenshot(inst, path, options)
    if options.get("resume"):
        inst.write(":RUN")
    return detail


# Request name -> handler(inst, absolute_path, options) -> short description.
# `path` is None for requests listed in PATHLESS_REQUESTS. The order here is the
# order the names appear in the request panel, commonest first.
REQUESTS = {
    "single": arm_single,
    "save_png": save_screenshot,
    "capture_png": capture_png,
}

#: Requests that do not write a file, so they need no "path".
PATHLESS_REQUESTS = frozenset({"single"})

#: Value that means "not asking for anything". The request file is a control
#: panel: every known request sits in it at this value until you replace it with
#: something real, and the server puts the value back once it has acted -- so the
#: panel stays put and firing the same request again is a one-character edit.
REQUEST_IDLE = 0


def is_request(value):
    """True when a request value asks for something rather than being idle.

    The idle value is `0`, and so is every other way of writing "nothing": an
    empty string, `null` and `false`. Anything else is a request -- `true`, a
    non-zero number, a path, or an options object.
    """
    if value is None or isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return bool(value.strip())
    return True


def pending_requests(panel):
    """The entries of `panel` that ask for something, as {name: value}."""
    return {name: value for name, value in panel.items()
            if is_request(value)}


def request_panel():
    """Every known request name at its idle value, in panel order."""
    return {name: REQUEST_IDLE for name in REQUESTS}


def reset_requests(path, handled, fallback=None):
    """Put the requests that were just acted on back to their idle value.

    `handled` maps each request name to the value that was acted on. The file is
    re-read first and only an entry still holding exactly that value is reset:
    re-reading keeps a request you added while the last one ran from being
    written away, and comparing values means a fresh value under the same name
    (asking for `single` again while the first is still being carried out)
    survives to be run rather than being swallowed.

    Only the handled names are touched, and only the request file is written --
    never the config file.
    """
    try:
        panel = load_config(path)
    except (json.JSONDecodeError, OSError):
        panel = fallback
    if not isinstance(panel, dict):
        return

    changed = False
    for name, value in handled.items():
        if is_request(value) and name in panel and panel[name] == value:
            panel[name] = REQUEST_IDLE
            changed = True
    if not changed:
        return
    try:
        write_json(path, panel)
    except OSError as e:
        print(f"  ! could not reset the request file: {e}", flush=True)


def prepare_requests_file(path):
    """Lay the request panel out, dropping anything left over. True if usable.

    Run once at startup. Every known request is added at its idle value, so the
    file doubles as a menu of what the server can do and as the place you say
    what to do next. Anything non-idle at that moment is a leftover from an
    earlier session -- nobody is asking for it now -- so it is put back to idle
    and named in the console instead of being carried out.

    False means the file could not be read or written, so the caller cannot
    trust its contents and should not run what is in it.
    """
    try:
        panel = load_config(path)
    except FileNotFoundError:
        panel = {}  # first run: lay out a bare panel
    except json.JSONDecodeError as e:
        print(f"  ! request file has a JSON error, left alone: {e}", flush=True)
        return False
    except OSError as e:
        print(f"  ! cannot read the request file: {e}", flush=True)
        return False

    if not isinstance(panel, dict):
        print(f"  ! the request file must contain an object, found "
              f"{type(panel).__name__} (left alone)", flush=True)
        return False

    left_over = sorted(pending_requests(panel))
    added = [name for name in REQUESTS if name not in panel]
    if left_over or added:
        laid_out = request_panel()
        laid_out.update(panel)  # keep names we know nothing about, as they are
        for name in left_over:
            laid_out[name] = REQUEST_IDLE
        try:
            write_json(path, laid_out)
        except OSError as e:
            print(f"  ! cannot write the request file: {e}", flush=True)
            return False

    if added:
        print(f"  request panel: added {', '.join(added)}", flush=True)
    if left_over:
        print("  ! dropping requests left over from a previous session: "
              f"{', '.join(left_over)}", flush=True)
    return True


def handle_requests(inst, requests, base_dir):
    """Carry out the pending `requests`, returning the ones it acted on.

    `requests` is already narrowed to the entries that ask for something (see
    pending_requests), and the return value is that same {name: value} mapping so
    the caller can reset exactly those to idle afterwards.

    Every entry is attempted and reported, successful or not. Leaving a failed
    one non-idle would make it fire again later, when some unrelated edit changes
    the file, which is a surprise nobody asked for.
    """
    handled = {}
    for name, value in requests.items():
        # Recorded before the branches below: from here on this entry counts as
        # attempted, so the caller resets it rather than leaving it to fire a
        # second time.
        handled[name] = value

        handler = REQUESTS.get(name)
        if handler is None:
            print(f"  ! ignoring unknown request '{name}' "
                  f"(valid: {', '.join(sorted(REQUESTS))})", flush=True)
            continue

        path = None
        if name not in PATHLESS_REQUESTS:
            path = action_path(value)
            if path is None:
                print(f"  ! request '{name}' needs a file path", flush=True)
                continue
            path = expand_path(path)
            if not os.path.isabs(path):
                path = os.path.join(base_dir, path)

        try:
            if path is not None:
                directory = os.path.dirname(path)
                if directory and not os.path.isdir(directory):
                    os.makedirs(directory, exist_ok=True)
            time.sleep(ACTION_SETTLE_SECONDS)
            detail = handler(inst, path, action_options(value))
        except Exception as e:
            print(f"  ! request '{name}' failed: {e}", flush=True)
            continue
        where = f" -> {path}" if path is not None else ""
        print(f"  ** {name}{where} ({detail})", flush=True)
    return handled


def parse_args():
    parser = argparse.ArgumentParser(
        description="Watch a JSON config and send SCPI commands on changes.",
        epilog="Example: python scope_server.py my_scope.json",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the JSON config file (default: %(default)s).",
    )
    parser.add_argument(
        "--requests",
        default=None,
        help="Path to the request file, the panel of one-shot requests the "
             "server lays out and resets to idle as it handles it "
             "(default: %s beside the config file)."
             % DEFAULT_REQUESTS_FILENAME,
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config_path = os.path.abspath(args.config)
    requests_path = (os.path.abspath(args.requests) if args.requests
                     else os.path.join(os.path.dirname(config_path),
                                       DEFAULT_REQUESTS_FILENAME))

    print(f"Watching {config_path}", flush=True)
    print(f"Requests {requests_path}", flush=True)

    inst = None
    last_settings = {}
    last_config_mtime = None
    last_requests_mtime = None
    current_resource = None
    #: True once the request panel has been laid out and cleared of leftovers, so
    #: that a non-idle entry is known to be something asked for just now.
    started = prepare_requests_file(requests_path)

    while True:
        try:
            config_mtime = os.stat(config_path).st_mtime
        except OSError:
            config_mtime = None
        try:
            requests_mtime = os.stat(requests_path).st_mtime
        except OSError:
            requests_mtime = None

        config_changed = config_mtime != last_config_mtime
        requests_changed = requests_mtime != last_requests_mtime
        last_config_mtime = config_mtime
        last_requests_mtime = requests_mtime

        if not (config_changed or requests_changed):
            time.sleep(POLL_INTERVAL)
            continue

        if config_changed and config_mtime is not None:
            try:
                config = load_config(config_path)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Config has a JSON error (kept old state): {e}",
                      flush=True)
                time.sleep(POLL_INTERVAL)
                continue

            resource_name = config["resource"]
            settings = config.get("settings", {})
            report_unknown_config_keys(config)

            if inst is None or resource_name != current_resource:
                if inst is not None:
                    inst.close()
                print(f"Connecting to {resource_name} ...", flush=True)
                try:
                    inst = connect(resource_name)
                    current_resource = resource_name
                    last_settings = {}  # force full apply on new device
                    print(f"Connected: "
                          f"{inst.query('*IDN?').strip()}", flush=True)
                except Exception as e:
                    print(f"Connect failed: {e}", flush=True)
                    inst = None
                    current_resource = None

            if inst is not None:
                print("Applying settings:", flush=True)
                last_settings = apply_settings(inst, settings, last_settings)

        if requests_changed and requests_mtime is not None:
            try:
                panel = load_config(requests_path)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Request file has a JSON error (ignored): {e}",
                      flush=True)
                continue

            if not isinstance(panel, dict):
                print(f"  ! the request file must contain an object, found "
                      f"{type(panel).__name__} (ignored)", flush=True)
                continue

            waiting = pending_requests(panel)
            if waiting:
                if not started:
                    # The startup pass could not read the file, so a non-idle
                    # entry may be a leftover from an earlier session: reset it
                    # without running it. It has loaded cleanly now, so stop
                    # distrusting it either way.
                    print("  ! dropping requests left over from a previous "
                          f"session: {', '.join(sorted(waiting))}", flush=True)
                    handled = waiting
                    started = True
                elif inst is None:
                    print("  ! requests pending but no instrument is "
                          "connected; dropping them", flush=True)
                    handled = waiting
                else:
                    print("Handling requests:", flush=True)
                    handled = handle_requests(inst, waiting,
                                              os.path.dirname(requests_path))
                # Each attempted request goes back to its idle value, so the
                # panel stays put and asking again is a one-character edit. A
                # failed one is reset too: leaving it non-idle would fire it
                # again later, when an unrelated edit happens to change the file.
                reset_requests(requests_path, handled, panel)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
