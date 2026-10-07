"""Forhåndsvisning, kontaktark og video. Krever oiiotool og ffmpeg (finnes i imaget og i GitHub Actions)."""

import json
import shutil
import subprocess

import pytest

from app import media

pytestmark = pytest.mark.skipif(
    not (shutil.which("oiiotool") and shutil.which("ffmpeg")), reason="krever oiiotool og ffmpeg"
)


def storrelse(fil) -> tuple[int, int]:
    ut = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                         "-of", "json", str(fil)], capture_output=True, text=True, check=True).stdout
    s = json.loads(ut)["streams"][0]
    return s["width"], s["height"]


def piksel(fil, x: int, y: int) -> tuple[int, int, int]:
    b, _ = storrelse(fil)
    rgb = subprocess.run(["ffmpeg", "-v", "error", "-i", str(fil), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    i = (y * b + x) * 3
    return tuple(rgb[i:i + 3])


def lag_bilder(mappe, antall: int, endelse: str = "png", storr: str = "1920x1080", farge: str | None = None):
    mappe.mkdir(exist_ok=True)
    filer = []
    for nr in range(1, antall + 1):
        fil = mappe / f"bilde_{nr:04d}.{endelse}"
        if endelse == "exr":
            subprocess.run(["oiiotool", "--pattern", "checker", storr, "4", "-d", "half", "-o", str(fil)], check=True)
        else:
            kilde = f"color=c={farge}:size={storr}" if farge else f"testsrc=size={storr}:rate=1"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", kilde,
                            "-frames:v", "1", str(fil)], check=True)
        filer.append(str(fil))
    return filer


@pytest.mark.parametrize("endelse", ["png", "exr"])
def test_forhandsvisning_maks_1024(tmp_path, endelse):
    [bilde] = lag_bilder(tmp_path / "bilder", 1, endelse)
    ut = tmp_path / "forhandsvisning.jpg"
    media.forhandsvisning(bilde, str(ut))
    assert storrelse(ut) == (1024, 576)
    assert ut.read_bytes()[:2] == b"\xff\xd8"  # JPEG


def test_kontaktark_3x3(tmp_path):
    filer = lag_bilder(tmp_path / "bilder", 12, storr="640x360")
    ut = tmp_path / "kontaktark.jpg"
    media.kontaktark(filer, str(ut))
    b, h = storrelse(ut)
    assert b <= 1024 and h <= 1024
    assert b > h  # 3 × 3 liggende bilder


@pytest.mark.parametrize("antall", [2, 4, 5, 8])
def test_kontaktark_rutenett_med_faerre_enn_9(tmp_path, antall):
    """Færre enn 9 bilder gir 2 × 2 eller 3 × 3, ikke en tynn stripe."""
    filer = lag_bilder(tmp_path / "bilder", antall, storr="640x360")
    ut = tmp_path / "kontaktark.jpg"
    media.kontaktark(filer, str(ut))
    b, h = storrelse(ut)
    assert b <= 1024 and b / 2 < h < b  # én rad med 16:9-bilder ville vært under halvparten så høy som bred


def test_kontaktark_uten_svarte_felt(tmp_path):
    """Miniatyrene skal ligge rett i rutenettet, uten svart felt øverst eller kuttet nederste rad."""
    filer = lag_bilder(tmp_path / "bilder", 9, storr="640x360", farge="0x4080c0")
    ut = tmp_path / "kontaktark.jpg"
    media.kontaktark(filer, str(ut))
    b, h = storrelse(ut)
    for x, y in [(2, 2), (b - 3, 2), (2, h - 3), (b - 3, h - 3), (b // 2, h // 2)]:
        assert max(piksel(ut, x, y)) > 60, f"svart piksel i ({x}, {y})"


def test_velg_jevnt():
    filer = [f"{i}" for i in range(1, 101)]
    valgt = media.velg_jevnt(filer)
    assert len(valgt) == 9 and valgt[0] == "1" and valgt[-1] == "100"
    assert media.velg_jevnt(["a", "b"]) == ["a", "b"]


def test_video(tmp_path):
    lag_bilder(tmp_path / "bilder", 5, storr="321x181")  # oddetall må rettes til partall
    ut = tmp_path / "video.mp4"
    media.video(str(tmp_path / "bilder"), "png", 24, str(ut))
    assert storrelse(ut) == (320, 180)
