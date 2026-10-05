import pytest

from app.jobb import UgyldigJobb, bildeliste, blender_kommando, sjekk_bilderute, tolk_linje, varighet

INFO = {"bilde_start": 10, "bilde_slutt": 20, "bilde_steg": 2}


@pytest.mark.parametrize("tekst, ut", [
    ("", [10, 12, 14, 16, 18, 20]),
    (None, [10, 12, 14, 16, 18, 20]),
    ("5", [5]),
    (" 1 - 3 ", [1, 2, 3]),
    ("7-7", [7]),
])
def test_bildeliste(tekst, ut):
    assert bildeliste(tekst, INFO) == ut


@pytest.mark.parametrize("tekst", ["abc", "1-", "-5", "5-1", "1,2,3", "1-200000", "1.5"])
def test_ugyldig_bilderute(tekst):
    with pytest.raises(UgyldigJobb):
        sjekk_bilderute(tekst)


def test_blender_kommando_animasjon():
    k = blender_kommando(["blender"], "/app", "/data/render/j", "PNG", [1, 2, 3])
    assert k[:3] == ["blender", "-b", "/data/render/j/kilde/scene.blend"]
    assert "--disable-autoexec" in k
    assert k[k.index("--python") + 1] == "/app/forbered.py"
    assert k[k.index("-o") + 1] == "/data/render/j/bilder/bilde_####"
    assert k[-11:] == ["-F", "PNG", "-x", "1", "-s", "1", "-e", "3", "-j", "1", "-a"]
    assert k.index("--python") < k.index("-o") < k.index("-a")


def test_blender_kommando_enkeltbilde():
    k = blender_kommando(["blender"], "/app", "/data/render/j", "OPEN_EXR", [42])
    assert k[-2:] == ["-f", "42"]
    assert "-a" not in k and "-s" not in k
    assert k[k.index("-F") + 1] == "OPEN_EXR"


def test_blender_kommando_steg():
    k = blender_kommando(["blender"], "/app", "/j", "PNG", [10, 12, 14])
    assert k[-7:] == ["-s", "10", "-e", "14", "-j", "2", "-a"]


def test_tolk_linje_fra_blender_5_2():
    # Ekte linjer fra Blender 5.2.2
    assert tolk_linje("00:01.250  render           | Fra: 17 | Mem: 1M | Updating Scene BVH | Building") == ("bilde", 17)
    assert tolk_linje(
        "00:01.329  render           | Saved: '/data/render/j/bilder/bilde_0001.png'"
    ) == ("lagret", "/data/render/j/bilder/bilde_0001.png")
    # Eldre format uten mellomrom
    assert tolk_linje("Fra:3 Mem:14.37M (Peak 14.80M) | Time:00:00.13") == ("bilde", 3)
    assert tolk_linje("RENDER-FEIL: Blender fant ingen GPU") == ("feil", "Blender fant ingen GPU")
    assert tolk_linje("RENDER-MOTOR: BLENDER_EEVEE") == ("motor", "BLENDER_EEVEE")
    assert tolk_linje("00:01.329  render           | Time: 00:00.17 (Saving: 00:00.05)") is None


def test_varighet():
    assert varighet(42) == "42 s"
    assert varighet(12 * 60 + 5) == "12 min"
    assert varighet(3 * 3600 + 15 * 60) == "3 t 15 min"
