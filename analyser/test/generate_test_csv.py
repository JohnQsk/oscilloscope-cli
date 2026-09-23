"""
Test CSV generator for Rigol waveform analyzer.

Generates a Rigol-format CSV (Time(s),CH1(V)) with a configurable sine wave.
Parameters are read from a JSON config file.

Usage:
    cd analyser/test
    python generate_test_csv.py                    # uses config.json
    python generate_test_csv.py --config my.json   # custom config
    python generate_test_csv.py -o other.csv       # override output path
"""

import argparse
import json
import math
from pathlib import Path


def load_config(config_path: Path) -> dict:
    """Load signal-generation parameters from a JSON config file."""
    with open(config_path) as f:
        config = json.load(f)

    required = ["sample_rate_hz", "duration_s", "amplitude_v",
                "dc_offset_v", "omega_rad_s", "output_path"]
    for key in required:
        if key not in config:
            raise KeyError(f"Missing required config key: {key}")
    return config


def generate_signal(config: dict) -> tuple[list[float], list[float]]:
    """Generate time and voltage arrays for the sine wave.

    Returns (time_list, voltage_list) where time is in seconds
    and voltage = amplitude * sin(omega * t) + dc_offset.
    """
    sr = config["sample_rate_hz"]
    duration = config["duration_s"]
    amp = config["amplitude_v"]
    offset = config["dc_offset_v"]
    omega = config["omega_rad_s"]

    dt = 1.0 / sr
    n_samples = int(duration * sr)

    time = [i * dt for i in range(n_samples)]
    voltage = [amp * math.sin(omega * t) + offset + math.sin(2000*t) for t in time]
    return time, voltage


def write_csv(time: list[float], voltage: list[float], output_path: Path):
    """Write time and voltage to a Rigol-format CSV file."""
    with open(output_path, "w", newline="") as f:
        f.write("Time(s),CH1(V)\n")
        for t, v in zip(time, voltage):
            f.write(f"{t:.10e},{v:.6f}\n")

    print(f"Wrote {len(time)} samples to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Rigol-format test CSV with a configurable sine wave")
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to config JSON (default: config.json in script directory)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Override output CSV path from config")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    config_path = args.config or (script_dir / "config.json")

    print(f"Loading config: {config_path}")
    config = load_config(config_path)

    output_path = args.output or (script_dir / config["output_path"])

    print(f"  Sample rate:  {config['sample_rate_hz']:.0f} Hz")
    print(f"  Duration:     {config['duration_s'] * 1e3:.3f} ms")
    print(f"  Amplitude:    {config['amplitude_v']} V")
    print(f"  DC offset:    {config['dc_offset_v']} V")
    print(f"  Omega:        {config['omega_rad_s']} rad/s")
    print(f"  Equivalent f: {config['omega_rad_s'] / (2 * math.pi):.1f} Hz")

    time, voltage = generate_signal(config)
    write_csv(time, voltage, output_path)


if __name__ == "__main__":
    main()
