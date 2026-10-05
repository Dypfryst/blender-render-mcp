"""Later som om den er Blender, slik at jobbflyten kan testes uten Blender og GPU.

Oppførsel styres med miljøvariabelen FALSK_BLENDER:
    (tom)   rendrer raskt
    treg    0,5 s per bilde
    ingen_gpu  skriver RENDER-FEIL og avslutter med kode 3, slik forbered.py gjør
    eevee_krasj  skriver motor BLENDER_EEVEE og krasjer før første bilde
    henger  begynner på første bilde og blir aldri ferdig
"""

import json
import os
import shutil
import sys
import time

args = sys.argv[1:]
modus = os.environ.get("FALSK_BLENDER", "")

if "--version" in args:
    print("Blender 5.2.2 LTS (falsk)")
    sys.exit(0)

blend = args[args.index("-b") + 1]
skript = args[args.index("--python") + 1]

if skript.endswith("pakk.py"):
    oppsett = json.load(open(os.environ["PAKK_OPPSETT"], encoding="utf-8"))
    innhold = open(blend, encoding="utf-8", errors="replace").read()
    rapport = {"ok": True, "mangler": [], "advarsler": [], "info": {
        "bilde_start": 1, "bilde_slutt": 4, "bilde_steg": 1, "fps": 24.0, "motor": "CYCLES", "samples": 16}}
    if "MANGLER" in innhold:
        rapport.update(ok=False, mangler=["Bilde «tre»: Z:\\teksturer\\tre.png (fant ikke /data/teksturer/tre.png)"])
    else:
        shutil.copy(blend, oppsett["maal"])
    json.dump(rapport, open(oppsett["rapport"], "w", encoding="utf-8"))
    sys.exit(0 if rapport["ok"] else 1)

# Render
if modus == "ingen_gpu":
    print("RENDER-FEIL: Blender fant ingen GPU (OPTIX eller CUDA).", flush=True)
    sys.exit(3)
if modus == "eevee_krasj":
    print("RENDER-MOTOR: BLENDER_EEVEE", flush=True)
    print("Error: Failed to create GPU context (EGL)", flush=True)
    sys.exit(134)

print("RENDER-MOTOR: CYCLES", flush=True)
print("RENDER-GPU: OPTIX: NVIDIA GeForce RTX 3080 (falsk)", flush=True)
monster = args[args.index("-o") + 1]
endelse = {"PNG": "png", "JPEG": "jpg", "OPEN_EXR": "exr"}[args[args.index("-F") + 1]]
if "-f" in args:
    bilder = [int(args[args.index("-f") + 1])]
else:
    s, e = int(args[args.index("-s") + 1]), int(args[args.index("-e") + 1])
    j = int(args[args.index("-j") + 1]) if "-j" in args else 1
    bilder = list(range(s, e + 1, j))

for nr in bilder:
    fil = monster.replace("####", f"{nr:04d}") + "." + endelse
    if os.path.exists(fil):
        continue  # som use_overwrite = False
    print(f"00:01.000  render           | Fra: {nr} | Mem: 1M | Rendering", flush=True)
    if modus == "henger":
        time.sleep(60)
    if modus == "treg":
        time.sleep(0.5)
    with open(fil, "wb") as f:
        f.write(b"bilde")
    print(f"00:01.100  render           | Saved: '{fil}'", flush=True)
    print("00:01.100  render           | Time: 00:00.10 (Saving: 00:00.00)", flush=True)
sys.exit(0)
