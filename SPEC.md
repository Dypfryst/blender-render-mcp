# Render-MCP for Blender på Unraid

Spesifikasjon for Claude Code · 5. oktober 2026 · Håkon

## Formål og rammer

Tjenesten lar Claude sende Blender-renderjobber til RTX 3080 i Unraid-serveren og hente resultatet tilbake, uten at noen sitter ved serveren.

- Én Docker-container på Unraid med MCP-server, kø og renderprosess.
- Brukes bare av Claude (Claude Code og Claude Desktop på PC-en) over hjemmenettet. Ingen manuell innsending og ingen nettside.
- Flere chattråder kan sende jobber samtidig. Jobbene legges i kø og rendres én om gangen.
- Ett skjermkort: RTX 3080 med 10 GB minne, delt med Plex, Ollama og andre tjenester.
- Blender i containeren skal ha samme versjon som på PC-en: 5.2 LTS. Nøyaktig patchversjon låses ved bygging.
- Resultatet havner i en delt mappe som PC-en ser som nettverksdisk, og Claude får et forhåndsvisningsbilde i svaret.

## Arkitektur og teknologi

```
Claude på PC-en ──HTTP + nøkkel──▶ MCP-server ──▶ Kø (SQLite) ──▶ Renderprosess ──▶ Blender 5.2 på RTX 3080
                                                                    ├─▶ Ollama: slipper grafikkminnet før hver jobb (valgfritt)
                                                                    ├─▶ ntfy: varsel til mobilen (valgfritt)
                                                                    └─▶ /data = sharen blender = Z: på PC-en
```

Claude snakker bare med MCP-serveren. Alt annet skjer inne i containeren, og filene leses fra Z: på PC-en.

- **Språk:** Python 3.12 med den offisielle MCP-pakken, transport Streamable HTTP.
- **Kø:** SQLite i `/config/ko.db`.
- **Renderprosess:** egen tråd eller prosess som starter Blender som underprosess, én om gangen.
- **Blender:** offisiell Linux-pakke, samme versjon som på PC-en.
- **Verktøy i imaget:** ffmpeg for video og oiiotool for forhåndsvisning av EXR. `nvidia-smi` kommer fra driveren via `--runtime=nvidia`.

## Mapper og stier

Alt ligger i sharen `blender`: `/mnt/user/blender` på Unraid, `/data` i containeren og `Z:\` på PC-en (`\\192.168.1.215\blender`).

```
/data/
  prosjekter/<prosjekt>/scene.blend        ← Claude lagrer her
  prosjekter/<prosjekt>/teksturer/…
  render/2026-10-05_0042_produktfilm/
    kilde/scene.blend                      ← kopi tatt ved innsending, teksturer pakket inn
    bilder/bilde_0001.png …
    video.mp4                              ← når video er bestilt
    forhandsvisning.jpg                    ← siste ferdige bilde, nedskalert
    kontaktark.jpg                         ← 3 × 3 utvalg ved animasjon
    jobb.json                              ← innstillinger og status
    logg.txt
/config/
  ko.db                                    ← køen (SQLite)
