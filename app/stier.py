"""Oversettelse mellom Windows-stier på PC-en og Linux-stier i containeren.

Brukes både av serveren og av pakk.py inne i Blender, så modulen bruker bare
standardbiblioteket.
"""

from __future__ import annotations

import os
import posixpath


class UgyldigSti(ValueError):
    """Stien er tom, ligger utenfor den delte mappen eller inneholder '..'."""


def _windows_prefiks(prefiks: str) -> str:
    """'Z:\\' → 'z:/', '\\\\192.168.1.215\\blender' → '//192.168.1.215/blender/'."""
    p = prefiks.replace("\\", "/").lower()
    return p if p.endswith("/") else p + "/"


def til_linux(sti: str, data_dir: str, win_prefix: str, unc_prefix: str) -> str:
    """Gjør en sti fra Claude om til en normalisert sti under data_dir.

    Godtar 'Z:\\prosjekter\\x', '\\\\192.168.1.215\\blender\\prosjekter\\x' og
    '/data/prosjekter/x'. Kaster UgyldigSti hvis stien ikke kan ligge under data_dir.
    Sjekker ikke symbolske lenker; det gjør sjekk_under_data().
    """
    if not sti or not sti.strip():
        raise UgyldigSti("Stien er tom.")
    sti = sti.strip().strip('"').strip("'")
    data_dir = data_dir.rstrip("/") or "/"
    s = sti.replace("\\", "/")
    rest: str | None = None

    for prefiks in (win_prefix, unc_prefix):
        if not prefiks:
            continue
        p = _windows_prefiks(prefiks)
        if s.lower().startswith(p) or s.lower() + "/" == p:
            rest = s[len(p):]
            break

    if rest is None:
        if s.startswith(data_dir + "/") or s == data_dir:
            rest = s[len(data_dir):]
        elif (len(s) >= 2 and s[1] == ":") or sti.startswith("\\\\"):
            raise UgyldigSti(
                f"Windows-stien {sti} ligger ikke under {win_prefix} eller {unc_prefix}. "
                "Filen må ligge på den delte disken."
            )
        else:
            raise UgyldigSti(f"Stien {sti} ligger ikke under {data_dir}.")

    deler = [d for d in rest.split("/") if d not in ("", ".")]
    if ".." in deler:
        raise UgyldigSti(f"Stien {sti} inneholder '..', som ikke er tillatt.")
    return posixpath.join(data_dir, *deler) if deler else data_dir


def til_windows(linux_sti: str, data_dir: str, win_prefix: str) -> str:
    """'/data/render/x/video.mp4' → 'Z:\\render\\x\\video.mp4'."""
    data_dir = data_dir.rstrip("/") or "/"
    if linux_sti != data_dir and not linux_sti.startswith(data_dir + "/"):
        raise UgyldigSti(f"{linux_sti} ligger ikke under {data_dir}.")
    rest = linux_sti[len(data_dir):].strip("/")
    prefiks = win_prefix if win_prefix.endswith("\\") else win_prefix + "\\"
    return prefiks + rest.replace("/", "\\")


def begge(linux_sti: str, data_dir: str, win_prefix: str) -> dict[str, str]:
    return {"windows": til_windows(linux_sti, data_dir, win_prefix), "linux": linux_sti}


def sjekk_under_data(linux_sti: str, data_dir: str) -> str:
    """Følger symbolske lenker og sjekker at målet fortsatt ligger under data_dir."""
    ekte = os.path.realpath(linux_sti)
    rot = os.path.realpath(data_dir)
    if ekte != rot and not ekte.startswith(rot.rstrip(os.sep) + os.sep):
        raise UgyldigSti(f"{linux_sti} peker ut av {data_dir} (symbolsk lenke).")
    return ekte


def oversett_blender_sti(sti: str, blend_mappe: str, data_dir: str, win_prefix: str, unc_prefix: str) -> str:
    """Oversetter en filsti lagret i en .blend-fil til en absolutt Linux-sti.

    Blender-stier kan være relative ('//teksturer/x.png', ofte med '\\' når filen er
    lagret på Windows) eller absolutte Windows- eller Linux-stier.
    """
    if sti.startswith("//"):
        rest = sti[2:].replace("\\", "/")
        return posixpath.normpath(posixpath.join(blend_mappe, rest))
    s = sti.replace("\\", "/")
    if s.startswith("/") and not sti.startswith("\\\\"):
        return posixpath.normpath(s)
    return til_linux(sti, data_dir, win_prefix, unc_prefix)
