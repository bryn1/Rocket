"""Fixtures for test_full_campaign (MC 10373): synthetic tiny frames with
ISO date strings, Title-cased, zigzag Close so edge-triggered signals fire."""
import pandas as pd


def _dates(n: int) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range("2025-01-02", periods=n)]


def date_at(n: int, i: int) -> str:
    return _dates(n)[i]


def synth_frames(n: int) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for t in ("AAA", "BBB", "CCC"):
        # segmented slope so RSI crosses its band after warm-up (edges fire)
        close = [100.0 + i for i in range(min(n, 140))]
        close += [close[-1] - i for i in range(1, min(n, 200) - 139)]
        close += [close[-1] + i for i in range(1, max(0, n - 199))]
        out[t] = pd.DataFrame({
            "date": _dates(n),
            "Open": [c - 0.5 for c in close],
            "High": [c + 1.0 for c in close],
            "Low": [c - 1.0 for c in close],
            "Close": close,
            "Volume": [1000.0 + i for i in range(n)],
        })
    return out
