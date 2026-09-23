"""
Time-domain and frequency-domain analyzer for Rigol oscilloscope CSV exports.

Usage:
    python fft.py                              # uses RigolDS1.csv
    python fft.py path/to/waveform.csv         # custom CSV
    python fft.py file.csv --fmax 10           # zoom frequency axis to 10 kHz
    python fft.py file.csv --export fft.csv    # save FFT data to CSV (no plot)
    python fft.py file.csv --peak              # print spectral peak
    python fft.py file.csv --nperseg 32768     # finer Welch freq resolution
"""

import numpy as np
from pathlib import Path


def load_csv(filepath: str) -> tuple[np.ndarray, np.ndarray]:
    """Load time and voltage columns from a Rigol CSV export."""
    data = np.loadtxt(filepath, delimiter=",", skiprows=1)
    time = data[:, 0]         # seconds
    voltage = data[:, 1]      # volts
    return time, voltage


def plot_time_domain(ax, time: np.ndarray, voltage: np.ndarray):
    """Plot voltage vs time (time domain)."""
    ax.plot(time * 1e3, voltage, linewidth=0.6, color="steelblue")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Voltage (V)")
    ax.set_title("Time Domain")
    ax.grid(True, alpha=0.3)


def compute_fft(time: np.ndarray, voltage: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compute single-sided magnitude spectrum with Hann window."""
    n = len(voltage)
    dt = (time[-1] - time[0]) / (n - 1)          # sample spacing
    fs = 1.0 / dt                                  # sample rate

    # Apply Hann window to reduce spectral leakage
    window = np.hanning(n)
    v_windowed = voltage * window

    # FFT and single-sided magnitude
    spectrum = np.fft.rfft(v_windowed)
    magnitude = np.abs(spectrum) / n
    magnitude[1:-1] *= 2                          # double non-DC/Nyquist bins

    freq = np.fft.rfftfreq(n, d=dt)
    return freq, magnitude


def compute_welch(time: np.ndarray, voltage: np.ndarray,
                  nperseg: int = 1024,
                  overlap: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Compute averaged magnitude spectrum (Welch's method).

    Splits the signal into overlapping segments, applies a Hann window
    to each, computes the FFT, and averages the magnitudes.  This
    trades frequency resolution for a much smoother (lower-variance)
    spectrum — especially useful for large captures where the raw FFT
    looks like a forest of narrow spikes.

    Parameters
    ----------
    nperseg : desired segment length in samples (default 1024).
              Will be clamped to the signal length if the signal is shorter.
    overlap : fraction of overlap between segments (default 0.5).
    """
    n = len(voltage)
    dt = (time[-1] - time[0]) / (n - 1)
    fs = 1.0 / dt

    # Clamp segment length to signal length (avoid zero segments on short data)
    nperseg = min(nperseg, n)
    step = int(nperseg * (1 - overlap))
    window = np.hanning(nperseg)

    # Single-sided FFT bins: rfft of nperseg samples
    n_freqs = nperseg // 2 + 1
    accum = np.zeros(n_freqs, dtype=np.float64)
    n_segments = 0

    for start in range(0, n - nperseg + 1, step):
        segment = voltage[start:start + nperseg] * window
        spec = np.abs(np.fft.rfft(segment)) / nperseg
        spec[1:-1] *= 2                           # single-sided scaling
        accum += spec
        n_segments += 1

    magnitude = accum / n_segments
    freq = np.fft.rfftfreq(nperseg, d=dt)
    return freq, magnitude


def save_fft_csv(freq: np.ndarray, magnitude: np.ndarray, filepath: str):
    """Save FFT frequency and magnitude arrays to a CSV file."""
    header = "Frequency(Hz),Magnitude(V)"
    np.savetxt(filepath, np.column_stack((freq, magnitude)),
               delimiter=",", header=header, comments="",
               fmt="%.4f,%.6f")


def find_peak(freq: np.ndarray, magnitude: np.ndarray) -> tuple[float, float]:
    """Return the frequency and magnitude of the spectral peak."""
    idx = np.argmax(magnitude)
    return float(freq[idx]), float(magnitude[idx])


def plot_frequency_domain(ax, freq: np.ndarray, magnitude: np.ndarray,
                          fmax_khz: float | None = None):
    """Plot magnitude vs frequency (frequency domain).

    If fmax_khz is given, the x-axis is limited to [0, fmax_khz].
    Otherwise the full spectrum is shown.
    """
    ax.plot(freq * 1e-3, magnitude, linewidth=0.6, color="firebrick")
    ax.set_xlabel("Frequency (kHz)")
    ax.set_ylabel("Magnitude (V)")
    ax.set_title("Frequency Domain")
    ax.set_xlim(left=0, right=fmax_khz)
    ax.grid(True, alpha=0.3)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Time-domain and frequency-domain analyzer for Rigol CSV exports")
    parser.add_argument("csv", nargs="?", default=None,
                        help="Path to CSV file (default: RigolDS1.csv in script directory)")
    parser.add_argument("--fmax", type=float, default=None, metavar="KHZ",
                        help="Cap frequency axis at this value (kHz). "
                             "Useful when the signal is low-frequency but the "
                             "sample rate is high (e.g. 1 kHz signal at 10 MHz "
                             "sample rate -> use --fmax 10)")
    parser.add_argument("--raw", action="store_true",
                        help="Use raw FFT instead of Welch averaging "
                             "(Welch is smoother; raw preserves full frequency resolution).")
    parser.add_argument("--nperseg", type=int, default=1024, metavar="N",
                        help="Welch segment length in samples (default 1024). "
                             "Larger = finer freq resolution but fewer segments to average. "
                             "Freq bin spacing = sample_rate / nperseg.")
    parser.add_argument("--peak", action="store_true",
                        help="Print the spectral peak (frequency and magnitude). "
                             "With --export, writes only the peak row to CSV.")
    parser.add_argument("--export", type=str, default=None, metavar="PATH",
                        help="Save FFT data (freq, magnitude) to a CSV file instead of plotting.")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    csv_path = args.csv or str(script_dir / "RigolDS1.csv")

    print(f"Loading: {csv_path}")
    time, voltage = load_csv(csv_path)
    print(f"  Samples: {len(voltage)}")
    print(f"  Time range: {time[0]*1e3:.3f} ms to {time[-1]*1e3:.3f} ms")
    sample_rate = 1/(time[1]-time[0])
    print(f"  Sample rate: {sample_rate:.1f} Hz")

    if args.raw:
        freq, magnitude = compute_fft(time, voltage)
    else:
        freq, magnitude = compute_welch(time, voltage, nperseg=args.nperseg)
        bin_spacing = sample_rate / min(args.nperseg, len(voltage))
        print(f"  Welch: {len(freq)} freq bins, ~{bin_spacing:.2f} Hz spacing")

    if args.peak:
        peak_freq, peak_mag = find_peak(freq, magnitude)
        print(f"  Peak: {peak_freq:.4f} Hz, {peak_mag:.6f} V")

    if args.export:
        if args.peak:
            peak_freq, peak_mag = find_peak(freq, magnitude)
            save_fft_csv(np.array([peak_freq]), np.array([peak_mag]), args.export)
        else:
            save_fft_csv(freq, magnitude, args.export)
        print(f"Exported to {args.export}")
    elif not args.peak:
        import matplotlib.pyplot as plt
        fig, (ax_t, ax_f) = plt.subplots(2, 1, figsize=(10, 7))
        plot_time_domain(ax_t, time, voltage)
        plot_frequency_domain(ax_f, freq, magnitude, args.fmax)

        fig.tight_layout()
        plt.show()


if __name__ == "__main__":
    main()