```

**Stioversettelse.** Verktøyene tar imot både Windows-stier (`Z:\prosjekter\…`, `\\192.168.1.215\blender\…`) og Linux-stier (`/data/…`). Svarene oppgir alltid begge.

**Kopi ved innsending.** Når en jobb mottas, lages en selvstendig kopi av .blend-filen i jobbens `kilde/`-mappe. Senere endringer i originalen påvirker ikke jobben.

1. Åpne originalen i Blender uten grensesnitt.
2. Oversett alle Windows-stier i bilder, lyd, fonter og koblede biblioteker til `/data/…`.
3. Pakk inn alle eksterne filer (`bpy.ops.file.pack_all()`) og lagre kopien med relative stier.
4. Koblede biblioteker pakkes ikke, men stiene oversettes. Svaret fra `send_render` advarer om dem.
5. Mangler en fil, avvises jobben med en liste over hva som mangler. Ingen render med rosa «mangler»-teksturer.

## MCP-verktøy

Seks verktøy over Streamable HTTP på `/mcp`. `send_render` svarer med en gang. Renderingen skjer i bakgrunnen, og Claude spør om status etterpå.

| Verktøy | Inndata | Svar |
| --- | --- | --- |
| `send_render` | `blend_fil` (påkrevd), `navn` (kort, brukes i mappenavnet), `motor` (CYCLES, BLENDER\_EEVEE eller fra\_fil; standard fra\_fil), `bilder` ("1", "1-250" eller tom = filens område), `oppløsning_prosent` (standard 100), `samples` (tom = filens), `format` (PNG, JPEG eller OPEN\_EXR; standard PNG), `video` (ja/nei; standard nei), `prioritet` (normal eller haster) | `jobb_id`, plass i køen, anslått start, jobbmappe (Windows og Linux), advarsler |
| `status` | `jobb_id` | Tilstand (venter, forbereder, kjører, ferdig, feilet, avbrutt), plass i køen, bilde x av y, prosent, startet, anslått ferdig. Ved feil: de siste 30 loggradene |
| `resultat` | `jobb_id` | Filliste, videofil, forhåndsvisning som bilde i svaret (JPEG, maks 1024 px på lengste side), kontaktark ved animasjon |
| `køen` | – | Aktive og ventende jobber, pluss de 10 siste ferdige, fra alle tråder |
| `avbryt` | `jobb_id` | Bekreftelse. Stopper Blender og beholder ferdige bilder |
| `helse` | – | GPU-navn, brukt og ledig grafikkminne, Blender-versjon, antall i kø, ledig plass på `/data` |

Verktøybeskrivelsene skal si til Claude at

- `send_render` returnerer straks, og at samme jobb ikke skal sendes på nytt mens den ligger i kø,
- status holder å sjekke hvert 30.–60. sekund,
- forhåndsvisningen fra `resultat` skal brukes til å vurdere renderen før neste runde.

## Kø og jobbflyt

Én renderprosess tar én jobb om gangen. Køen lagres i SQLite, så ingen jobber forsvinner ved omstart.

1. **Mottak.** Sjekk at stien ligger under `/data`, at filen finnes, at bilderuten er gyldig og at det er minst `MIN_FREE_GB` ledig. Lag jobbmappe og løpenummer.
2. **Forbereder.** Lag kopien med innpakkede filer (se Mapper og stier). Feiler det, settes jobben til feilet med årsak.
3. **Venter.** Rekkefølgen er eldste først. `haster` går foran `normal`, men avbryter aldri en jobb som kjører.
4. **Kjører.** Renderprosessen plukker neste jobb, frigjør grafikkminne (se Samspill) og starter Blender.
5. **Ferdig eller feilet.** Lag forhåndsvisning, kontaktark og eventuell video. Send varsel.

**Etter omstart.** Jobber som sto som «kjører», settes først i køen igjen. Blender hopper over bilder som allerede finnes, så jobben fortsetter der den slapp.

**Grenser.** En jobb som går lenger enn `MAX_JOB_HOURS` (standard 12), eller som ikke lager et nytt bilde på `MAX_FRAME_MINUTES` (standard 60), stoppes og settes til feilet.

**Opprydding.** Resultater slettes aldri automatisk. Jobber eldre enn 90 dager fjernes bare fra oversikten i `køen`.

## Kjøring av Blender

Blender startes uten grensesnitt med et oppstartsskript som slår på GPU-en og legger på innstillingene fra jobben.

```
blender -b /data/render/<jobb>/kilde/scene.blend --disable-autoexec \
  --python /app/forbered.py \
  -o /data/render/<jobb>/bilder/bilde_#### -F PNG -x 1 \
  -s 1 -e 250 -a
