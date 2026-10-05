"""Jobbkøen, lagret i SQLite slik at ingen jobber forsvinner ved omstart.

Tilstander: forbereder → venter → kjører → ferdig | feilet | avbrutt.
Rekkefølge blant ventende: gjenopptatte jobber først, så haster, så eldste først.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from typing import Any, Iterator

TILSTANDER = ("forbereder", "venter", "kjører", "ferdig", "feilet", "avbrutt")
AKTIVE = ("forbereder", "kjører")
AVSLUTTET = ("ferdig", "feilet", "avbrutt")
PRIORITET = {"haster": 0, "normal": 1}

_SKJEMA = """
CREATE TABLE IF NOT EXISTS jobber (
    nr              INTEGER PRIMARY KEY AUTOINCREMENT,
    jobb_id         TEXT UNIQUE,
    navn            TEXT NOT NULL,
    tilstand        TEXT NOT NULL,
    prioritet       INTEGER NOT NULL DEFAULT 1,
    gjenopptatt     INTEGER NOT NULL DEFAULT 0,
    original        TEXT NOT NULL,
    mappe           TEXT,
    innstillinger   TEXT NOT NULL DEFAULT '{}',
    info            TEXT NOT NULL DEFAULT '{}',
    advarsler       TEXT NOT NULL DEFAULT '[]',
    bilder          TEXT NOT NULL DEFAULT '[]',
    bilder_ferdig   INTEGER NOT NULL DEFAULT 0,
    gjeldende_bilde INTEGER,
    sek_per_bilde   REAL,
    opprettet       REAL NOT NULL,
    startet         REAL,
    ferdig          REAL,
    feilmelding     TEXT
);
"""

_JSON_FELT = ("innstillinger", "info", "advarsler", "bilder")
_REKKEFOLGE = "gjenopptatt DESC, prioritet ASC, nr ASC"


def slug(navn: str) -> str:
    """'Produktfilm – blå bil!' → 'produktfilm-bla-bil'."""
    s = navn.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40].strip("-") or "jobb"


class Ko:
    def __init__(self, db_sti: str):
        self.db_sti = db_sti
        self._las = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(db_sti)), exist_ok=True)
        db = sqlite3.connect(db_sti, timeout=30)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(_SKJEMA)
        finally:
            db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._las:
            db = sqlite3.connect(self.db_sti, timeout=30, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            finally:
                db.close()

    @staticmethod
    def _rad(rad: sqlite3.Row | None) -> dict[str, Any] | None:
        if rad is None:
            return None
        d = dict(rad)
        for felt in _JSON_FELT:
            d[felt] = json.loads(d[felt])
        return d

    # --- Mottak og forberedelse -------------------------------------------------

    def ny_jobb(self, navn: str, original: str, innstillinger: dict, prioritet: str, render_dir: str,
                naa: float | None = None) -> dict[str, Any]:
        """Lager jobben i tilstand 'forbereder' med løpenummer og jobbmappe."""
        naa = time.time() if naa is None else naa
        with self._tx() as db:
            nr = db.execute(
                "INSERT INTO jobber (navn, tilstand, prioritet, original, innstillinger, opprettet)"
                " VALUES (?, 'forbereder', ?, ?, ?, ?)",
                (navn, PRIORITET[prioritet], original, json.dumps(innstillinger), naa),
            ).lastrowid
            dato = time.strftime("%Y-%m-%d", time.localtime(naa))
            jobb_id = f"{dato}_{nr:04d}_{slug(navn)}"
            mappe = os.path.join(render_dir, jobb_id).replace("\\", "/")
            db.execute("UPDATE jobber SET jobb_id = ?, mappe = ? WHERE nr = ?", (jobb_id, mappe, nr))
            return self._rad(db.execute("SELECT * FROM jobber WHERE nr = ?", (nr,)).fetchone())

    def klar(self, jobb_id: str, info: dict, bilder: list[int], advarsler: list[str]) -> bool:
        """Forberedelsen er ferdig: jobben legges i køen. Usann hvis den ble avbrutt imens."""
        with self._tx() as db:
            n = db.execute(
                "UPDATE jobber SET tilstand = 'venter', info = ?, bilder = ?, advarsler = ?"
                " WHERE jobb_id = ? AND tilstand = 'forbereder'",
                (json.dumps(info), json.dumps(bilder), json.dumps(advarsler), jobb_id),
            ).rowcount
            return n == 1

    # --- Renderprosessen ---------------------------------------------------------

    def neste(self, naa: float | None = None) -> dict[str, Any] | None:
        """Tar neste ventende jobb og setter den til 'kjører'."""
        naa = time.time() if naa is None else naa
        with self._tx() as db:
            if db.execute("SELECT 1 FROM jobber WHERE tilstand = 'kjører'").fetchone():
                return None
            rad = db.execute(f"SELECT * FROM jobber WHERE tilstand = 'venter' ORDER BY {_REKKEFOLGE} LIMIT 1").fetchone()
            if rad is None:
                return None
            db.execute(
                "UPDATE jobber SET tilstand = 'kjører', startet = COALESCE(startet, ?), gjenopptatt = 0 WHERE nr = ?",
                (naa, rad["nr"]),
            )
            return self._rad(db.execute("SELECT * FROM jobber WHERE nr = ?", (rad["nr"],)).fetchone())

    def gjenopprett_etter_omstart(self) -> list[str]:
        """Kjørende jobber settes først i køen igjen. Halvferdige forberedelser feiler."""
        with self._tx() as db:
            gjenopptatt = [r["jobb_id"] for r in db.execute("SELECT jobb_id FROM jobber WHERE tilstand = 'kjører'")]
            db.execute("UPDATE jobber SET tilstand = 'venter', gjenopptatt = 1 WHERE tilstand = 'kjører'")
            db.execute(
                "UPDATE jobber SET tilstand = 'feilet', ferdig = ?,"
                " feilmelding = 'Tjenesten startet på nytt under forberedelsen. Send jobben på nytt.'"
                " WHERE tilstand = 'forbereder'",
                (time.time(),),
            )
            return gjenopptatt

    def oppdater(self, jobb_id: str, **felter: Any) -> None:
        if not felter:
            return
        for felt in _JSON_FELT:
            if felt in felter:
                felter[felt] = json.dumps(felter[felt])
        sett = ", ".join(f"{k} = ?" for k in felter)
        with self._tx() as db:
            db.execute(f"UPDATE jobber SET {sett} WHERE jobb_id = ?", (*felter.values(), jobb_id))

    def legg_til_advarsel(self, jobb_id: str, tekst: str) -> None:
        with self._tx() as db:
            rad = db.execute("SELECT advarsler FROM jobber WHERE jobb_id = ?", (jobb_id,)).fetchone()
            if rad is None:
                return
            advarsler = json.loads(rad["advarsler"])
            if tekst not in advarsler:
                advarsler.append(tekst)
            db.execute("UPDATE jobber SET advarsler = ? WHERE jobb_id = ?", (json.dumps(advarsler), jobb_id))

    def avslutt(self, jobb_id: str, tilstand: str, feilmelding: str | None = None, naa: float | None = None) -> bool:
        """Setter sluttilstand, men overskriver ikke en jobb som allerede er avsluttet."""
        assert tilstand in AVSLUTTET
        naa = time.time() if naa is None else naa
        with self._tx() as db:
            n = db.execute(
                "UPDATE jobber SET tilstand = ?, feilmelding = COALESCE(?, feilmelding), ferdig = ?"
                " WHERE jobb_id = ? AND tilstand NOT IN ('ferdig', 'feilet', 'avbrutt')",
                (tilstand, feilmelding, naa, jobb_id),
            ).rowcount
            return n == 1

    # --- Oppslag -----------------------------------------------------------------

    def hent(self, jobb_id: str) -> dict[str, Any] | None:
        with self._tx() as db:
            return self._rad(db.execute("SELECT * FROM jobber WHERE jobb_id = ?", (jobb_id,)).fetchone())

    def ventende(self) -> list[dict[str, Any]]:
        with self._tx() as db:
            return [self._rad(r) for r in db.execute(f"SELECT * FROM jobber WHERE tilstand = 'venter' ORDER BY {_REKKEFOLGE}")]

    def aktive(self) -> list[dict[str, Any]]:
        with self._tx() as db:
            return [self._rad(r) for r in db.execute(
                "SELECT * FROM jobber WHERE tilstand IN ('forbereder', 'kjører') ORDER BY nr")]

    def siste_avsluttede(self, antall: int = 10, maks_dager: float = 90, naa: float | None = None) -> list[dict[str, Any]]:
        naa = time.time() if naa is None else naa
        with self._tx() as db:
            return [self._rad(r) for r in db.execute(
                "SELECT * FROM jobber WHERE tilstand IN ('ferdig', 'feilet', 'avbrutt') AND opprettet >= ?"
                " ORDER BY COALESCE(ferdig, opprettet) DESC LIMIT ?",
                (naa - maks_dager * 86400, antall),
            )]

    def plass(self, jobb_id: str) -> int | None:
        """1 = neste jobb som starter. None hvis jobben ikke venter."""
        for i, jobb in enumerate(self.ventende(), start=1):
            if jobb["jobb_id"] == jobb_id:
                return i
        return None

    def snitt_sek_per_bilde(self, antall: int = 10) -> float | None:
        """Snitt fra de siste ferdige jobbene, brukt til å anslå når ventende jobber starter."""
        with self._tx() as db:
            verdier = [r[0] for r in db.execute(
                "SELECT sek_per_bilde FROM jobber WHERE tilstand = 'ferdig' AND sek_per_bilde IS NOT NULL"
                " ORDER BY ferdig DESC LIMIT ?", (antall,))]
        return sum(verdier) / len(verdier) if verdier else None
