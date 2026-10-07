# Endringslogg

## 0.1.1 – 2026-10-07

- Kontaktarket har ikke lenger svart felt øverst og kuttet nederste rad. Miniatyrene beholdt en forskyvning etter `--fit` som `--mosaic` tok med.
- Kontaktarket er et rutenett også med færre enn 9 bilder: 2 × 2 for 2–4 bilder og 3 × 3 for 5–9.
- README: `claude mcp add` bruker `--scope user`, så serveren er tilgjengelig i alle prosjekter.
- SPEC.md bruker navnene `koen` og `opplosning_prosent`, som serveren.
- GitHub Actions: `docker/setup-buildx-action` v4 og `docker/build-push-action` v7, som kjører på Node 24.

## 0.1.0 – 2026-10-05

Første versjon etter SPEC.md.

- MCP-server over Streamable HTTP på `/mcp` med Bearer-nøkkel.
- Verktøyene `send_render`, `status`, `resultat`, `koen`, `avbryt` og `helse`.
- Kø i SQLite med `haster`, og gjenopptak etter omstart fra siste ferdige bilde.
- Selvstendig kopi ved innsending: Windows-stier oversettes, filer pakkes inn, jobber med manglende filer avvises.
- GPU med OPTIX og CUDA som reserve; feiler uten GPU med mindre `ALLOW_CPU=true`.
- Forhåndsvisning, kontaktark og video; frigjøring av grafikkminne i Ollama; varsel med ntfy.
- Blender 5.2.2 LTS, låst med sjekksum.
- Unraid-mal og GitHub Actions som tester og publiserer imaget til ghcr.io.

Avvik fra SPEC.md: `køen` heter `koen` og `oppløsning_prosent` heter `opplosning_prosent`, fordi Claude ikke godtar æøå i verktøy- og parameternavn. `video` er av/på (true/false).
