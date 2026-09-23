#!/usr/bin/env python3
"""Set the ``resource`` field in a scope config JSON file.

Discovers instruments through the PyVISA-py (``@py``) backend, shows them as a
numbered list, and writes the one you pick into the config file consumed by
``scope_server.py``. Other keys in the config are preserved untouched.

The write is atomic (temp file + ``os.replace``), so a running
``scope_server.py`` never sees a half-written file and reconnects on its next
poll.

Examples
--------
Pick an instrument from the interactive list::

    python set_resource.py

Use a different config file::

    python set_resource.py -c keysight.json

Show the list (with model identification) and exit::

    python set_resource.py --list --identify

Skip the prompt by targeting a device directly -- hex or decimal VID both work::

    python set_resource.py --vid 0x0957
    python set_resource.py --match MY00000000
    python set_resource.py --index 2

Set an explicit string, probing no hardware at all::

    python set_resource.py --resource USB0::2391::6054::MY00000000::0::INSTR

Preview without writing::

    python set_resource.py --vid 0x0957 --dry-run
"""

import argparse
import json
import os
import re
import sys
import tempfile

try:
    import pyvisa
except ImportError:  # pragma: no cover - dependency guidance only
    sys.exit("pyvisa is not installed. Run: pip install -r requirements.txt")

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "scope_config.json")
IDENTIFY_TIMEOUT_MS = 2000

NO_RESOURCES_HINT = (
    "No instruments found on the '@py' backend.\n"
    "On Windows this usually means the scope has no WinUSB driver bound, or\n"
    "Windows bound its own USBTMC driver. See docs/usb-driver-setup.md."
)


def fail(message, code=1):
    """Print ``message`` to stderr and exit."""
    print("error: " + message, file=sys.stderr)
    sys.exit(code)


def parse_args(argv=None):
    """Parse command-line arguments (``argv`` defaults to ``sys.argv[1:]``)."""
    parser = argparse.ArgumentParser(
        description="Pick a connected instrument and store it in a scope "
                    "config JSON file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples\n--------\n", 1)[-1],
    )
    parser.add_argument(
        "-c", "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Config file to update (default: %(default)s).",
    )
    parser.add_argument(
        "--resource",
        help="Explicit VISA resource string. Skips discovery and the list.",
    )
    parser.add_argument(
        "--vid",
        help="Match USB vendor ID, e.g. 0x0957 or 2391. Bypasses the list.",
    )
    parser.add_argument(
        "--pid",
        help="Match USB product ID, e.g. 0x17A6 or 6054. Bypasses the list.",
    )
    parser.add_argument(
        "--match",
        help="Case-insensitive substring match on the resource string "
             "(handy for serial numbers). Bypasses the list.",
    )
    parser.add_argument(
        "--index",
        type=int,
        help="Pick the Nth entry from the discovery list (1-based). "
             "Bypasses the list.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show discovered instruments and exit without changing anything.",
    )
    parser.add_argument(
        "--identify",
        action="store_true",
        help="Query *IDN? to show the model. Opens each instrument, so it is "
             "slower and may fail while another tool holds the device.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change, but do not write the file.",
    )
    return parser.parse_args(argv)


def to_id(token):
    """Parse a VID/PID token as hex (``0x1AB1``) or decimal (``6833``).

    VISA resource strings appear in both forms in the wild -- pyvisa-py reports
    decimal, while hand-written configs often use hex -- so callers compare
    numbers rather than strings.
    """
    token = token.strip()
    try:
        if token.lower().startswith("0x"):
            return int(token, 16)
        return int(token, 10)
    except (AttributeError, ValueError):
        return None


def parse_resource(resource):
    """Break a VISA resource string into a display-friendly dict.

    Returns a dict with ``kind`` (``USB``, ``TCPIP``, ``ASRL``, ...) and, for
    USB resources, numeric ``vid``/``pid`` plus the ``serial``.
    """
    parts = [p.strip() for p in resource.split("::")]
    info = {"kind": "?", "vid": None, "pid": None, "serial": None}
    if parts and parts[0]:
        match = re.match(r"^([A-Za-z]+)", parts[0])
        if match:
            info["kind"] = match.group(1).upper()
    if info["kind"] == "USB" and len(parts) >= 4:
        info["vid"] = to_id(parts[1])
        info["pid"] = to_id(parts[2])
        info["serial"] = parts[3]
    return info


def describe(resource):
    """Return a one-line human description of a VISA resource string.

    Both hex and decimal VID/PID are shown, because pyvisa-py reports decimal
    in the resource string while this repo's docs quote hex -- and ``--vid``
    reads a ``0x`` prefix as hex and bare digits as decimal.
    """
    info = parse_resource(resource)
    if info["vid"] is not None and info["pid"] is not None:
        return "VID 0x%04X (%d)  PID 0x%04X (%d)  serial %s" % (
            info["vid"], info["vid"], info["pid"], info["pid"],
            info["serial"])
    return "%s resource" % info["kind"]


def format_list(resources):
    """Render a resource list as indented lines with descriptions."""
    return "\n".join("  [%d] %s\n      %s" % (i, r, describe(r))
                     for i, r in enumerate(resources, 1))


def identify(rm, resource):
    """Return the instrument's ``*IDN?`` reply, or an error description."""
    try:
        inst = rm.open_resource(resource)
    except Exception as exc:
        return "<open failed: %s>" % exc
    try:
        inst.timeout = IDENTIFY_TIMEOUT_MS
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        return inst.query("*IDN?").strip()
    except Exception as exc:
        return "<no response: %s>" % exc
    finally:
        try:
            inst.close()
        except Exception:
            pass


