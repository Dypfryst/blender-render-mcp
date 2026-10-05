import os

import pytest

from app.stier import UgyldigSti, begge, oversett_blender_sti, sjekk_under_data, til_linux, til_windows

WIN = "Z:\\"
UNC = "\\\\192.168.1.215\\blender"


def lin(sti: str) -> str:
    return til_linux(sti, "/data", WIN, UNC)


@pytest.mark.parametrize(
    "inn, ut",
    [
        ("Z:\\prosjekter\\bil\\scene.blend", "/data/prosjekter/bil/scene.blend"),
        ("z:/prosjekter/bil/scene.blend", "/data/prosjekter/bil/scene.blend"),
        ("Z:\\", "/data"),
        ("\\\\192.168.1.215\\blender\\prosjekter\\bil\\scene.blend", "/data/prosjekter/bil/scene.blend"),
        ("//192.168.1.215/blender/prosjekter/a.blend", "/data/prosjekter/a.blend"),
        ("/data/prosjekter/bil/scene.blend", "/data/prosjekter/bil/scene.blend"),
        ("/data/prosjekter//bil/./scene.blend", "/data/prosjekter/bil/scene.blend"),
        ('  "Z:\\prosjekter\\med mellomrom\\scene.blend"  ', "/data/prosjekter/med mellomrom/scene.blend"),
    ],
)
def test_til_linux(inn, ut):
    assert lin(inn) == ut


@pytest.mark.parametrize(
    "inn",
    [
        "",
        "C:\\Users\\hakon\\scene.blend",
        "\\\\annen-server\\deling\\scene.blend",
        "/etc/passwd",
        "/database/x.blend",
        "/data/../etc/passwd",
        "Z:\\prosjekter\\..\\..\\etc\\passwd",
        "relativ/sti.blend",
    ],
)
def test_til_linux_avviser(inn):
    with pytest.raises(UgyldigSti):
        lin(inn)


def test_til_windows():
    assert til_windows("/data/render/a/video.mp4", "/data", WIN) == "Z:\\render\\a\\video.mp4"
    assert til_windows("/data", "/data", WIN) == "Z:\\"
    assert til_windows("/data/x", "/data", "Y:") == "Y:\\x"
    with pytest.raises(UgyldigSti):
        til_windows("/annet/x", "/data", WIN)


def test_begge():
    assert begge("/data/render/a", "/data", WIN) == {"windows": "Z:\\render\\a", "linux": "/data/render/a"}


def test_tur_retur():
    sti = "/data/prosjekter/æøå/scene.blend"
    assert lin(til_windows(sti, "/data", WIN)) == sti


@pytest.mark.parametrize(
    "inn, ut",
    [
        ("//teksturer/tre.png", "/data/prosjekter/bil/teksturer/tre.png"),
        ("//teksturer\\tre.png", "/data/prosjekter/bil/teksturer/tre.png"),
        ("//..\\felles\\hdri.exr", "/data/prosjekter/felles/hdri.exr"),
        ("Z:\\felles\\hdri.exr", "/data/felles/hdri.exr"),
        ("\\\\192.168.1.215\\blender\\felles\\hdri.exr", "/data/felles/hdri.exr"),
        ("/data/felles/hdri.exr", "/data/felles/hdri.exr"),
    ],
)
def test_oversett_blender_sti(inn, ut):
    assert oversett_blender_sti(inn, "/data/prosjekter/bil", "/data", WIN, UNC) == ut


def test_oversett_blender_sti_utenfor_delingen():
    with pytest.raises(UgyldigSti):
        oversett_blender_sti("C:\\Users\\hakon\\tre.png", "/data/prosjekter/bil", "/data", WIN, UNC)


@pytest.mark.skipif(os.name == "nt", reason="symbolske lenker krever Linux")
def test_symbolsk_lenke_ut_avvises(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    utenfor = tmp_path / "hemmelig"
    utenfor.mkdir()
    (data / "lenke").symlink_to(utenfor)
    with pytest.raises(UgyldigSti):
        sjekk_under_data(str(data / "lenke" / "x.blend"), str(data))
    (data / "ok.blend").write_text("x")
    assert sjekk_under_data(str(data / "ok.blend"), str(data)) == os.path.realpath(data / "ok.blend")
