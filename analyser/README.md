# Waveform Analyzer

Time-domain and frequency-domain (FFT) plotter for Rigol DS1104Z CSV waveform exports.

## Setup

Requires `numpy` and `matplotlib`:

```powershell
pip install numpy matplotlib
```

## Usage

```powershell
cd analyser
python fft.py                         # uses RigolDS1.csv in this directory
python fft.py path/to/another.csv     # specify a different CSV
python fft.py file.csv --fmax 10      # zoom frequency axis to 0-10 kHz
```

The script opens an interactive matplotlib window with two subplots:

- **Top** — time domain: voltage vs time
- **Bottom** — frequency domain: averaged magnitude spectrum (Welch's method, Hann window)

Add `--raw` to use the un-averaged FFT instead.

### `--fmax`

Use `--fmax` to cap the frequency axis when your signal is low-frequency but the sample rate is high. For example, a 1 kHz gate signal captured at 10 MHz yields a 0-5 MHz spectrum where the signal is invisible — `--fmax 10` zooms in to 0-10 kHz.

### `--raw`

Uses a single full-length FFT instead of Welch averaging. Preserves maximum frequency resolution but produces a noisy (spiky) spectrum for large captures. The default Welch mode averages overlapping windowed segments for a smooth, readable plot.

### `--nperseg`

Controls the Welch segment length in samples. This directly determines frequency resolution:

```
bin spacing (Hz) = sample_rate / nperseg
```

| `nperseg` | Bin spacing @ 100 kS/s | Bin spacing @ 1 MS/s |
|---|---|---|
| 1024 (default) | 97.7 Hz | 977 Hz |
| 8192 | 12.2 Hz | 122 Hz |
| 65536 | 1.5 Hz | 15.3 Hz |
| 131072 | 0.76 Hz | 7.6 Hz |

Larger values give finer frequency resolution but fewer overlapping segments to average (smoother spectrum requires more segments). The segment length is clamped to the signal length if the signal is shorter than `nperseg`.

For a 160 Hz signal at 100 kS/s, `--nperseg 65536` gives ~1.5 Hz bins -- enough to clearly resolve the peak. Use `--raw` if you need the full signal-length resolution.

```powershell
python fft.py file.csv --nperseg 65536
```

### `--peak`

Prints the spectral peak (frequency and magnitude) to stdout. When combined with `--export`, writes only the peak row to the CSV instead of the full spectrum.

```powershell
python fft.py file.csv --peak                        # print peak only
python fft.py file.csv --peak --export peak.csv      # export peak only
python fft.py file.csv --nperseg 65536 --peak        # finer resolution + peak
```

### `--export`

Saves the FFT result (frequency and magnitude columns) to a CSV file instead of opening a plot. Does not require matplotlib.

```powershell
python fft.py file.csv --export fft_data.csv
```

Output CSV header: `Frequency(Hz),Magnitude(V)`

## CSV Format

Expected header: `Time(s),CH1(V)`. The Rigol scope exports this format directly via its Save/Recall menu.

## Test CSV Generator

A pure-stdlib script in `test/` generates Rigol-format CSVs with a configurable sine wave — no oscilloscope needed.

```powershell
cd analyser/test
python generate_test_csv.py                    # uses config.json
python generate_test_csv.py -o my_wave.csv     # override output path
python generate_test_csv.py --config alt.json  # custom config
```

### Config (`test/config.json`)

```json
{
    "sample_rate_hz": 100000,
    "duration_s": 0.06283185,
    "amplitude_v": 2.0,
    "dc_offset_v": 0.0,
    "omega_rad_s": 1000.0,
    "output_path": "RigolDS1.csv"
}
```

| Key | Description |
|---|---|
| `sample_rate_hz` | Samples per second |
| `duration_s` | Signal duration in seconds |
| `amplitude_v` | Sine amplitude in volts |
| `dc_offset_v` | DC offset in volts |
| `omega_rad_s` | Angular frequency (ω = 2πf) in rad/s |
| `output_path` | Output CSV filename (relative to script dir) |

The generated CSV can be fed directly to `fft.py`:

```powershell
cd analyser
python fft.py test/RigolDS1.csv
```

## FFT Export Test

`test/test_fft_export.py` validates the FFT and CSV export pipeline: it generates a known sine wave, runs the FFT, exports to CSV, and checks the peak frequency and magnitude.

```powershell
python analyser/test/test_fft_export.py
```