```

Enkeltbilde: `-f <nr>` i stedet for `-s`, `-e` og `-a`.

**`forbered.py`**

- Velg OPTIX, med CUDA som reserve. Slå på alle GPU-er og sett `scene.cycles.device = 'GPU'`.
- Legg på motor, samples og oppløsning fra jobben.
- Sett `render.use_overwrite = False`, slik at ferdige bilder hoppes over ved omstart.
- Skriv GPU-navnet til loggen.
- Finner Blender ingen GPU, skal jobben feile, ikke rendre i stillhet på prosessoren. Unntak: `ALLOW_CPU=true`.

**EEVEE.** Cycles trenger ingen skjerm. EEVEE trenger EGL for å rendre uten skjerm, og dermed `NVIDIA_DRIVER_CAPABILITIES=all` og EGL-bibliotekene i imaget. Klarer ikke EEVEE å starte, skal jobben feile med en tydelig melding om å bruke CYCLES. Ingen automatisk bytte av motor, fordi resultatet ser annerledes ut.

**Fremdrift.** Les Blenders utskrift: linjer med `Saved:` teller ferdige bilder, linjer med `Fra:` gir bildet som rendres. Anslått tid = snittid per ferdig bilde × gjenstående bilder.

**Forhåndsvisning.** Oppdater `forhandsvisning.jpg` etter hvert ferdige bilde. EXR konverteres til JPEG med `oiiotool`. Ved animasjon lages `kontaktark.jpg` til slutt: 9 jevnt fordelte bilder i et 3 × 3-rutenett.

**Video.** Når `video` er bestilt:

```
ffmpeg -framerate <filens fps> -i bilde_%04d.png -c:v libx264 -pix_fmt yuv420p -crf 18 video.mp4
```

## Samspill med andre GPU-tjenester og varsling

Grafikkminnet frigjøres før hver jobb, og et varsel sendes når jobben er ferdig. Begge er valgfrie og slås på med miljøvariabler.

**Ollama.** Når `OLLAMA_URL` er satt, gjør renderprosessen dette før hver jobb:

1. Hent lastede modeller med `GET /api/ps`.
2. Be hver modell slippe minnet med `POST /api/generate` og `{"model": "<navn>", "keep_alive": 0}`.
3. Les ledig grafikkminne med `nvidia-smi`. Under `MIN_FREE_VRAM_GB` (standard 6) gis en advarsel i `status`, men jobben kjører.

Plex-transkoding bruker lite minne og trenger ingen håndtering.

**Varsling.** Når `NTFY_URL` er satt, sendes en melding ved ferdig og feilet jobb, for eksempel «Render ferdig: produktfilm (12 min)», med jobbmappen. MCP kan ikke si fra til Claude av seg selv, så varselet er måten du får vite at noe er klart.

## Sikkerhet

Tjenesten er bare tilgjengelig på hjemmenettet og krever en hemmelig nøkkel på hvert kall.

- Alle forespørsler krever `Authorization: Bearer <MCP_TOKEN>`. Uten nøkkel avvises kallet.
- Porten åpnes ikke mot internett og legges ikke bak Nginx Proxy Manager.
- Alle stier normaliseres og må ligge under `/data`. `..` og symbolske lenker ut av mappen avvises.
- Blender kjøres med `--disable-autoexec`, så skript som ligger inne i .blend-filer ikke kjøres. Bruker filen drivere med Python-uttrykk som ikke er enkle (`driver.is_simple_expression` er usann), skal `send_render` advare om at de ikke blir evaluert.
- Ingen verktøy tar imot fritt Python eller skallkommandoer.
- Containeren kjører som `PUID`/`PGID` 99/100, ikke privilegert.

## Konfigurasjon

Alt styres med miljøvariabler i Unraid-malen. Blender-versjonen settes som byggeargument (`BLENDER_VERSION`).

| Variabel | Standard | Hva den gjør |
| --- | --- | --- |
| `MCP_TOKEN` | påkrevd | Hemmelig nøkkel Claude sender med |
| `PORT` | 8765 | Port for MCP, endepunkt `/mcp` |
| `DATA_DIR` | /data | Den delte mappen |
| `CONFIG_DIR` | /config | Køen og oppsett |
| `WIN_PREFIX` | `Z:\` | Windows-stien som tilsvarer `/data` |
| `UNC_PREFIX` | `\\192.168.1.215\blender` | Alternativ Windows-sti |
| `OLLAMA_URL` | tom | For eksempel `http://192.168.1.215:11434`. Tom = ingen frigjøring |
| `NTFY_URL` | tom | Adresse for varsel. Tom = ingen varsling |
| `MAX_JOB_HOURS` | 12 | Største lengde på én jobb |
| `MAX_FRAME_MINUTES` | 60 | Lengste tid uten nytt bilde |
| `MIN_FREE_GB` | 50 | Minste ledige plass på `/data` før start |
| `MIN_FREE_VRAM_GB` | 6 | Under dette: advarsel i `status` |
| `ALLOW_CPU` | false | Tillat rendering på prosessor hvis GPU mangler |
| `PUID` / `PGID` / `UMASK` | 99 / 100 / 000 | Unraids standardbruker og rettigheter |
| `NVIDIA_VISIBLE_DEVICES` | GPU-UUID | Fra Nvidia-pluginen i Unraid |
| `NVIDIA_DRIVER_CAPABILITIES` | all | Nødvendig for EEVEE uten skjerm |

