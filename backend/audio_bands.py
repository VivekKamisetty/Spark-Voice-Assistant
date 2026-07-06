"""Cheap per-block FFT band energies (bass/mid/high), shared by the mic
listener and TTS playback so the orb can react to actual frequency content
instead of a single overall RMS value. There's no audio stream reaching the
Electron renderer (mic audio never leaves Python; TTS plays natively via
sounddevice), so this runs here rather than via a browser-side AnalyserNode —
same numpy cost as the RMS calculation it replaces, just bucketed by
frequency instead of averaged flat.
"""

import numpy as np

BASS_RANGE = (20.0, 250.0)
MID_RANGE = (250.0, 2000.0)
HIGH_RANGE = (2000.0, 8000.0)

# Empirical scale so typical speech/mic levels land roughly in 0-1 for the
# shader uniforms that consume these — matches the spirit of the old
# `rms * 6` scaling constant it replaces, tuned by ear/eye rather than derived.
BAND_SCALE = 9.0


def compute_bands(block: np.ndarray, sample_rate: int) -> tuple[float, float, float]:
    """Returns (bass, mid, high) energy for one audio block, each roughly in
    0-1 for normal speech levels. Coarse by design (single-block FFT, no
    windowing beyond a simple Hann taper) — this drives a reactive visual,
    not spectral analysis.
    """
    n = len(block)
    if n < 8:
        return 0.0, 0.0, 0.0

    windowed = block * np.hanning(n)
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    def band_energy(lo: float, hi: float) -> float:
        mask = (freqs >= lo) & (freqs < hi)
        if not np.any(mask):
            return 0.0
        energy = float(np.sqrt(np.mean(spectrum[mask] ** 2))) / n
        return min(energy * BAND_SCALE, 1.4)

    bass = band_energy(*BASS_RANGE)
    mid = band_energy(*MID_RANGE)
    high = band_energy(HIGH_RANGE[0], min(HIGH_RANGE[1], sample_rate / 2))

    return bass, mid, high