def print_device_list(resources, rm, do_identify, current=None, indent="  "):
    """Print the numbered instrument list, marking the currently configured one."""
    for position, resource in enumerate(resources, 1):
        marker = "   <- current" if resource == current else ""
        print("%s[%d] %s%s" % (indent, position, resource, marker))
        print("%s    %s" % (indent, describe(resource)))
        if do_identify:
            print("%s    IDN: %s" % (indent, identify(rm, resource)))


def default_choice(resources, current):
    """Return the 1-based default selection: the current device, else the first."""
    if current in resources:
        return resources.index(current) + 1
    return 1


def prompt_choice(resources, rm, do_identify, current):
    """Show the instrument list and interactively pick one."""
    print("Instruments found:")
    print_device_list(resources, rm, do_identify, current)

    default = default_choice(resources, current)
    prompt = "Select instrument [1-%d] (Enter = %d, q = quit): " % (
        len(resources), default)

    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            fail("aborted")
        if raw == "":
            return resources[default - 1]
        if raw.lower() in ("q", "quit"):
            fail("aborted")
        if raw.isdigit() and 1 <= int(raw) <= len(resources):
            return resources[int(raw) - 1]
        print("  ! enter a number between 1 and %d" % len(resources))


def select(resources, args, rm, current):
    """Resolve the target resource from filters, index, or the prompt."""
    if args.index is not None:
        if not 1 <= args.index <= len(resources):
            fail("--index %d is out of range; %d instrument(s) discovered"
                 % (args.index, len(resources)))
        return resources[args.index - 1]

    filters_supplied = (args.vid is not None or args.pid is not None
                        or args.match is not None)
    filtered = resources
    if args.vid is not None:
        wanted = to_id(args.vid)
        if wanted is None:
            fail("could not parse --vid %r as a number" % args.vid)
        filtered = [r for r in filtered if parse_resource(r)["vid"] == wanted]
    if args.pid is not None:
        wanted = to_id(args.pid)
        if wanted is None:
            fail("could not parse --pid %r as a number" % args.pid)
        filtered = [r for r in filtered if parse_resource(r)["pid"] == wanted]
    if args.match is not None:
        needle = args.match.lower()
        filtered = [r for r in filtered if needle in r.lower()]

    if filters_supplied:
        # Honour an explicit filter without prompting when it is unambiguous.
        # Note: tested on the flag, not on whether filtering changed the list --
        # a filter that matches everything is still an explicit instruction.
        if not filtered:
            fail("no instrument matched the given filter. Discovered:\n"
                 + format_list(resources))
        if len(filtered) == 1:
            return filtered[0]
        resources = filtered

    if not sys.stdin.isatty():
        if len(resources) == 1:
            return resources[0]
        fail("multiple instruments found and no terminal to prompt on:\n"
             + format_list(resources)
             + "\nRe-run with --index N, --vid/--pid, --match, or --resource.")

    return prompt_choice(resources, rm, args.identify, current)


def load_config(path):
    """Load and validate the config JSON, exiting cleanly if unusable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except json.JSONDecodeError as exc:
        fail("%s is not valid JSON: %s" % (path, exc))
    except OSError as exc:
        fail("cannot read %s: %s" % (path, exc))
    if not isinstance(config, dict):
        fail("%s must contain a JSON object, found %s"
             % (path, type(config).__name__))
    return config


def save_config(path, config):
    """Write ``config`` as pretty JSON, atomically.

    A temp file in the destination directory plus ``os.replace`` means a
    concurrent reader (scope_server.py polls this file) only ever sees the
    complete old or complete new content. The parent directory is created if
    the given path points somewhere that does not exist yet.
    """
    directory = os.path.dirname(path) or "."
    if not os.path.isdir(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            fail("cannot create directory %s: %s" % (directory, exc))

    try:
        handle_fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".scope_config-", suffix=".tmp")
    except OSError as exc:
        fail("cannot write into %s: %s" % (directory, exc))

    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        fail("cannot write %s: %s" % (path, exc))


def report(previous, target):
    """Print the before/after resource values."""
    print("  resource: %s" % ("(unset)" if previous is None else previous))
    print("         -> %s" % target)


def main():
    args = parse_args()
    config_path = os.path.abspath(os.path.expanduser(args.config))

    # Read the config first: the current device becomes the default selection.
    exists = os.path.exists(config_path)
    if exists:
        config = load_config(config_path)
        previous = config.get("resource")
    else:
        config = {}
        previous = None

    rm = None
    resources = []
    if not args.resource:
        rm = pyvisa.ResourceManager("@py")
        resources = sorted(rm.list_resources())

    if args.list:
        if not resources:
            print("No instruments found.")
            print(NO_RESOURCES_HINT)
            return 1
        print("Instruments found:")
        print_device_list(resources, rm, args.identify, previous)
        return 0

    if args.resource:
        target = args.resource
        if args.identify:
            rm = pyvisa.ResourceManager("@py")
            print("IDN: %s" % identify(rm, target))
    else:
        if not resources:
            fail(NO_RESOURCES_HINT)
        target = select(resources, args, rm, previous)

    if previous == target:
        print("%s already points at %s"
              % (os.path.basename(config_path), target))
        return 0

    config["resource"] = target

    if args.dry_run:
        print("dry run, %s not written:" % os.path.basename(config_path))
        report(previous, target)
        return 0

    if not exists:
        print("note: %s does not exist; creating it"
              % os.path.basename(config_path), file=sys.stderr)

    save_config(config_path, config)
    print("%s updated:" % os.path.basename(config_path))
    report(previous, target)
    print("scope_server.py will reconnect on its next poll.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
