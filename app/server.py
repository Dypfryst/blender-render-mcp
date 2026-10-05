"""MCP-serveren: seks verktøy over Streamable HTTP på /mcp, beskyttet med Bearer-nøkkel."""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from typing import Annotated, Literal

import uvicorn
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import gpu
from .config import Config
from .ko import Ko
from .renderer import Renderprosess
from .tjeneste import Avvist, Tjeneste

log = logging.getLogger("render")

INSTRUKSJONER = """Render-server for Blender på RTX 3080 i Unraid-serveren.
Lagre .blend-filen på den delte disken (Z:\\prosjekter\\<prosjekt>\\), send den med send_render,
følg med med status hvert 30.–60. sekund, og hent resultatet med resultat.
Jobber fra alle tråder deler én kø og rendres én om gangen."""


class BearerNokkel:
    """ASGI-mellomvare: alle HTTP-kall må ha 'Authorization: Bearer <MCP_TOKEN>'."""

    def __init__(self, app, nokkel: str):
        self.app = app
        self.forventet = f"Bearer {nokkel}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            hode = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(hode, self.forventet):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body",
                            "body": json.dumps({"feil": "Mangler eller feil nøkkel (Authorization: Bearer <MCP_TOKEN>)."}).encode()})
                return
        await self.app(scope, receive, send)


def lag_mcp(tjeneste: Tjeneste) -> MCPServer:
    mcp = MCPServer("blender-render", instructions=INSTRUKSJONER)

    def kall(funksjon, *args, **kwargs):
        try:
            return funksjon(*args, **kwargs)
        except Avvist as feil:
            raise ToolError(str(feil)) from feil

    @mcp.tool(
        description=(
            "Sender en Blender-renderjobb til køen på render-serveren (RTX 3080). Returnerer straks med jobb_id; "
            "selve renderingen skjer i bakgrunnen. Ikke send samme jobb på nytt mens den ligger i kø eller kjører – "
            "følg den med status i stedet. Filen kopieres ved innsending, så senere endringer i originalen påvirker "
            "ikke jobben. Mangler teksturer eller andre filer, avvises jobben med en liste over hva som mangler."
        ),
        annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False),
    )
    def send_render(
        blend_fil: Annotated[str, Field(description="Sti til .blend-filen, f.eks. Z:\\prosjekter\\bil\\scene.blend eller /data/prosjekter/bil/scene.blend")],
        navn: Annotated[str, Field(description="Kort navn, brukes i mappenavnet. Tom = filnavnet.")] = "",
        motor: Annotated[Literal["CYCLES", "BLENDER_EEVEE", "fra_fil"], Field(description="Rendermotor. fra_fil bruker filens innstilling.")] = "fra_fil",
        bilder: Annotated[str, Field(description="\"1\" for ett bilde, \"1-250\" for en rute, tom for filens område.")] = "",
        opplosning_prosent: Annotated[int, Field(description="Oppløsning i prosent av filens oppløsning.", ge=1, le=1000)] = 100,
        samples: Annotated[int | None, Field(description="Antall samples. Tom = filens verdi.", ge=1)] = None,
        format: Annotated[Literal["PNG", "JPEG", "OPEN_EXR"], Field(description="Filformat for bildene.")] = "PNG",
        video: Annotated[bool, Field(description="Lag video.mp4 av bildene når animasjonen er ferdig (krever PNG eller JPEG).")] = False,
        prioritet: Annotated[Literal["normal", "haster"], Field(description="haster går foran normal i køen, men avbryter aldri en jobb som kjører.")] = "normal",
    ) -> dict:
        return kall(tjeneste.send_render, blend_fil, navn, motor, bilder, opplosning_prosent, samples, format, video, prioritet)

    @mcp.tool(
        description=(
            "Status for en renderjobb: tilstand (venter, forbereder, kjører, ferdig, feilet, avbrutt), plass i køen, "
            "bilde x av y, prosent og anslått ferdig. Ved feil følger de siste 30 loggradene. "
            "Det holder å sjekke hvert 30.–60. sekund."
        ),
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def status(jobb_id: Annotated[str, Field(description="jobb_id fra send_render")]) -> dict:
        return kall(tjeneste.status, jobb_id)

    @mcp.tool(
        description=(
            "Resultatet av en renderjobb: filer (Windows- og Linux-stier), video, og forhåndsvisning som bilde i svaret "
            "(siste ferdige bilde, maks 1024 px), pluss kontaktark med 9 bilder ved animasjon. Bruk forhåndsvisningen "
            "til å vurdere renderen før neste runde."
        ),
        annotations=ToolAnnotations(readOnlyHint=True),
        structured_output=False,
    )
    def resultat(jobb_id: Annotated[str, Field(description="jobb_id fra send_render")]) -> list:
        svar, vedlegg = kall(tjeneste.resultat, jobb_id)
        innhold: list = [json.dumps(svar, ensure_ascii=False, indent=2)]
        for sti in vedlegg:
            with open(sti, "rb") as f:
                innhold.append(Image(data=f.read(), format="jpeg"))
        return innhold

    @mcp.tool(
        name="koen",
        description="Køen: aktive og ventende jobber fra alle tråder, pluss de 10 siste ferdige.",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def koen() -> dict:
        return kall(tjeneste.koen)

    @mcp.tool(
        description="Avbryter en renderjobb. Stopper Blender hvis jobben kjører; ferdige bilder blir liggende.",
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True),
    )
    def avbryt(jobb_id: Annotated[str, Field(description="jobb_id fra send_render")]) -> dict:
        return kall(tjeneste.avbryt, jobb_id)

    @mcp.tool(
        description="Helsesjekk: GPU-navn, brukt og ledig grafikkminne, Blender-versjon, antall i kø og ledig plass på /data.",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def helse() -> dict:
        return kall(tjeneste.helse)

    return mcp


def lag_app(cfg: Config, tjeneste: Tjeneste):
    mcp = lag_mcp(tjeneste)
    # Stateless: ingen økter som forsvinner når containeren startes på nytt.
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    return BearerNokkel(app, cfg.mcp_token)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    cfg = Config.fra_miljo()
    if len(cfg.mcp_token) < 16:
        log.error("MCP_TOKEN mangler eller er kortere enn 16 tegn. Sett en lang tilfeldig nøkkel i Unraid-malen.")
        sys.exit(1)
    for mappe in (cfg.data_dir, cfg.config_dir):
        if not os.path.isdir(mappe):
            log.error("Mappen %s finnes ikke. Sjekk stiene i Unraid-malen.", mappe)
            sys.exit(1)
    os.makedirs(cfg.render_dir, exist_ok=True)

    gpuer = gpu.les_gpuer()
    if gpuer:
        for g in gpuer:
            log.info("GPU: %s, %s GB ledig av %s GB", g["navn"], g["ledig_gb"], g["totalt_gb"])
    else:
        log.warning("Fant ingen GPU med nvidia-smi. Sjekk --runtime=nvidia og NVIDIA_VISIBLE_DEVICES.")

    ko = Ko(cfg.ko_db)
    for jid in ko.gjenopprett_etter_omstart():
        log.info("Gjenopptar jobb %s etter omstart", jid)
    renderer = Renderprosess(cfg, ko)
    tjeneste = Tjeneste(cfg, ko, renderer)
    log.info("Blender: %s", tjeneste.blender_versjon)
    renderer.start()

    log.info("MCP-serveren lytter på port %s, endepunkt /mcp", cfg.port)
    uvicorn.run(lag_app(cfg, tjeneste), host="0.0.0.0", port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
