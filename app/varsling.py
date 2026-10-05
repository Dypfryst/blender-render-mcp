"""Samspill med Ollama (frigjøre grafikkminne) og ntfy (varsel til mobilen)."""

from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)


def _http(url: str, data: bytes | None = None, headers: dict | None = None, tidsfrist: float = 30) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=tidsfrist) as svar:
        return svar.read()


def frigjor_ollama(ollama_url: str) -> list[str]:
    """Ber alle lastede Ollama-modeller slippe grafikkminnet. Returnerer navnene."""
    lastet = json.loads(_http(f"{ollama_url}/api/ps"))
    navn = [m.get("model") or m.get("name") for m in lastet.get("models", [])]
    for modell in navn:
        _http(
            f"{ollama_url}/api/generate",
            json.dumps({"model": modell, "keep_alive": 0}).encode(),
            {"Content-Type": "application/json"},
            tidsfrist=60,
        )
    return navn


def send_ntfy(ntfy_url: str, tittel: str, tekst: str, feil: bool = False) -> None:
    """Sender et varsel. Feil logges, men stopper aldri jobben."""
    try:
        _http(
            ntfy_url,
            tekst.encode("utf-8"),
            {
                # HTTP-hoder må være latin-1; ntfy godtar RFC 2047 for resten.
                "Title": _hode(tittel),
                "Tags": "x" if feil else "white_check_mark",
                "Priority": "high" if feil else "default",
            },
        )
    except Exception as unntak:  # noqa: BLE001
        log.warning("Klarte ikke å sende varsel til ntfy: %s", unntak)


def _hode(tekst: str) -> str:
    try:
        tekst.encode("latin-1")
        return tekst
    except UnicodeEncodeError:
        import base64
        return "=?UTF-8?B?" + base64.b64encode(tekst.encode()).decode() + "?="
