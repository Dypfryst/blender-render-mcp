"""Forhåndsvisning, kontaktark og video. Krever oiiotool og ffmpeg (finnes i imaget og i GitHub Actions)."""

import json
import shutil
import subprocess

import pytest

from app import media

verktoy = pytest.mark.skipif(
    not (shutil.which("oiiotool") and shutil.which("ffmpeg")), reason="krever oiiotool og ffmpeg"
)


def test_maal():
    assert media.maal(1920, 1080, 1024) == (1024, 576)
    assert media.maal(1080, 1920, 1024) == (576, 1024)
    assert media.maal(640, 360, 1024) is None  # små bilder skaleres ikke opp
    assert media.maal(1024, 1024, 1024) is None


def storrelse(fil) -> tuple[int, int]:
    ut = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                         "-of", "json", str(fil)], capture_output=True, text=True, check=True).stdout
    s = json.loads(ut)["streams"][0]
    return s["width"], s["height"]


def lag_bilder(mappe, antall: int, endelse: str = "png", storr: str = "1920x1080"):
    mappe.mkdir(exist_ok=True)
    filer = []
    for nr in range(1, antall + 1):
        fil = mappe / f"bilde_{nr:04d}.{endelse}"
        if endelse == "exr":
            subprocess.run(["oiiotool", "--pattern", "checker", storr, "4", "-d", "half", "-o", str(fil)], check=True)
        else:
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={storr}:rate=1",
                            "-frames:v", "1", str(fil)], check=True)
        filer.append(str(fil))
    return filer


@verktoy
@pytest.mark.parametrize("endelse", ["png", "exr"])
def test_forhandsvisning_maks_1024(tmp_path, endelse):
    [bilde] = lag_bilder(tmp_path / "bilder", 1, endelse)
    ut = tmp_path / "forhandsvisning.jpg"
    media.forhandsvisning(bilde, str(ut))
    assert storrelse(ut) == (1024, 576)
    assert ut.read_bytes()[:2] == b"\xff\xd8"  # JPEG


@verktoy
def test_kontaktark_3x3(tmp_path):
    filer = lag_bilder(tmp_path / "bilder", 12, storr="640x360")
    ut = tmp_path / "kontaktark.jpg"
    media.kontaktark(filer, str(ut))
    b, h = storrelse(ut)
    assert b <= 1024 and h <= 1024
    assert b == 1024 and h < b  # 3 × 3 liggende bilder, ikke fylt ut til kvadrat


def test_velg_jevnt():
    filer = [f"{i}" for i in range(1, 101)]
    valgt = media.velg_jevnt(filer)
    assert len(valgt) == 9 and valgt[0] == "1" and valgt[-1] == "100"
    assert media.velg_jevnt(["a", "b"]) == ["a", "b"]


@verktoy
def test_video(tmp_path):
    lag_bilder(tmp_path / "bilder", 5, storr="321x181")  # oddetall må rettes til partall
    ut = tmp_path / "video.mp4"
    media.video(str(tmp_path / "bilder"), "png", 24, str(ut))
    assert storrelse(ut) == (320, 180)
