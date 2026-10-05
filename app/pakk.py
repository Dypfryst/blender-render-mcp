"""Lager en selvstendig kopi av en .blend-fil ved innsending. Kjøres inne i Blender:

    blender -b <original.blend> --factory-startup --disable-autoexec --python pakk.py

Leser oppsettet fra filen i miljøvariabelen PAKK_OPPSETT (JSON):
    data_dir, win_prefix, unc_prefix, maal (kilde/scene.blend), rapport (JSON-fil som skrives)

1. Oversetter Windows-stier i bilder, lyd, fonter, filmklipp, volumer, cache og
   koblede biblioteker til stier under data_dir.
2. Avviser jobben (exit 1) med liste over filer som mangler.
3. Pakker inn eksterne filer og lagrer kopien med relative stier.

Rapporten inneholder info om scenen (bildeområde, fps, motor) og advarsler.
"""

import json
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stier import UgyldigSti, oversett_blender_sti  # noqa: E402


def _normaliser(sti: str) -> str:
    return os.path.normpath(sti).replace("\\", "/")


class Pakker:
    def __init__(self, oppsett: dict):
        self.data_dir = oppsett["data_dir"].rstrip("/")
        self.win_prefix = oppsett["win_prefix"]
        self.unc_prefix = oppsett["unc_prefix"]
        self.blend_mappe = os.path.dirname(_normaliser(bpy.data.filepath))
        self.mangler: list[str] = []
        self.advarsler: list[str] = []

    def oversett(self, sti: str, hva: str) -> str | None:
        """Absolutt sti under data_dir, eller None (og registrert som mangler)."""
        try:
            ny = _normaliser(oversett_blender_sti(sti, self.blend_mappe, self.data_dir, self.win_prefix, self.unc_prefix))
        except UgyldigSti as feil:
            self.mangler.append(f"{hva}: {sti} ({feil})")
            return None
        if ny != self.data_dir and not ny.startswith(self.data_dir + "/"):
            self.mangler.append(f"{hva}: {sti} ligger utenfor den delte mappen")
            return None
        return ny

    def sjekk_finnes(self, sti: str, hva: str, original: str) -> bool:
        if os.path.exists(sti):
            return True
        self.mangler.append(f"{hva}: {original} (fant ikke {sti})")
        return False

    def bilder(self) -> None:
        for bilde in bpy.data.images:
            if bilde.packed_file or bilde.source not in ("FILE", "SEQUENCE", "MOVIE", "TILED") or not bilde.filepath:
                continue
            if bilde.library:
                continue  # hører til et koblet bibliotek
            hva = f"Bilde «{bilde.name}»"
            ny = self.oversett(bilde.filepath, hva)
            if ny is None:
                continue
            if bilde.source == "TILED":
                fliser = [ny.replace("<UDIM>", str(f.number)) for f in bilde.tiles]
                if not any(os.path.exists(f) for f in fliser):
                    self.mangler.append(f"{hva}: {bilde.filepath} (fant ingen UDIM-fliser)")
                    continue
            elif not self.sjekk_finnes(ny, hva, bilde.filepath):
                continue
            bilde.filepath = ny
            if bilde.source in ("SEQUENCE", "MOVIE"):
                self.advarsler.append(
                    f"{hva} er en bildesekvens eller film og kan ikke pakkes inn. Den leses fra {ny} under renderingen."
                )

    def enkle(self, samling, navn: str, kan_pakkes: bool) -> None:
        for blokk in samling:
            sti = getattr(blokk, "filepath", "")
            if not sti or sti == "<builtin>" or getattr(blokk, "packed_file", None) or blokk.library:
                continue
            hva = f"{navn} «{blokk.name}»"
            ny = self.oversett(sti, hva)
            if ny is None or not self.sjekk_finnes(ny, hva, sti):
                continue
            blokk.filepath = ny
            if not kan_pakkes:
                self.advarsler.append(f"{hva} kan ikke pakkes inn og leses fra {ny} under renderingen.")

    def biblioteker(self) -> None:
        for bib in bpy.data.libraries:
            hva = f"Koblet bibliotek «{bib.name}»"
            ny = self.oversett(bib.filepath, hva)
            if ny is None or not self.sjekk_finnes(ny, hva, bib.filepath):
                continue
            bib.filepath = ny
            self.advarsler.append(
                f"{hva} blir ikke pakket inn. Endringer i {ny} før renderingen er ferdig kommer med, "
                "og stier inne i biblioteket blir ikke oversatt."
            )

    def drivere(self) -> None:
        funnet: list[str] = []
        for attr in dir(bpy.data):
            samling = getattr(bpy.data, attr, None)
            if not isinstance(samling, bpy.types.bpy_prop_collection):
                continue
            for id_ in samling:
                if not isinstance(id_, bpy.types.ID):
                    break
                kandidater = [id_]
                tre = getattr(id_, "node_tree", None)
                if tre is not None:
                    kandidater.append(tre)
                for k in kandidater:
                    animasjon = getattr(k, "animation_data", None)
                    if animasjon is None:
                        continue
                    for fkurve in animasjon.drivers:
                        driver = fkurve.driver
                        if driver.type == "SCRIPTED" and not driver.is_simple_expression:
                            tekst = f"{k.name}: {fkurve.data_path} = {driver.expression}"
                            if tekst not in funnet:
                                funnet.append(tekst)
        if funnet:
            vis = "; ".join(funnet[:5]) + (f" og {len(funnet) - 5} til" if len(funnet) > 5 else "")
            self.advarsler.append(
                "Filen har drivere med Python-uttrykk som ikke blir evaluert, fordi skript er slått av "
                f"av sikkerhetshensyn: {vis}"
            )


