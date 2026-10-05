"""MCP over HTTP: nøkkelsjekk og verktøyliste."""

import json
import os
import re
import sys

import pytest
from starlette.testclient import TestClient

from app.config import Config
from app.ko import Ko
from app.renderer import Renderprosess
from app.server import lag_app
from app.tjeneste import Tjeneste

FALSK = [sys.executable, os.path.join(os.path.dirname(__file__), "falsk_blender.py")]
NOKKEL = "en-lang-hemmelig-nokkel-for-test"
HODER = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
         "MCP-Protocol-Version": "2025-06-18"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}


@pytest.fixture
def klient(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    cfg = Config(mcp_token=NOKKEL, data_dir=data.as_posix(), config_dir=(tmp_path / "config").as_posix(), min_free_gb=0)
    ko = Ko(cfg.ko_db)
    tjeneste = Tjeneste(cfg, ko, Renderprosess(cfg, ko, blender=FALSK, nvidia=False), blender=FALSK)
    with TestClient(lag_app(cfg, tjeneste)) as k:
        yield k


@pytest.mark.parametrize("auth", [None, "Bearer feil-nokkel", f"Basic {NOKKEL}", NOKKEL, f"Bearer {NOKKEL}x"])
def test_feil_eller_manglende_nokkel_avvises(klient, auth):
    hoder = dict(HODER, **({"Authorization": auth} if auth else {}))
    svar = klient.post("/mcp", json=INIT, headers=hoder)
    assert svar.status_code == 401
    assert "nøkkel" in svar.json()["feil"]


def test_ogsaa_andre_stier_krever_nokkel(klient):
    assert klient.get("/").status_code == 401
    assert klient.get("/mcp").status_code == 401


def kall(klient, metode, params=None, id_=2):
    svar = klient.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": metode, "params": params or {}},
                       headers=dict(HODER, Authorization=f"Bearer {NOKKEL}"))
    assert svar.status_code == 200, svar.text
    return svar.json()


def test_riktig_nokkel_og_verktoy(klient):
    init = kall(klient, "initialize", INIT["params"], 1)
    assert init["result"]["serverInfo"]["name"] == "blender-render"
    verktoy = {v["name"]: v for v in kall(klient, "tools/list")["result"]["tools"]}
    assert set(verktoy) == {"send_render", "status", "resultat", "koen", "avbryt", "helse"}
    # Claude godtar bare [a-zA-Z0-9_-] i verktøy- og parameternavn
    for navn, v in verktoy.items():
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", navn)
        for param in v["inputSchema"].get("properties", {}):
            assert re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", param), param
    send = verktoy["send_render"]
    assert send["inputSchema"]["required"] == ["blend_fil"]
    assert "ikke send samme jobb på nytt" in send["description"].lower()
    assert "30.–60. sekund" in verktoy["status"]["description"]
    assert "vurdere renderen" in verktoy["resultat"]["description"]


def test_verktoykall_gjennom_mcp(klient):
    helse = kall(klient, "tools/call", {"name": "helse", "arguments": {}})["result"]
    assert not helse.get("isError")
    assert json.loads(helse["content"][0]["text"])["blender"].startswith("Blender 5.2")
    feil = kall(klient, "tools/call", {"name": "status", "arguments": {"jobb_id": "finnes-ikke"}})["result"]
    assert feil["isError"]
    assert "Fant ingen jobb" in feil["content"][0]["text"]
    avvist = kall(klient, "tools/call", {"name": "send_render", "arguments": {"blend_fil": "C:\\hemmelig.blend"}})["result"]
    assert avvist["isError"]


def test_resultat_sender_bildet_i_svaret(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    cfg = Config(mcp_token=NOKKEL, data_dir=data.as_posix(), config_dir=(tmp_path / "config").as_posix(), min_free_gb=0)
    ko = Ko(cfg.ko_db)
    tjeneste = Tjeneste(cfg, ko, Renderprosess(cfg, ko, blender=FALSK, nvidia=False), blender=FALSK)
    jobb = ko.ny_jobb("bilde", "/data/x.blend", {"format": "PNG"}, "normal", cfg.render_dir)
    os.makedirs(f"{jobb['mappe']}/bilder")
    ko.klar(jobb["jobb_id"], {}, [1], [])
    ko.neste()
    ko.avslutt(jobb["jobb_id"], "ferdig")
    open(f"{jobb['mappe']}/bilder/bilde_0001.png", "wb").write(b"png")
    open(f"{jobb['mappe']}/forhandsvisning.jpg", "wb").write(b"\xff\xd8\xff\xe0jpeg")
    with TestClient(lag_app(cfg, tjeneste)) as k:
        res = kall(k, "tools/call", {"name": "resultat", "arguments": {"jobb_id": jobb["jobb_id"]}})["result"]
    assert not res.get("isError")
    tekst, bilde = res["content"]
    assert json.loads(tekst["text"])["bilder"]["antall_ferdige"] == 1
    assert bilde["type"] == "image" and bilde["mimeType"] == "image/jpeg"
