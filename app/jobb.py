"""Rene hjelpefunksjoner for jobber: bilderute, filnavn, Blender-kommando og logglinjer."""

from __future__ import annotations

import json
import os
import re
import time

FORMATER = {"PNG": "png", "JPEG": "jpg", "OPEN_EXR": "exr"}
MOTORER = ("CYCLES", "BLENDER_EEVEE", "fra_fil")
MAKS_BILDER = 100_000

_RUTE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")
_FRA = re.compile(r"\bFra:\s*(\d+)")
_LAGRET = re.compile(r"\bSaved:\s*'(.+)'")


class UgyldigJobb(ValueError):
    pass


def sjekk_bilderute(tekst: str | None) -> None:
    """Sjekker bare syntaksen. Tom betyr filens område."""
    if tekst is None or not str(tekst).strip():
        return
    m = _RUTE.match(str(tekst))
    if not m:
        raise UgyldigJobb(f"Ugyldig bilderute «{tekst}». Bruk et tall (\"1\"), en rute (\"1-250\") eller la feltet stå tomt.")
    start, slutt = int(m.group(1)), int(m.group(2) or m.group(1))
    if slutt < start:
        raise UgyldigJobb(f"Ugyldig bilderute «{tekst}»: siste bilde er før første.")
    if slutt - start + 1 > MAKS_BILDER:
        raise UgyldigJobb(f"Bilderuten «{tekst}» har mer enn {MAKS_BILDER} bilder.")


def bildeliste(tekst: str | None, info: dict) -> list[int]:
    """Bildenumrene som skal rendres. Tom rute = filens område og steg."""
    sjekk_bilderute(tekst)
    if tekst is None or not str(tekst).strip():
        start, slutt, steg = info["bilde_start"], info["bilde_slutt"], max(1, info.get("bilde_steg", 1))
    else:
        m = _RUTE.match(str(tekst))
        start, slutt, steg = int(m.group(1)), int(m.group(2) or m.group(1)), 1
    if slutt < start:
        raise UgyldigJobb(f"Filen har ugyldig bildeområde {start}–{slutt}.")
    return list(range(start, slutt + 1, steg))


def bildefil(mappe: str, nr: int, fmt: str) -> str:
    return f"{mappe}/bilder/bilde_{nr:04d}.{FORMATER[fmt]}"


def ferdige_bilder(mappe: str, bilder: list[int], fmt: str) -> list[int]:
    return [nr for nr in bilder if os.path.exists(bildefil(mappe, nr, fmt))]


def blender_kommando(blender: list[str], app_dir: str, mappe: str, fmt: str, bilder: list[int]) -> list[str]:
    kommando = [
        *blender, "-b", f"{mappe}/kilde/scene.blend",
        "--factory-startup", "--disable-autoexec",
        "--python-exit-code", "2", "--python", os.path.join(app_dir, "forbered.py").replace("\\", "/"),
        "-o", f"{mappe}/bilder/bilde_####", "-F", fmt, "-x", "1",
    ]
    if len(bilder) == 1:
        return kommando + ["-f", str(bilder[0])]
    steg = bilder[1] - bilder[0]
    return kommando + ["-s", str(bilder[0]), "-e", str(bilder[-1]), "-j", str(steg), "-a"]


def tolk_linje(linje: str) -> tuple[str, str | int] | None:
    """('bilde', nr) for Fra-linjer, ('lagret', sti) for Saved, ('feil'|'motor'|'gpu', tekst)."""
    if m := _LAGRET.search(linje):
        return "lagret", m.group(1)
    if m := _FRA.search(linje):
        return "bilde", int(m.group(1))
    for prefiks, art in (("RENDER-FEIL:", "feil"), ("RENDER-MOTOR:", "motor"), ("RENDER-GPU:", "gpu")):
        if linje.startswith(prefiks):
            return art, linje[len(prefiks):].strip()
    return None


def tid(t: float | None) -> str | None:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else None


def varighet(sek: float) -> str:
    sek = int(round(sek))
    if sek < 60:
        return f"{sek} s"
    if sek < 3600:
        return f"{sek // 60} min"
    return f"{sek // 3600} t {sek % 3600 // 60} min"


def skriv_jobb_json(jobb: dict) -> None:
    """jobb.json i jobbmappen: innstillinger og status. Leses også av forbered.py."""
    mappe = jobb.get("mappe")
    if not mappe or not os.path.isdir(mappe):
        return
    ut = {k: v for k, v in jobb.items() if k != "nr"}
    for felt in ("opprettet", "startet", "ferdig"):
        ut[felt] = tid(jobb.get(felt))
    ut["bilder"] = {"første": jobb["bilder"][0], "siste": jobb["bilder"][-1], "antall": len(jobb["bilder"])} if jobb.get("bilder") else None
    tmp = os.path.join(mappe, "jobb.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ut, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(mappe, "jobb.json"))


def siste_linjer(fil: str, antall: int = 30) -> list[str]:
    try:
        with open(fil, encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f.readlines()[-antall:]]
    except OSError:
        return []
