"""Renderprosessen: tar én jobb om gangen fra køen og kjører Blender som underprosess."""

from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import threading
import time

from . import gpu, media, varsling
from .config import Config
from .jobb import (FORMATER, bildefil, blender_kommando, ferdige_bilder, skriv_jobb_json, tid, tolk_linje,
                   varighet)
from .ko import Ko
from .stier import til_windows

log = logging.getLogger(__name__)

EEVEE_FEIL = ("EEVEE klarte ikke å starte uten skjerm (EGL). Send jobben på nytt med motor CYCLES. "
              "Sjekk også at containeren har NVIDIA_DRIVER_CAPABILITIES=all.")


class Renderprosess(threading.Thread):
    def __init__(self, cfg: Config, ko: Ko, blender: list[str] | None = None, nvidia: bool = True):
        super().__init__(name="renderprosess", daemon=True)
        self.cfg = cfg
        self.ko = ko
        self.blender = blender or [cfg.blender_bin]  # liste, slik at testene kan bruke en falsk Blender
        self.nvidia = nvidia
        self.vekk = threading.Event()
        self.stopp_alt = threading.Event()
        self._las = threading.Lock()
        self._prosess: subprocess.Popen | None = None
        self._jobb_id: str | None = None
        self._avbryt = False

    # --- Styring fra serveren ----------------------------------------------------------

    def ny_jobb(self) -> None:
        self.vekk.set()

    def avbryt(self, jobb_id: str) -> bool:
        """Stopper Blender hvis jobben kjører nå."""
        with self._las:
            if self._jobb_id != jobb_id:
                return False
            self._avbryt = True
            if self._prosess is not None:
                _drep(self._prosess)
            return True

    def gjeldende(self) -> str | None:
        return self._jobb_id

    def stopp(self) -> None:
        self.stopp_alt.set()
        self.vekk.set()

    def run(self) -> None:
        while not self.stopp_alt.is_set():
            try:
                jobb = self.ko.neste()
            except Exception:  # noqa: BLE001
                log.exception("Klarte ikke å lese køen")
                jobb = None
            if jobb is None:
                self.vekk.wait(5)
                self.vekk.clear()
                continue
            try:
                self.kjor(jobb)
            except Exception as unntak:  # noqa: BLE001
                log.exception("Uventet feil i jobb %s", jobb["jobb_id"])
                self.ko.avslutt(jobb["jobb_id"], "feilet", f"Uventet feil i renderprosessen: {unntak!r}")
            finally:
                with self._las:
                    self._jobb_id, self._prosess, self._avbryt = None, None, False

    # --- Én jobb -----------------------------------------------------------------------

    def kjor(self, jobb: dict) -> None:
        jid, mappe = jobb["jobb_id"], jobb["mappe"]
        fmt = jobb["innstillinger"]["format"]
        bilder = jobb["bilder"]
        with self._las:
            self._jobb_id, self._avbryt = jid, False
        os.makedirs(f"{mappe}/bilder", exist_ok=True)
        loggfil = open(f"{mappe}/logg.txt", "a", encoding="utf-8")

        def skriv(tekst: str) -> None:
            loggfil.write(f"[{tid(time.time())}] {tekst}\n")
            loggfil.flush()

        try:
            skriv(f"Starter jobb {jid}" + (" (gjenopptatt etter omstart)" if jobb["startet"] and jobb["bilder_ferdig"] else ""))
            skriv_jobb_json(self.ko.hent(jid))
            self._frigjor_grafikkminne(jid, skriv)

            ferdige = ferdige_bilder(mappe, bilder, fmt)
            self.ko.oppdater(jid, bilder_ferdig=len(ferdige))
            gjenstaar = [nr for nr in bilder if nr not in set(ferdige)]
            if ferdige:
                skriv(f"{len(ferdige)} av {len(bilder)} bilder finnes allerede og hoppes over.")

            resultat = self._kjor_blender(jobb, gjenstaar, skriv, loggfil) if gjenstaar else {"ok": True}
            self._avslutt(jobb, resultat, skriv)
        finally:
            loggfil.close()

    def _frigjor_grafikkminne(self, jid: str, skriv) -> None:
        if self.cfg.ollama_url:
            try:
                modeller = varsling.frigjor_ollama(self.cfg.ollama_url)
                skriv("Ollama: " + (f"ba {', '.join(modeller)} slippe grafikkminnet" if modeller else "ingen modeller lastet"))
                if modeller:
                    time.sleep(2)
            except Exception as unntak:  # noqa: BLE001
                skriv(f"Ollama: klarte ikke å frigjøre grafikkminne ({unntak})")
        if self.nvidia:
            ledig = gpu.ledig_vram_gb()
            if ledig is not None:
                skriv(f"Ledig grafikkminne før start: {ledig} GB")
                if ledig < self.cfg.min_free_vram_gb:
                    self.ko.legg_til_advarsel(
                        jid, f"Bare {ledig} GB ledig grafikkminne ved start (under {self.cfg.min_free_vram_gb:g} GB). "
                             "Renderingen kan bli treg eller feile hvis scenen er stor.")

    def _kjor_blender(self, jobb: dict, gjenstaar: list[int], skriv, loggfil) -> dict:
        jid, mappe = jobb["jobb_id"], jobb["mappe"]
        fmt = jobb["innstillinger"]["format"]
        bilder = jobb["bilder"]
        # Med use_overwrite=False hopper Blender over ferdige bilder, så hele ruten kan sendes.
        kommando = blender_kommando(self.blender, self.cfg.app_dir, mappe, fmt, bilder)
        skriv("Kommando: " + " ".join(kommando))
        miljo = dict(os.environ, RENDER_JOBB=f"{mappe}/jobb.json", ALLOW_CPU=str(self.cfg.allow_cpu).lower(),
                     PYTHONIOENCODING="utf-8")
        prosess = subprocess.Popen(
            kommando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=miljo,
            start_new_session=(os.name == "posix"),
        )
        with self._las:
            self._prosess = prosess
            if self._avbryt:
                _drep(prosess)

        linjer: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_les, args=(prosess, linjer), daemon=True).start()

        start = time.time()
        siste_bilde_tid = start
        lagret_naa = 0
        motor = jobb["info"].get("motor")
        feilmelding = None
        tidsavbrudd = None
        sist_sjekket = start
        ferdige = set(ferdige_bilder(mappe, bilder, fmt))
        while True:
            try:
                linje = linjer.get(timeout=1)
            except queue.Empty:
                linje = ""
            if linje is None:
                break
            if linje:
                loggfil.write(linje if linje.endswith("\n") else linje + "\n")
                tolket = tolk_linje(linje.strip())
                if tolket:
                    art, verdi = tolket
                    if art == "bilde":
                        self.ko.oppdater(jid, gjeldende_bilde=verdi)
                    elif art == "lagret":
                        lagret_naa += 1
                        siste_bilde_tid = time.time()
                        ferdige = set(ferdige_bilder(mappe, bilder, fmt))
                        self.ko.oppdater(jid, bilder_ferdig=len(ferdige),
                                         sek_per_bilde=(siste_bilde_tid - start) / lagret_naa)
                        self._forhandsvisning(verdi, mappe, skriv)
                    elif art == "feil":
                        feilmelding = verdi
                    elif art == "motor":
                        motor = verdi
                    elif art == "gpu":
                        log.info("Jobb %s: %s", jid, verdi)
            naa = time.time()
            if naa - sist_sjekket > 5:  # avbryt kan komme før renderprosessen kjente jobben
                sist_sjekket = naa
                if not self._avbryt and self.ko.hent(jid)["tilstand"] == "avbrutt":
                    with self._las:
                        self._avbryt = True
                    _drep(prosess)
            if tidsavbrudd is None:
                if naa - start > self.cfg.max_job_hours * 3600:
                    tidsavbrudd = f"Jobben gikk lenger enn MAX_JOB_HOURS ({self.cfg.max_job_hours:g} t) og ble stoppet."
                elif naa - siste_bilde_tid > self.cfg.max_frame_minutes * 60:
                    tidsavbrudd = (f"Ingen nye bilder på {self.cfg.max_frame_minutes:g} min (MAX_FRAME_MINUTES). "
                                   "Jobben ble stoppet.")
                if tidsavbrudd:
                    skriv(tidsavbrudd)
                    _drep(prosess)
        kode = prosess.wait()
        skriv(f"Blender avsluttet med kode {kode}")
        ferdige = set(ferdige_bilder(mappe, bilder, fmt))
        self.ko.oppdater(jid, bilder_ferdig=len(ferdige))

        if self._avbryt:
            return {"ok": False, "avbrutt": True}
        if tidsavbrudd:
            return {"ok": False, "feil": tidsavbrudd}
        if kode == 0 and len(ferdige) == len(bilder):
            return {"ok": True}
        if feilmelding:
            return {"ok": False, "feil": feilmelding}
        if motor and motor.startswith("BLENDER_EEVEE") and lagret_naa == 0:
            return {"ok": False, "feil": EEVEE_FEIL}
        if kode == 0:
            return {"ok": False, "feil": f"Blender avsluttet, men bare {len(ferdige)} av {len(bilder)} bilder finnes."}
        return {"ok": False, "feil": f"Blender stoppet med feilkode {kode}. Se loggen."}

    def _forhandsvisning(self, bilde: str, mappe: str, skriv) -> None:
        try:
            media.forhandsvisning(bilde, f"{mappe}/forhandsvisning.jpg")
        except Exception as unntak:  # noqa: BLE001
            skriv(f"Klarte ikke å lage forhåndsvisning: {unntak}")

    def _avslutt(self, jobb: dict, resultat: dict, skriv) -> None:
        jid, mappe = jobb["jobb_id"], jobb["mappe"]
        inn = jobb["innstillinger"]
        fmt = inn["format"]
        bilder = jobb["bilder"]
        ferdige = ferdige_bilder(mappe, bilder, fmt)
        filer = [bildefil(mappe, nr, fmt) for nr in ferdige]

        if resultat.get("avbrutt"):
            skriv(f"Avbrutt. {len(ferdige)} ferdige bilder er beholdt.")
            self.ko.avslutt(jid, "avbrutt")
            skriv_jobb_json(self.ko.hent(jid))
            return

        if resultat["ok"]:
            if filer and not os.path.exists(f"{mappe}/forhandsvisning.jpg"):
                self._forhandsvisning(filer[-1], mappe, skriv)
            if len(bilder) > 1:
                try:
                    media.kontaktark(filer, f"{mappe}/kontaktark.jpg")
                except Exception as unntak:  # noqa: BLE001
                    skriv(f"Klarte ikke å lage kontaktark: {unntak}")
            if inn.get("video") and len(bilder) > 1 and fmt != "OPEN_EXR":
                try:
                    skriv("Lager video med ffmpeg")
                    media.video(f"{mappe}/bilder", FORMATER[fmt], jobb["info"].get("fps") or 24, f"{mappe}/video.mp4")
                except Exception as unntak:  # noqa: BLE001
                    resultat = {"ok": False, "feil": f"Bildene er ferdige, men videoen feilet: {unntak}"}

        jobb = self.ko.hent(jid)
        brukt = time.time() - (jobb["startet"] or time.time())
        windows = til_windows(mappe, self.cfg.data_dir, self.cfg.win_prefix)
        if resultat["ok"]:
            skriv(f"Ferdig etter {varighet(brukt)}")
            endret = self.ko.avslutt(jid, "ferdig")
            tittel, tekst, feil = f"Render ferdig: {jobb['navn']} ({varighet(brukt)})", windows, False
        else:
            skriv(f"Feilet: {resultat['feil']}")
            endret = self.ko.avslutt(jid, "feilet", resultat["feil"])
            tittel, tekst, feil = f"Render feilet: {jobb['navn']}", f"{resultat['feil']}\n{windows}", True
        skriv_jobb_json(self.ko.hent(jid))
        if endret and self.cfg.ntfy_url:
            varsling.send_ntfy(self.cfg.ntfy_url, tittel, tekst, feil)


def _les(prosess: subprocess.Popen, linjer: queue.Queue) -> None:
    assert prosess.stdout is not None
    for linje in prosess.stdout:
        linjer.put(linje)
    linjer.put(None)


def _drep(prosess: subprocess.Popen) -> None:
    """Stopper Blender og eventuelle underprosesser."""
    if prosess.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(prosess.pid, signal.SIGTERM)
            try:
                prosess.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(prosess.pid, signal.SIGKILL)
        else:
            prosess.kill()
    except ProcessLookupError:
        pass
