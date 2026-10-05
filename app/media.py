"""Forhåndsvisning, kontaktark og video med oiiotool og ffmpeg."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile

MAKS_PX = 1024
MINIATYR_PX = 336


def _kjor(kommando: list[str], tidsfrist: float = 600) -> None:
    resultat = subprocess.run(kommando, capture_output=True, text=True, timeout=tidsfrist)
    if resultat.returncode != 0:
        raise RuntimeError(f"{kommando[0]} feilet: {(resultat.stderr or resultat.stdout).strip()[-500:]}")


def _til_jpeg_args(bilde: str, px: int) -> list[str]:
    args = [bilde, "--ch", "R,G,B"]
    if bilde.lower().endswith(".exr"):
        args += ["--colorconvert", "linear", "sRGB"]  # EXR er lineært; JPEG skal være sRGB
    return args + ["--fit", f"{px}x{px}", "--fullpixels"]


def forhandsvisning(bilde: str, ut: str) -> None:
    """Nedskalert JPEG, maks 1024 px på lengste side. Skrives atomisk."""
    tmp = ut + ".tmp.jpg"
    _kjor(["oiiotool", *_til_jpeg_args(bilde, MAKS_PX), "-o", tmp], 120)
    os.replace(tmp, ut)


def velg_jevnt(filer: list[str], antall: int = 9) -> list[str]:
    """Plukker ut jevnt fordelte filer, med første og siste."""
    if len(filer) <= antall:
        return list(filer)
    return [filer[round(i * (len(filer) - 1) / (antall - 1))] for i in range(antall)]


def kontaktark(filer: list[str], ut: str) -> None:
    """3 × 3 rutenett av 9 jevnt fordelte bilder (én rad hvis færre enn 9)."""
    valgt = velg_jevnt(sorted(filer))
    if not valgt:
        return
    kolonner, rader = (3, 3) if len(valgt) == 9 else (len(valgt), 1)
    with tempfile.TemporaryDirectory() as tmp:
        miniatyrer = []
        for i, fil in enumerate(valgt):
            m = os.path.join(tmp, f"{i}.png")
            _kjor(["oiiotool", *_til_jpeg_args(fil, MINIATYR_PX), "-o", m], 120)
            miniatyrer.append(m)
        _kjor(["oiiotool", *miniatyrer, "--mosaic:pad=8", f"{kolonner}x{rader}",
               "--fit", f"{MAKS_PX}x{MAKS_PX}", "--fullpixels", "-o", ut], 300)


def video(bildemappe: str, endelse: str, fps: float, ut: str) -> None:
    mønster = os.path.join(bildemappe, f"bilde_*.{endelse}")
    if not glob.glob(mønster):
        raise RuntimeError("Fant ingen bilder å lage video av.")
    _kjor([
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", f"{fps:g}", "-pattern_type", "glob", "-i", mønster,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # libx264 med yuv420p krever partall
        ut,
    ], 3600)


def verktoy_finnes() -> dict[str, bool]:
    return {navn: shutil.which(navn) is not None for navn in ("oiiotool", "ffmpeg")}
