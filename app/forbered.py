"""Oppstartsskript for renderingen. Kjøres inne i Blender med --python før renderingen starter.

Leser jobbens innstillinger fra filen i miljøvariabelen RENDER_JOBB (jobb.json).
Slår på GPU-en (OPTIX, ellers CUDA) og legger på motor, samples og oppløsning.
Finnes ingen GPU, avsluttes Blender med kode 3, med mindre ALLOW_CPU=true.
Linjer som starter med 'RENDER-' leses av renderprosessen.
"""

import json
import os
import sys

import bpy

GPU_TYPER = ("OPTIX", "CUDA")


def melding(tekst: str) -> None:
    print(tekst, flush=True)


def feil(tekst: str, kode: int) -> None:
    melding(f"RENDER-FEIL: {tekst}")
    sys.stdout.flush()
    os._exit(kode)  # sys.exit stopper ikke alltid Blender fra å fortsette med renderingen


def slaa_paa_gpu() -> str | None:
    """Returnerer navnet på GPU-ene som brukes, eller None."""
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for typ in GPU_TYPER:
        try:
            prefs.compute_device_type = typ
        except TypeError:
            continue
        prefs.refresh_devices()
        enheter = [d for d in prefs.devices if d.type == typ]
        if not enheter:
            continue
        for d in prefs.devices:
            d.use = d.type == typ
        return f"{typ}: " + ", ".join(d.name for d in enheter)
    return None


def main() -> None:
    jobb = json.load(open(os.environ["RENDER_JOBB"], encoding="utf-8"))
    inn = jobb["innstillinger"]
    tillat_cpu = os.environ.get("ALLOW_CPU", "false").strip().lower() in ("1", "true", "ja", "yes", "on")
    scene = bpy.context.scene
    r = scene.render

    motor = inn.get("motor") or "fra_fil"
    if motor != "fra_fil":
        r.engine = motor
    melding(f"RENDER-MOTOR: {r.engine}")

    r.use_overwrite = False  # ferdige bilder hoppes over ved omstart
    r.use_placeholder = False
    if inn.get("opplosning_prosent"):
        r.resolution_percentage = int(inn["opplosning_prosent"])

    samples = inn.get("samples")
    if r.engine == "CYCLES":
        if samples:
            scene.cycles.samples = int(samples)
        gpu = slaa_paa_gpu()
        if gpu:
            scene.cycles.device = "GPU"
            melding(f"RENDER-GPU: {gpu}")
        elif tillat_cpu:
            scene.cycles.device = "CPU"
            melding("RENDER-GPU: ingen GPU funnet, rendrer på prosessoren fordi ALLOW_CPU=true")
        else:
            feil(
                "Blender fant ingen GPU (OPTIX eller CUDA). Sjekk at containeren har --runtime=nvidia "
                "og riktig NVIDIA_VISIBLE_DEVICES. Sett ALLOW_CPU=true for å tillate rendering på prosessoren.",
                3,
            )
    elif r.engine.startswith("BLENDER_EEVEE"):
        if samples:
            scene.eevee.taa_render_samples = int(samples)
        melding("RENDER-GPU: EEVEE bruker GPU-en via EGL")

    melding(
        f"RENDER-OPPSETT: {r.resolution_x}x{r.resolution_y} @ {r.resolution_percentage}%, "
        f"samples {samples or 'fra filen'}"
    )


try:
    main()
except SystemExit:
    raise
except Exception as unntak:  # noqa: BLE001
    feil(f"Oppstartsskriptet feilet: {unntak!r}", 2)
