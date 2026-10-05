import pytest

from app.ko import Ko, slug


@pytest.fixture
def ko(tmp_path):
    return Ko(str(tmp_path / "config" / "ko.db"))


def legg_i_ko(ko: Ko, navn: str, prioritet: str = "normal", naa: float = 1_760_000_000.0) -> str:
    jobb = ko.ny_jobb(navn, f"/data/prosjekter/{navn}.blend", {}, prioritet, "/data/render", naa=naa)
    assert ko.klar(jobb["jobb_id"], {}, [1], [])
    return jobb["jobb_id"]


def ta_neste(ko: Ko) -> str | None:
    jobb = ko.neste()
    return jobb["navn"] if jobb else None


def test_slug():
    assert slug("Produktfilm – blå bil!") == "produktfilm-bla-bil"
    assert slug("Ærlig Øl") == "aerlig-ol"
    assert slug("!!!") == "jobb"
    assert len(slug("x" * 100)) == 40


def test_jobb_id_og_mappe(ko):
    jobb = ko.ny_jobb("Produktfilm", "/data/p/a.blend", {"motor": "CYCLES"}, "normal", "/data/render")
    assert jobb["tilstand"] == "forbereder"
    assert jobb["jobb_id"].endswith("_0001_produktfilm")
    assert jobb["mappe"] == "/data/render/" + jobb["jobb_id"]
    assert jobb["innstillinger"] == {"motor": "CYCLES"}
    neste = ko.ny_jobb("Produktfilm", "/data/p/a.blend", {}, "normal", "/data/render")
    assert neste["jobb_id"] != jobb["jobb_id"]
    assert "_0002_" in neste["jobb_id"]


def test_eldste_forst(ko):
    for navn in ("a", "b", "c"):
        legg_i_ko(ko, navn)
    assert ta_neste(ko) == "a"


def test_bare_en_kjorer_om_gangen(ko):
    a = legg_i_ko(ko, "a")
    legg_i_ko(ko, "b")
    assert ta_neste(ko) == "a"
    assert ko.neste() is None
    ko.avslutt(a, "ferdig")
    assert ta_neste(ko) == "b"


def test_haster_gaar_foran_men_avbryter_ikke(ko):
    a = legg_i_ko(ko, "a")
    legg_i_ko(ko, "b")
    assert ta_neste(ko) == "a"
    legg_i_ko(ko, "haster1", "haster")
    legg_i_ko(ko, "haster2", "haster")
    # a kjører fortsatt; haster venter til den er ferdig
    assert ko.hent(a)["tilstand"] == "kjører"
    assert [j["navn"] for j in ko.ventende()] == ["haster1", "haster2", "b"]
    ko.avslutt(a, "ferdig")
    assert ta_neste(ko) == "haster1"


def test_plass(ko):
    a = legg_i_ko(ko, "a")
    b = legg_i_ko(ko, "b")
    h = legg_i_ko(ko, "h", "haster")
    assert (ko.plass(h), ko.plass(a), ko.plass(b)) == (1, 2, 3)
    ko.neste()
    assert ko.plass(h) is None
    assert ko.plass(a) == 1


def test_forbereder_er_ikke_i_ko(ko):
    jobb = ko.ny_jobb("x", "/data/x.blend", {}, "normal", "/data/render")
    assert ko.neste() is None
    assert ko.plass(jobb["jobb_id"]) is None


def test_gjenopptak_etter_omstart(tmp_path):
    db = str(tmp_path / "ko.db")
    ko = Ko(db)
    a = legg_i_ko(ko, "a")
    h = legg_i_ko(ko, "h", "haster")
    legg_i_ko(ko, "lang")
    assert ta_neste(ko) == "h"
    ko.avslutt(h, "ferdig")
    ko.avslutt(a, "avbrutt")  # a avbrytes mens den venter
    assert ta_neste(ko) == "lang"
    lang = ko.hent(ko.aktive()[0]["jobb_id"])
    halvferdig = ko.ny_jobb("halv", "/data/h.blend", {}, "normal", "/data/render")
    legg_i_ko(ko, "ny_haster", "haster")

    # Containeren startes på nytt: ny Ko mot samme fil
    ko2 = Ko(db)
    assert ko2.gjenopprett_etter_omstart() == [lang["jobb_id"]]
    assert ko2.hent(halvferdig["jobb_id"])["tilstand"] == "feilet"
    # Den avbrutte jobben går foran selv en ny haster-jobb
    assert [j["navn"] for j in ko2.ventende()] == ["lang", "ny_haster"]
    gjenopptatt = ko2.neste()
    assert gjenopptatt["navn"] == "lang"
    assert gjenopptatt["startet"] == lang["startet"]
    assert gjenopptatt["gjenopptatt"] == 0


def test_avslutt_overskriver_ikke_avbrutt(ko):
    a = legg_i_ko(ko, "a")
    ko.neste()
    assert ko.avslutt(a, "avbrutt")
    assert not ko.avslutt(a, "ferdig")
    assert ko.hent(a)["tilstand"] == "avbrutt"


def test_klar_etter_avbrudd_under_forberedelse(ko):
    jobb = ko.ny_jobb("x", "/data/x.blend", {}, "normal", "/data/render")
    ko.avslutt(jobb["jobb_id"], "avbrutt")
    assert not ko.klar(jobb["jobb_id"], {}, [1], [])
    assert ko.hent(jobb["jobb_id"])["tilstand"] == "avbrutt"


def test_siste_avsluttede_skjuler_gamle(ko):
    gammel = legg_i_ko(ko, "gammel", naa=1_000_000.0)
    ko.avslutt(gammel, "ferdig")
    ny = legg_i_ko(ko, "ny")
    ko.avslutt(ny, "feilet", "noe gikk galt")
    navn = [j["navn"] for j in ko.siste_avsluttede(naa=1_760_000_000.0)]
    assert navn == ["ny"]
    assert ko.hent(gammel) is not None  # bare skjult fra oversikten, ikke slettet


def test_advarsler_og_snitt(ko):
    a = legg_i_ko(ko, "a")
    ko.legg_til_advarsel(a, "lite minne")
    ko.legg_til_advarsel(a, "lite minne")
    assert ko.hent(a)["advarsler"] == ["lite minne"]
    assert ko.snitt_sek_per_bilde() is None
    ko.oppdater(a, sek_per_bilde=10.0)
    ko.avslutt(a, "ferdig")
    assert ko.snitt_sek_per_bilde() == 10.0