def scene_info() -> dict:
    scene = bpy.context.scene
    r = scene.render
    motor = r.engine
    if motor == "CYCLES":
        samples = scene.cycles.samples
    elif motor.startswith("BLENDER_EEVEE"):
        samples = scene.eevee.taa_render_samples
    else:
        samples = None
    return {
        "scene": scene.name,
        "bilde_start": scene.frame_start,
        "bilde_slutt": scene.frame_end,
        "bilde_steg": scene.frame_step,
        "fps": r.fps / r.fps_base,
        "motor": motor,
        "samples": samples,
        "oppløsning": [r.resolution_x, r.resolution_y, r.resolution_percentage],
        "kamera": scene.camera.name if scene.camera else None,
    }


def main() -> int:
    oppsett = json.load(open(os.environ["PAKK_OPPSETT"], encoding="utf-8"))
    rapport = {"ok": False, "mangler": [], "advarsler": [], "info": {}}
    try:
        p = Pakker(oppsett)
        p.bilder()
        p.enkle(bpy.data.sounds, "Lyd", True)
        p.enkle(bpy.data.fonts, "Font", True)
        p.enkle(bpy.data.movieclips, "Filmklipp", False)
        p.enkle(bpy.data.volumes, "Volum", False)
        p.enkle(bpy.data.cache_files, "Cache-fil", False)
        p.biblioteker()
        p.drivere()
        rapport["info"] = scene_info()
        if bpy.context.scene.camera is None:
            p.mangler.append("Scenen har ikke noe aktivt kamera.")
        rapport["mangler"], rapport["advarsler"] = p.mangler, p.advarsler
        if not p.mangler:
            bpy.ops.file.pack_all()
            os.makedirs(os.path.dirname(oppsett["maal"]), exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=oppsett["maal"], relative_remap=True, copy=True)
            rapport["ok"] = True
    except Exception:
        rapport["mangler"].append("Klarte ikke å lage kopien: " + traceback.format_exc(limit=3))
    with open(oppsett["rapport"], "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2)
    return 0 if rapport["ok"] else 1


sys.exit(main())
