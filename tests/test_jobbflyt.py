"""Hele jobbflyten med en falsk Blender: mottak, kø, rendering, avbryt, gjenopptak og feil."""

import os
import sys
import time

import pytest

from app.config import Config
from app.ko import Ko
from app.renderer import EEVEE_FEIL, Renderprosess
from app.tjeneste import Avvist, Tjeneste

SCENE = r"Z:\prosjekter\test\scene.blend"
FALSK = [sys.executable, os.path.join(os.path.dirname(__file__), "falsk_blender.py")]


@pytest.fixture
def oppsett(tmp_path, monkeypatch):
    monkeypatch.delenv("FALSK_BLENDER", raising=False)
    data = tmp_path / "data"
    (data / "prosjekter" / "test").mkdir(parents=True)
    (data / "prosjekter" / "test" / "scene.blend").write_text("scene versjon 1")
    (data / "prosjekter" / "test" / "mangler.blend").write_text("MANGLER")
    cfg = Config(mcp_token="x" * 32, data_dir=data.as_posix(), config_dir=(tmp_path / "config").as_posix(),
                 min_free_gb=0, max_frame_minutes=1)
    ko = Ko(cfg.ko_db)
    renderer = Renderprosess(cfg, ko, blender=FALSK, nvidia=False)
    tjeneste = Tjeneste(cfg, ko, renderer, blender=FALSK)
    yield cfg, ko, renderer, tjeneste
    renderer.stopp()


def vent_til(tjeneste, jid, tilstander, tidsfrist=20):
    slutt = time.time() + tidsfrist
    while time.time() < slutt:
        s = tjeneste.status(jid)
        if s["tilstand"] in tilstander:
            return s
        time.sleep(0.1)
    raise AssertionError(f"{jid} ble ikke {tilstander}: {tjeneste.status(jid)}")


def test_ett_bilde_fra_windows_sti(oppsett):
    cfg, ko, renderer, tj = oppsett
    svar = tj.send_render(SCENE, navn="Første test", bilder="3")
    assert svar["tilstand"] == "venter" and svar["plass_i_koen"] == 1
    assert svar["jobbmappe"]["windows"].startswith("Z:\\render\\")
    assert svar["jobbmappe"]["windows"].endswith("_0001_forste-test")
    renderer.start()
    s = vent_til(tj, svar["jobb_id"], ("ferdig", "feilet"))
    assert s["tilstand"] == "ferdig", s
    assert s["bilde"] == "1 av 1" and s["prosent"] == 100
    mappe = svar["jobbmappe"]["linux"]
    assert os.path.exists(f"{mappe}/bilder/bilde_0003.png")
    assert os.path.exists(f"{mappe}/kilde/scene.blend")
    assert os.path.exists(f"{mappe}/jobb.json")
    assert "Ferdig etter" in open(f"{mappe}/logg.txt", encoding="utf-8").read()
    res, vedlegg = tj.resultat(svar["jobb_id"])
    assert res["bilder"]["antall_ferdige"] == 1
    assert res["bilder"]["filer"][0]["windows"].endswith("\\bilder\\bilde_0003.png")


def test_kopi_ved_innsending(oppsett):
    cfg, ko, renderer, tj = oppsett
    svar = tj.send_render("/data/prosjekter/test/scene.blend".replace("/data", cfg.data_dir))
    original = f"{cfg.data_dir}/prosjekter/test/scene.blend"
    with open(original, "w") as f:
        f.write("scene versjon 2")
    assert open(f"{svar['jobbmappe']['linux']}/kilde/scene.blend").read() == "scene versjon 1"


def test_manglende_fil_avvises(oppsett):
    cfg, ko, renderer, tj = oppsett
    with pytest.raises(Avvist, match="tre.png"):
        tj.send_render("Z:\\prosjekter\\test\\mangler.blend")
    assert ko.ventende() == []
    assert ko.siste_avsluttede()[0]["tilstand"] == "feilet"


@pytest.mark.parametrize("sti, melding", [
    ("Z:\\prosjekter\\finnes_ikke.blend", "Fant ikke"),
    ("C:\\Users\\hakon\\scene.blend", "delte disken"),
    ("Z:\\prosjekter\\..\\..\\hemmelig.blend", "'..'"),
    ("Z:\\prosjekter\\test\\scene.txt", "ikke en .blend"),
])
def test_ugyldige_stier(oppsett, sti, melding):
    with pytest.raises(Avvist, match=melding):
        oppsett[3].send_render(sti)


def test_ugyldige_innstillinger(oppsett):
    tj = oppsett[3]
    with pytest.raises(Avvist, match="bilderute"):
        tj.send_render(SCENE, bilder="1-x")
    with pytest.raises(Avvist, match="motor"):
        tj.send_render(SCENE, motor="WORKBENCH")


def test_lite_diskplass_avvises(oppsett):
    cfg, ko, renderer, tj = oppsett
    cfg.min_free_gb = 10**9
    with pytest.raises(Avvist, match="MIN_FREE_GB"):
        tj.send_render(SCENE)


