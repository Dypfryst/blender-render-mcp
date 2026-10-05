"""Hele flyten med ekte Blender. Kjøres bare når BLENDER_TEST peker på Blender 5.2:

    BLENDER_TEST="C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" pytest tests/test_ekte_blender.py

Dekker lokalt det som ligner akseptansetest 2, 3, 5 og 7 (uten Unraid og uten nvidia-smi).
"""

import os
import subprocess
import time

import pytest

from app.config import Config
from app.ko import Ko
from app.renderer import Renderprosess
from app.tjeneste import Avvist, Tjeneste

BLENDER = os.environ.get("BLENDER_TEST")
pytestmark = pytest.mark.skipif(not BLENDER, reason="sett BLENDER_TEST til Blender 5.2 for å kjøre")
HER = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def miljo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ekte")
    data = tmp / "data"
    prosjekt = data / "prosjekter" / "test"
    subprocess.run([BLENDER, "-b", "--factory-startup", "--python", os.path.join(HER, "lag_testscene.py"), "--",
                    str(prosjekt)], check=True, capture_output=True)
    # Variant der teksturen ligger på en Z:-sti, slik den gjør når filen er lagret på PC-en
    subprocess.run([BLENDER, "-b", str(prosjekt / "scene.blend"), "--factory-startup", "--python-expr",
                    "import bpy; bpy.data.images['rutenett.png'].filepath = r'Z:\\prosjekter\\test\\teksturer\\rutenett.png';"
                    f"bpy.ops.wm.save_as_mainfile(filepath=r'{prosjekt / 'zsti.blend'}')"],
                   check=True, capture_output=True)
    cfg = Config(mcp_token="x" * 32, data_dir=data.as_posix(), config_dir=(tmp / "config").as_posix(),
                 min_free_gb=0, allow_cpu=True)
    ko = Ko(cfg.ko_db)
    renderer = Renderprosess(cfg, ko, blender=[BLENDER], nvidia=False)
    tjeneste = Tjeneste(cfg, ko, renderer, blender=[BLENDER])
    renderer.start()
    yield tjeneste, prosjekt
    renderer.stopp()


def vent(tj, jid, tidsfrist=180):
    slutt = time.time() + tidsfrist
    while time.time() < slutt:
        s = tj.status(jid)
        if s["tilstand"] in ("ferdig", "feilet", "avbrutt"):
            return s
        time.sleep(0.5)
    raise AssertionError(tj.status(jid))


@pytest.mark.parametrize("motor", ["CYCLES", "BLENDER_EEVEE"])
def test_ett_bilde_med_z_tekstur(miljo, motor):
    tj, prosjekt = miljo
    svar = tj.send_render(r"Z:\prosjekter\test\zsti.blend", navn=f"ekte {motor}", motor=motor, bilder="5",
                          opplosning_prosent=50, samples=4)
    s = vent(tj, svar["jobb_id"])
    assert s["tilstand"] == "ferdig", s
    assert os.path.getsize(f"{svar['jobbmappe']['linux']}/bilder/bilde_0005.png") > 1000


def test_animasjon_med_steg_og_kopi(miljo):
    tj, prosjekt = miljo
    svar = tj.send_render(r"Z:\prosjekter\test\scene.blend", navn="animasjon", motor="CYCLES", bilder="1-3",
                          opplosning_prosent=25, samples=2)
    # Originalen endres etter innsending: skal ikke påvirke jobben
    (prosjekt / "teksturer" / "rutenett.png").write_bytes(b"odelagt")
    s = vent(tj, svar["jobb_id"])
    assert s["tilstand"] == "ferdig", s
    assert s["bilde"] == "3 av 3"
    assert sorted(os.listdir(f"{svar['jobbmappe']['linux']}/bilder")) == [f"bilde_000{i}.png" for i in (1, 2, 3)]


def test_manglende_tekstur_avvises(miljo):
    tj, prosjekt = miljo
    subprocess.run([BLENDER, "-b", str(prosjekt / "scene.blend"), "--factory-startup", "--python-expr",
                    "import bpy; bpy.data.images['rutenett.png'].filepath = r'Z:\\prosjekter\\borte\\x.png';"
                    f"bpy.ops.wm.save_as_mainfile(filepath=r'{prosjekt / 'mangler.blend'}')"],
                   check=True, capture_output=True)
    with pytest.raises(Avvist, match="borte"):
        tj.send_render(r"Z:\prosjekter\test\mangler.blend")
