"""Leser skjermkortet med nvidia-smi (kommer fra driveren via --runtime=nvidia)."""

from __future__ import annotations

import subprocess

SPORRING = "name,memory.used,memory.free,memory.total"


def les_gpuer(tidsfrist: float = 10) -> list[dict]:
    """Én rad per GPU. Tom liste hvis nvidia-smi mangler eller feiler."""
    try:
        ut = subprocess.run(
            ["nvidia-smi", f"--query-gpu={SPORRING}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=tidsfrist, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return tolk(ut)


def tolk(ut: str) -> list[dict]:
    gpuer = []
    for linje in ut.strip().splitlines():
        deler = [d.strip() for d in linje.split(",")]
        if len(deler) != 4:
            continue
        navn, brukt, ledig, totalt = deler
        try:
            gpuer.append({
                "navn": navn,
                "brukt_gb": round(int(brukt) / 1024, 1),
                "ledig_gb": round(int(ledig) / 1024, 1),
                "totalt_gb": round(int(totalt) / 1024, 1),
            })
        except ValueError:
            continue
    return gpuer


def ledig_vram_gb() -> float | None:
    gpuer = les_gpuer()
    return min(g["ledig_gb"] for g in gpuer) if gpuer else None
