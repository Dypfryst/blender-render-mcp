"""Logikken bak de seks MCP-verktøyene, uavhengig av MCP slik at den kan testes."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from functools import cached_property

from . import gpu
from .config import Config
from .jobb import (FORMATER, MOTORER, UgyldigJobb, bildefil, bildeliste, ferdige_bilder, siste_linjer,
                   sjekk_bilderute, skriv_jobb_json, tid)
from .ko import AVSLUTTET, Ko
from .renderer import Renderprosess
from .stier import UgyldigSti, begge, sjekk_under_data, til_linux

log = logging.getLogger(__name__)


class Avvist(Exception):
    """Kallet avvises med en melding til Claude."""


class Tjeneste:
    def __init__(self, cfg: Config, ko: Ko, renderer: Renderprosess, blender: list[str] | None = None):
        self.cfg = cfg
        self.ko = ko
        self.renderer = renderer
        self.blender = blender or [cfg.blender_bin]

    def _stier(self, linux: str) -> dict[str, str]:
        return begge(linux, self.cfg.data_dir, self.cfg.win_prefix)

    # --- send_render ---------------------------------------------------------------------

    def send_render(self, blend_fil: str, navn: str = "", motor: str = "fra_fil", bilder: str = "",
                    opplosning_prosent: int = 100, samples: int | None = None, format: str = "PNG",
                    video: bool = False, prioritet: str = "normal") -> dict:
        # 1. Mottak: sjekk innholdet før noe lages
        if motor not in MOTORER:
            raise Avvist(f"Ukjent motor «{motor}». Bruk CYCLES, BLENDER_EEVEE eller fra_fil.")
        if format not in FORMATER:
            raise Avvist(f"Ukjent format «{format}». Bruk PNG, JPEG eller OPEN_EXR.")
        if prioritet not in ("normal", "haster"):
            raise Avvist("Prioritet må være normal eller haster.")
        if not 1 <= int(opplosning_prosent) <= 1000:
            raise Avvist("opplosning_prosent må være mellom 1 og 1000.")
        if samples is not None and int(samples) < 1:
            raise Avvist("samples må være minst 1, eller tom for filens verdi.")
        try:
            sjekk_bilderute(bilder)
            linux = til_linux(blend_fil, self.cfg.data_dir, self.cfg.win_prefix, self.cfg.unc_prefix)
            if not linux.lower().endswith(".blend"):
                raise Avvist(f"{blend_fil} er ikke en .blend-fil.")
            sjekk_under_data(linux, self.cfg.data_dir)
        except (UgyldigSti, UgyldigJobb) as feil:
            raise Avvist(str(feil)) from feil
        if not os.path.isfile(linux):
            raise Avvist(f"Fant ikke {blend_fil} ({linux}). Er filen lagret på den delte disken?")
        ledig = shutil.disk_usage(self.cfg.data_dir).free / 1024**3
        if ledig < self.cfg.min_free_gb:
            raise Avvist(f"Bare {ledig:.0f} GB ledig på {self.cfg.data_dir}, minst {self.cfg.min_free_gb:g} GB kreves (MIN_FREE_GB).")

        innstillinger = {
            "blend_fil": blend_fil, "motor": motor, "bilder": bilder or "", "opplosning_prosent": int(opplosning_prosent),
            "samples": int(samples) if samples else None, "format": format, "video": bool(video), "prioritet": prioritet,
        }
        navn = (navn or os.path.splitext(os.path.basename(linux))[0]).strip()
        jobb = self.ko.ny_jobb(navn, linux, innstillinger, prioritet, self.cfg.render_dir)
        jid, mappe = jobb["jobb_id"], jobb["mappe"]
        os.makedirs(f"{mappe}/kilde", exist_ok=True)
        os.makedirs(f"{mappe}/bilder", exist_ok=True)

        # 2. Forbereder: selvstendig kopi med innpakkede filer
        try:
            rapport = self._pakk(linux, mappe)
        except Exception as feil:  # noqa: BLE001
            rapport = {"ok": False, "mangler": [f"Klarte ikke å lage kopien: {feil}"], "advarsler": [], "info": {}}
        if not rapport["ok"]:
            melding = "Jobben er avvist fordi kopien ikke kunne lages:\n- " + "\n- ".join(rapport["mangler"])
            self.ko.avslutt(jid, "feilet", melding)
            shutil.rmtree(mappe, ignore_errors=True)
            raise Avvist(melding)

        info = rapport["info"]
        advarsler = list(rapport["advarsler"])
        try:
            liste = bildeliste(bilder, info)
        except UgyldigJobb as feil:
            self.ko.avslutt(jid, "feilet", str(feil))
            shutil.rmtree(mappe, ignore_errors=True)
            raise Avvist(str(feil)) from feil
        if video and len(liste) == 1:
            advarsler.append("Video er bestilt, men jobben har bare ett bilde. Ingen video lages.")
        elif video and format == "OPEN_EXR":
            advarsler.append("Video lages bare fra PNG eller JPEG. Velg et av dem hvis du vil ha video.")
        info["motor_i_jobben"] = info.get("motor") if motor == "fra_fil" else motor
        if info["motor_i_jobben"] not in ("CYCLES", "BLENDER_EEVEE"):
            advarsler.append(f"Motoren {info['motor_i_jobben']} er ikke testet. CYCLES anbefales.")

        # 3. Venter
        if not self.ko.klar(jid, info, liste, advarsler):
            raise Avvist(f"Jobben {jid} ble avbrutt mens kopien ble laget.")
        skriv_jobb_json(self.ko.hent(jid))
        self.renderer.ny_jobb()
        plass = self.ko.plass(jid)
        return {
            "jobb_id": jid,
            "tilstand": "venter",
            "plass_i_koen": plass,
            "anslatt_start": self._anslatt_start(jid),
            "jobbmappe": self._stier(mappe),
            "bilder": f"{liste[0]}–{liste[-1]} ({len(liste)} bilder)" if len(liste) > 1 else str(liste[0]),
            "motor": info["motor_i_jobben"],
            "advarsler": advarsler,
            "neste_steg": "Jobben er lagt i køen. Sjekk med status hvert 30.–60. sekund. Ikke send den på nytt.",
        }

    def _pakk(self, linux: str, mappe: str) -> dict:
        oppsett = {
            "data_dir": self.cfg.data_dir, "win_prefix": self.cfg.win_prefix, "unc_prefix": self.cfg.unc_prefix,
            "maal": f"{mappe}/kilde/scene.blend", "rapport": f"{mappe}/kilde/pakk_rapport.json",
        }
        oppsett_fil = f"{mappe}/kilde/pakk_oppsett.json"
        with open(oppsett_fil, "w", encoding="utf-8") as f:
            json.dump(oppsett, f, ensure_ascii=False)
        kommando = [*self.blender, "-b", linux, "--factory-startup", "--disable-autoexec",
                    "--python", os.path.join(self.cfg.app_dir, "pakk.py")]
        with open(f"{mappe}/logg.txt", "a", encoding="utf-8") as logg:
            logg.write(f"[{tid(time.time())}] Lager kopi av {linux}\n")
            logg.flush()
            subprocess.run(kommando, stdout=logg, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=1800,
                           env=dict(os.environ, PAKK_OPPSETT=oppsett_fil, PYTHONIOENCODING="utf-8"))
        try:
            with open(oppsett["rapport"], encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {"ok": False, "mangler": ["Blender klarte ikke å åpne filen. Se logg.txt."], "advarsler": [], "info": {}}
        finally:
            os.remove(oppsett_fil)

    def _anslatt_start(self, jid: str) -> str | None:
        """Anslag basert på jobben som kjører og snittid per bilde fra tidligere jobber."""
        snitt = self.ko.snitt_sek_per_bilde()
        rest = 0.0
        for aktiv in self.ko.aktive():
            if aktiv["tilstand"] != "kjører":
                continue
            spb = aktiv["sek_per_bilde"] or snitt
            if spb is None:
                return None
            rest += spb * (len(aktiv["bilder"]) - aktiv["bilder_ferdig"])
        for ventende in self.ko.ventende():
            if ventende["jobb_id"] == jid:
                return tid(time.time() + rest) if rest else "nå"
            if snitt is None:
                return None
            rest += snitt * len(ventende["bilder"])
        return None

    # --- status ----------------------------------------------------------------------------

    def _hent(self, jobb_id: str) -> dict:
        jobb = self.ko.hent(jobb_id.strip())
        if jobb is None:
            raise Avvist(f"Fant ingen jobb med id «{jobb_id}». Bruk køen for å se jobbene.")
        return jobb

    def status(self, jobb_id: str) -> dict:
        jobb = self._hent(jobb_id)
        totalt = len(jobb["bilder"])
        ferdig = jobb["bilder_ferdig"]
        svar = {
            "jobb_id": jobb["jobb_id"],
            "navn": jobb["navn"],
            "tilstand": jobb["tilstand"],
            "plass_i_koen": self.ko.plass(jobb["jobb_id"]),
            "bilde": f"{ferdig} av {totalt}" if totalt else None,
            "prosent": round(100 * ferdig / totalt, 1) if totalt else 0,
            "rendrer_bilde_nr": jobb["gjeldende_bilde"] if jobb["tilstand"] == "kjører" else None,
            "opprettet": tid(jobb["opprettet"]),
            "startet": tid(jobb["startet"]),
            "anslatt_ferdig": None,
            "ferdig": tid(jobb["ferdig"]),
            "advarsler": jobb["advarsler"],
            "jobbmappe": self._stier(jobb["mappe"]) if jobb["mappe"] else None,
        }
        if jobb["tilstand"] == "venter":
            svar["anslatt_start"] = self._anslatt_start(jobb["jobb_id"])
        if jobb["tilstand"] == "kjører":
            spb = jobb["sek_per_bilde"] or self.ko.snitt_sek_per_bilde()
            if spb:
                svar["anslatt_ferdig"] = tid(time.time() + spb * (totalt - ferdig))
        if jobb["tilstand"] == "feilet":
            svar["feilmelding"] = jobb["feilmelding"]
            svar["siste_loggrader"] = siste_linjer(f"{jobb['mappe']}/logg.txt", 30)
        if jobb["tilstand"] == "ferdig":
            svar["neste_steg"] = "Hent resultatet med resultat og vurder forhåndsvisningen før neste runde."
        return svar

    # --- resultat --------------------------------------------------------------------------

    def resultat(self, jobb_id: str) -> tuple[dict, list[str]]:
        """Svar og liste over bilder (forhåndsvisning, kontaktark) som skal med i svaret."""
        jobb = self._hent(jobb_id)
        mappe = jobb["mappe"]
        if jobb["tilstand"] in ("forbereder", "venter"):
            raise Avvist(f"Jobben {jobb['jobb_id']} har ikke startet ennå ({jobb['tilstand']}).")
        fmt = jobb["innstillinger"]["format"]
        ferdige = ferdige_bilder(mappe, jobb["bilder"], fmt)
        filer = [bildefil(mappe, nr, fmt) for nr in ferdige]
        svar: dict = {
            "jobb_id": jobb["jobb_id"],
            "tilstand": jobb["tilstand"],
            "jobbmappe": self._stier(mappe),
            "bilder": {
                "antall_ferdige": len(filer),
                "antall_bestilt": len(jobb["bilder"]),
                "filer": [self._stier(f) for f in (filer if len(filer) <= 6 else filer[:3] + filer[-3:])],
            },
        }
        if len(filer) > 6:
            svar["bilder"]["merk"] = f"Viser de tre første og tre siste av {len(filer)} bilder."
        if os.path.exists(f"{mappe}/video.mp4"):
            svar["video"] = self._stier(f"{mappe}/video.mp4")
        vedlegg = []
        for navn in ("forhandsvisning.jpg", "kontaktark.jpg"):
            sti = f"{mappe}/{navn}"
            if os.path.exists(sti):
                svar[navn.split(".")[0]] = self._stier(sti)
                vedlegg.append(sti)
        if jobb["tilstand"] == "kjører":
            svar["merk"] = "Jobben kjører fortsatt. Forhåndsvisningen er siste ferdige bilde."
        elif jobb["tilstand"] == "feilet":
            svar["feilmelding"] = jobb["feilmelding"]
        if vedlegg:
            svar["vurdering"] = "Se på forhåndsvisningen og vurder renderen før neste runde."
        return svar, vedlegg

    # --- køen ------------------------------------------------------------------------------

    def koen(self) -> dict:
        def kort(j: dict) -> dict:
            totalt = len(j["bilder"])
            return {
                "jobb_id": j["jobb_id"], "navn": j["navn"], "tilstand": j["tilstand"],
                "prioritet": "haster" if j["prioritet"] == 0 else "normal",
                "bilde": f"{j['bilder_ferdig']} av {totalt}" if totalt else None,
                "opprettet": tid(j["opprettet"]), "ferdig": tid(j["ferdig"]),
            }
        ventende = self.ko.ventende()
        return {
            "aktive": [kort(j) for j in self.ko.aktive()],
            "ventende": [dict(kort(j), plass=i) for i, j in enumerate(ventende, start=1)],
            "siste_ferdige": [kort(j) for j in self.ko.siste_avsluttede(10)],
        }

    # --- avbryt ----------------------------------------------------------------------------

    def avbryt(self, jobb_id: str) -> dict:
        jobb = self._hent(jobb_id)
        jid = jobb["jobb_id"]
        if jobb["tilstand"] in AVSLUTTET:
            return {"jobb_id": jid, "tilstand": jobb["tilstand"], "melding": f"Jobben er allerede {jobb['tilstand']}."}
        self.ko.avslutt(jid, "avbrutt", "Avbrutt etter ønske.")
        stoppet = self.renderer.avbryt(jid)
        for _ in range(30):  # vent til renderprosessen har sluppet jobben, maks 15 s
            if not stoppet or self.renderer.gjeldende() != jid:
                break
            time.sleep(0.5)
        skriv_jobb_json(self.ko.hent(jid))
        ferdige = len(ferdige_bilder(jobb["mappe"], jobb["bilder"], jobb["innstillinger"]["format"])) if jobb["bilder"] else 0
        return {
            "jobb_id": jid,
            "tilstand": "avbrutt",
            "melding": ("Blender er stoppet. " if stoppet else "Jobben var ikke startet. ") + f"{ferdige} ferdige bilder er beholdt.",
            "jobbmappe": self._stier(jobb["mappe"]),
        }

    # --- helse -----------------------------------------------------------------------------

    @cached_property
    def blender_versjon(self) -> str:
        try:
            ut = subprocess.run([*self.blender, "--factory-startup", "--version"], capture_output=True, text=True,
                                timeout=60).stdout
            return next((l.strip() for l in ut.splitlines() if l.startswith("Blender")), "ukjent")
        except (OSError, subprocess.SubprocessError):
            return "ukjent (fant ikke Blender)"

    def helse(self) -> dict:
        disk = shutil.disk_usage(self.cfg.data_dir)
        gpuer = gpu.les_gpuer()
        return {
            "gpu": gpuer or "Fant ingen GPU (nvidia-smi svarer ikke). Sjekk --runtime=nvidia.",
            "blender": self.blender_versjon,
            "i_koen": len(self.ko.ventende()),
            "kjorer": self.renderer.gjeldende(),
            "ledig_plass_data_gb": round(disk.free / 1024**3, 1),
            "min_ledig_plass_gb": self.cfg.min_free_gb,
            "ollama": bool(self.cfg.ollama_url),
            "varsling": bool(self.cfg.ntfy_url),
            "tillat_cpu": self.cfg.allow_cpu,
        }
