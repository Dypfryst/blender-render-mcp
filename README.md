# Render-MCP for Blender på Unraid

Lar Claude sende Blender-renderjobber til RTX 3080 i Unraid-serveren og hente resultatet tilbake. Én container med MCP-server, kø og renderprosess. Hele spesifikasjonen står i [SPEC.md](SPEC.md).

- Image: `ghcr.io/dypfryst/blender-render-mcp` (bygges av GitHub Actions når en versjon tagges)
- Blender: 5.2.2 LTS, låst med sjekksum i `Dockerfile`
- Verktøy for Claude: `send_render`, `status`, `resultat`, `koen`, `avbryt`, `helse`

> Verktøyet for køen heter `koen` og parameteren `opplosning_prosent` (uten ø), fordi Claude bare godtar a–z, 0–9, `_` og `-` i verktøy- og parameternavn.

## Installasjon på Unraid

Installasjonen tar under en time når imaget er bygget. Forutsetninger: den nye disken er lagt inn, og Nvidia Driver-pluginen ser 3080.

### A. Forbered lagring

1. **Lag sharen.** Shares → Add Share. Navn `blender`. Primary storage: Cache, Secondary storage: Array, slik at ferdige filer flyttes til arrayet om natta.
2. **Koble PC-en.** Utforsker → Denne PC-en → Koble til nettverksstasjon. Bokstav `Z:`, mappe `\\192.168.1.215\blender`. Bokstaven må være den samme som `WIN_PREFIX`.

### B. Bygg imaget

3. **Lag repoet.** Opprett `Dypfryst/blender-render-mcp` på GitHub, klon det på PC-en og legg spesifikasjonen inn som `SPEC.md`.
4. **La Claude Code bygge.** Når enhetstestene passerer: push og tag `v0.1.0`:

   ```
   git push origin main
   git tag v0.1.0
   git push origin v0.1.0
   ```

   GitHub Actions kjører testene, bygger imaget, tester det med ekte Blender og publiserer det til ghcr.io. Følg med under fanen *Actions* på GitHub.

   Første gang: gå til GitHub → profilen din → *Packages* → `blender-render-mcp` → *Package settings* og sett *Visibility* til *Public*. Ellers må Unraid logges inn mot ghcr.io.

### C. Installer containeren

5. **Finn GPU-ID-en.** Settings → Nvidia Driver. Kopier GPU UUID (starter med `GPU-`).
6. **Legg inn malen.** Kopier `unraid/blender-render-mcp.xml` til `/boot/config/plugins/dockerMan/templates-user/`, for eksempel med Krusader.
7. **Opprett containeren.** Docker → Add Container → velg `blender-render-mcp` under Template. Slå på Advanced View og sjekk feltene:
   - Repository: `ghcr.io/dypfryst/blender-render-mcp:latest`
   - Extra Parameters: `--runtime=nvidia`
   - Port: 8765 → 8765
   - Path: `/data` → `/mnt/user/blender`
   - Path: `/config` → `/mnt/user/appdata/blender-render-mcp`
   - Variabler: `MCP_TOKEN` (lag en lang tilfeldig nøkkel), `NVIDIA_VISIBLE_DEVICES` (UUID-en fra steg 5), resten som i Konfigurasjon

   En tilfeldig nøkkel kan lages i PowerShell:

   ```
   -join ((48..57) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char]$_ })
   ```
8. **Start og sjekk.** Apply, og åpne loggen fra Docker-oversikten. Den skal vise GPU-navnet og at serveren lytter på port 8765. Slå på autostart.

### D. Koble til Claude

9. **Claude Code.** I PowerShell på PC-en:

   ```
   claude mcp add --transport http --scope user render http://192.168.1.215:8765/mcp --header "Authorization: Bearer <MCP_TOKEN>"
   ```

   `--scope user` gjør serveren tilgjengelig i alle prosjekter, ikke bare i mappen du står i. Sjekk med `claude mcp add --help` hvis syntaksen har endret seg.
10. **Claude Desktop (valgfritt).** Lokal konfigurasjon i `claude_desktop_config.json` via broen `npx mcp-remote http://192.168.1.215:8765/mcp` med samme nøkkel i en header. Krever Node.js. Ikke testet.
11. **Test.** Be Claude kjøre `helse` på render-serveren, og gå gjennom akseptansetestene i SPEC.md.

   Testscenen lages med Blender på PC-en:

   ```
   & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python tests\lag_testscene.py -- Z:\prosjekter\testscene
   ```

### Backup og gjenoppretting

Koden og Unraid-malen ligger i repoet. Resultatene ligger i `blender`-sharen og tas med i vanlig backup. `/config` inneholder bare køen og Blenders hurtiglager og kan tas med i appdata-backupen. Ved gjenoppretting legges malen tilbake (steg 6), containeren opprettes (steg 7), og tjenesten er oppe igjen.

## Ny Blender-versjon

1. Finn versjonen og sjekksummen på <https://download.blender.org/release/> (fila `blender-<versjon>.sha256`, linjen for `linux-x64.tar.xz`).
2. Endre `BLENDER_VERSION` og `BLENDER_SHA256` øverst i `Dockerfile`.
3. Commit, push og tag en ny versjon (f.eks. `v0.2.0`). Oppdater containeren i Unraid.

## Utvikling

```
uv venv .venv
uv pip install --python .venv -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Testene bruker en falsk Blender (`tests/falsk_blender.py`), så de trenger verken Blender eller GPU. Med ekte Blender:

```
$env:BLENDER_TEST = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
.venv\Scripts\python -m pytest tests\test_ekte_blender.py
```

Testene for forhåndsvisning og video krever `oiiotool` og `ffmpeg` og kjører i GitHub Actions.

## Oppbygging

```
app/config.py      miljøvariabler
app/stier.py       Windows ↔ Linux-stier, sjekk av /data
app/ko.py          køen i SQLite
app/tjeneste.py    logikken bak verktøyene
app/renderer.py    renderprosessen (én jobb om gangen)
app/server.py      MCP-serveren og nøkkelsjekken
app/pakk.py        kjøres i Blender: kopi med innpakkede filer
app/forbered.py    kjøres i Blender: GPU og innstillinger før rendering
app/media.py       forhåndsvisning, kontaktark, video
app/gpu.py         nvidia-smi
app/varsling.py    Ollama og ntfy
```