## Bygg, repo og levering

Koden ligger i et GitHub-repo, og et ferdig image bygges automatisk. Unraid henter bare imaget, så serveren kan settes opp på nytt fra repoet når som helst.

**Repo:** `github.com/Dypfryst/blender-render-mcp`

```
Dockerfile
app/            server, kø, renderprosess, forbered.py, pakk.py
tests/          enhetstester og testscene
unraid/         blender-render-mcp.xml (Unraid-mal)
.github/workflows/build.yml
README.md       installasjon, samme innhold som kapitlet under
CHANGELOG.md
```

**Dockerfile**

- Grunnimage Ubuntu 24.04.
- Blender fra den offisielle Linux-pakken på download.blender.org, låst til `BLENDER_VERSION` og kontrollert mot sjekksum.
- Systempakker: Python 3, ffmpeg, `openimageio-tools` og bibliotekene Blender trenger, inkludert EGL (`libegl1`, `libgl1`, `libxi6`, `libxkbcommon0`, `libsm6`, `libxrender1`, `libxxf86vm1`, `libxfixes3`).
- MCP-serveren bruker den offisielle Python-pakken `mcp`.
- Serveren kjører på systemets Python. Skript i Blender kjører på Blenders egen innebygde Python. De to holdes adskilt.

**Bygg og publisering.** GitHub Actions bygger og publiserer til `ghcr.io/dypfryst/blender-render-mcp` med versjonsnummer og `latest` når en versjon tagges (`v0.1.0`). Imaget kan være offentlig, siden det ikke inneholder hemmeligheter. Er det privat, må Unraid logges inn mot ghcr.io.

**Tester.** Enhetstester for stioversettelse, køens rekkefølge, `haster` og gjenopptak etter omstart. En liten testscene lages av et skript i `tests/` og brukes i akseptansetestene.

## Akseptansetester

Tjenesten er ferdig når alle ti testene er bestått fra Claude Code på PC-en.