def test_to_jobber_og_haster(oppsett, monkeypatch):
    cfg, ko, renderer, tj = oppsett
    monkeypatch.setenv("FALSK_BLENDER", "treg")
    a = tj.send_render(SCENE, navn="a")["jobb_id"]
    renderer.start()
    vent_til(tj, a, ("kjører",))
    b = tj.send_render(SCENE, navn="b")["jobb_id"]
    h = tj.send_render(SCENE, navn="h", prioritet="haster")
    assert h["plass_i_koen"] == 1
    assert tj.status(b)["plass_i_koen"] == 2
    assert tj.status(a)["tilstand"] == "kjører"  # haster avbryter ikke
    assert [j["navn"] for j in tj.koen()["ventende"]] == ["h", "b"]
    vent_til(tj, b, ("ferdig",), 30)
    assert ko.hent(h["jobb_id"])["ferdig"] < ko.hent(b)["ferdig"]
    assert ko.hent(a)["ferdig"] < ko.hent(h["jobb_id"])["ferdig"]
    assert len({ko.hent(j)["mappe"] for j in (a, b, h["jobb_id"])}) == 3


def test_avbryt_kjorende(oppsett, monkeypatch):
    cfg, ko, renderer, tj = oppsett
    monkeypatch.setenv("FALSK_BLENDER", "treg")
    svar = tj.send_render(SCENE)
    renderer.start()
    jid = svar["jobb_id"]
    slutt = time.time() + 10
    while ko.hent(jid)["bilder_ferdig"] < 1 and time.time() < slutt:
        time.sleep(0.05)
    a = tj.avbryt(jid)
    assert a["tilstand"] == "avbrutt"
    assert "Blender er stoppet" in a["melding"]
    time.sleep(1.5)
    assert tj.status(jid)["tilstand"] == "avbrutt"
    bilder = os.listdir(f"{svar['jobbmappe']['linux']}/bilder")
    assert 1 <= len(bilder) < 4  # ferdige bilder er beholdt, resten ble ikke laget
    assert renderer.gjeldende() is None


def test_avbryt_ventende(oppsett):
    tj = oppsett[3]
    jid = tj.send_render(SCENE)["jobb_id"]
    assert "ikke startet" in tj.avbryt(jid)["melding"]
    assert oppsett[1].ventende() == []
    assert "allerede" in tj.avbryt(jid)["melding"]


def test_gjenopptak_hopper_over_ferdige_bilder(oppsett):
    cfg, ko, renderer, tj = oppsett
    svar = tj.send_render(SCENE)
    jid, mappe = svar["jobb_id"], svar["jobbmappe"]["linux"]
    # Som om containeren stoppet midt i: jobben sto som kjører med to ferdige bilder
    ko.neste()
    for nr in (1, 2):
        with open(f"{mappe}/bilder/bilde_{nr:04d}.png", "wb") as f:
            f.write(b"gammelt")
    ko2 = Ko(cfg.ko_db)
    assert ko2.gjenopprett_etter_omstart() == [jid]
    renderer2 = Renderprosess(cfg, ko2, blender=FALSK, nvidia=False)
    tj2 = Tjeneste(cfg, ko2, renderer2, blender=FALSK)
    renderer2.start()
    try:
        assert vent_til(tj2, jid, ("ferdig", "feilet"))["tilstand"] == "ferdig"
    finally:
        renderer2.stopp()
    assert open(f"{mappe}/bilder/bilde_0001.png", "rb").read() == b"gammelt"
    assert open(f"{mappe}/bilder/bilde_0003.png", "rb").read() == b"bilde"
    assert "2 av 4 bilder finnes allerede" in open(f"{mappe}/logg.txt", encoding="utf-8").read()


def test_ingen_gpu_feiler(oppsett, monkeypatch):
    cfg, ko, renderer, tj = oppsett
    monkeypatch.setenv("FALSK_BLENDER", "ingen_gpu")
    jid = tj.send_render(SCENE)["jobb_id"]
    renderer.start()
    s = vent_til(tj, jid, ("ferdig", "feilet"))
    assert s["tilstand"] == "feilet"
    assert "ingen GPU" in s["feilmelding"]
    assert any("RENDER-FEIL" in l for l in s["siste_loggrader"])


def test_eevee_krasj_gir_tydelig_melding(oppsett, monkeypatch):
    cfg, ko, renderer, tj = oppsett
    monkeypatch.setenv("FALSK_BLENDER", "eevee_krasj")
    jid = tj.send_render(SCENE, motor="BLENDER_EEVEE", bilder="1")["jobb_id"]
    renderer.start()
    s = vent_til(tj, jid, ("ferdig", "feilet"))
    assert s["feilmelding"] == EEVEE_FEIL
    assert "CYCLES" in s["feilmelding"]


def test_ingen_nye_bilder_stopper_jobben(oppsett, monkeypatch):
    cfg, ko, renderer, tj = oppsett
    cfg.max_frame_minutes = 1 / 60  # 1 s
    monkeypatch.setenv("FALSK_BLENDER", "henger")
    jid = tj.send_render(SCENE)["jobb_id"]
    renderer.start()
    s = vent_til(tj, jid, ("ferdig", "feilet"))
    assert s["tilstand"] == "feilet"
    assert "MAX_FRAME_MINUTES" in s["feilmelding"]
    assert renderer.gjeldende() is None or renderer.gjeldende() != jid


def test_for_lang_jobb_stoppes(oppsett, monkeypatch):
    cfg, ko, renderer, tj = oppsett
    cfg.max_job_hours = 1 / 3600  # 1 s
    monkeypatch.setenv("FALSK_BLENDER", "henger")
    jid = tj.send_render(SCENE)["jobb_id"]
    renderer.start()
    s = vent_til(tj, jid, ("ferdig", "feilet"))
    assert "MAX_JOB_HOURS" in s["feilmelding"]


def test_helse(oppsett):
    h = oppsett[3].helse()
    assert h["blender"].startswith("Blender 5.2")
    assert h["i_koen"] == 0
    assert h["ledig_plass_data_gb"] > 0