| # | Test | Bestått når |
| --- | --- | --- |
| 1 | `helse` | Viser RTX 3080, Blender 5.2 og ledig plass |
| 2 | Cycles, ett bilde av testscenen | `resultat` gir et bilde i svaret, og `nvidia-smi` viser Blender på GPU-en under renderingen |
| 3 | EEVEE, ett bilde av testscenen | Rendres, eller feiler med tydelig melding om å bruke CYCLES |
| 4 | To jobber sendt fra to tråder nesten samtidig | Den andre venter, begge blir ferdige i hver sin mappe |
| 5 | Originalfilen endres etter innsending | Renderen viser versjonen fra innsendingen |
| 6 | Containeren startes på nytt midt i en animasjon | Jobben fortsetter fra siste ferdige bilde |
| 7 | Scene med tekstur på en `Z:\`-sti | Teksturen kommer med i renderen |
| 8 | `avbryt` på en kjørende jobb | Blender stopper, ferdige bilder blir liggende, GPU-en frigjøres |
| 9 | Ferdig jobb med `NTFY_URL` satt | Varsel kommer på mobilen |
| 10 | Kall med feil eller manglende nøkkel | Avvises |

## Installasjon på Unraid

Installasjonen tar under en time når imaget er bygget. Forutsetninger: den nye disken er lagt inn, og Nvidia Driver-pluginen ser 3080 (gjort).

**A. Forbered lagring**

1. **Lag sharen.** Shares → Add Share. Navn `blender`. Primary storage: Cache, Secondary storage: Array, slik at ferdige filer flyttes til arrayet om natta.
2. **Koble PC-en.** Utforsker → Denne PC-en → Koble til nettverksstasjon. Bokstav `Z:`, mappe `\\192.168.1.215\blender`. Bokstaven må være den samme som `WIN_PREFIX`.

**B. Bygg imaget**

3. **Lag repoet.** Opprett `Dypfryst/blender-render-mcp` på GitHub, klon det på PC-en og legg denne filen inn som `SPEC.md` i repoet.
4. **La Claude Code bygge.** Åpne Claude Code i mappen og be den bygge etter `SPEC.md`. Når enhetstestene passerer: push og tag `v0.1.0`. GitHub Actions bygger og publiserer imaget.

**C. Installer containeren**

5. **Finn GPU-ID-en.** Settings → Nvidia Driver. Kopier GPU UUID (starter med `GPU-`).
6. **Legg inn malen.** Kopier `unraid/blender-render-mcp.xml` til `/boot/config/plugins/dockerMan/templates-user/`, for eksempel med Krusader.
7. **Opprett containeren.** Docker → Add Container → velg `blender-render-mcp` under Template. Slå på Advanced View og sjekk feltene:
   - Repository: `ghcr.io/dypfryst/blender-render-mcp:latest`
   - Extra Parameters: `--runtime=nvidia`
   - Port: 8765 → 8765
   - Path: `/data` → `/mnt/user/blender`
   - Path: `/config` → `/mnt/user/appdata/blender-render-mcp`
   - Variabler: `MCP_TOKEN` (lag en lang tilfeldig nøkkel), `NVIDIA_VISIBLE_DEVICES` (UUID-en fra steg 5), resten som i Konfigurasjon
8. **Start og sjekk.** Apply, og åpne loggen fra Docker-oversikten. Den skal vise GPU-navnet og at serveren lytter på port 8765. Slå på autostart.

**D. Koble til Claude**

9. **Claude Code.** I PowerShell på PC-en:

   ```
   claude mcp add --transport http render http://192.168.1.215:8765/mcp --header "Authorization: Bearer <MCP_TOKEN>"
   ```

   Sjekk med `claude mcp add --help` hvis syntaksen har endret seg.
10. **Claude Desktop (valgfritt).** Lokal konfigurasjon i `claude_desktop_config.json` via broen `npx mcp-remote http://192.168.1.215:8765/mcp` med samme nøkkel i en header. Krever Node.js. Ikke testet.
11. **Test.** Be Claude kjøre `helse` på render-serveren, og gå gjennom akseptansetestene.

**Backup og gjenoppretting.** Koden og Unraid-malen ligger i repoet. Resultatene ligger i `blender`-sharen og tas med i vanlig backup. `/config` inneholder bare køen og kan tas med i appdata-backupen. Ved gjenoppretting legges malen tilbake (steg 6), containeren opprettes (steg 7), og tjenesten er oppe igjen.

## Utenfor omfang

Første versjon holdes smal. Verktøyene utad skal ikke endres hvis noe av dette legges til senere.

- Nettside eller manuell innsending.
- Flere maskiner som rendrer, for eksempel 5080 i PC-en. Kan legges til senere med Flamenco bak de samme verktøyene.
- Tilgang fra internett, og dermed fra Claude i nettleser og mobilapp.
- Kobling til Hermes. Hermes kan bruke den samme MCP-serveren senere.
- Automatisk sletting av gamle resultater.
