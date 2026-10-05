"""Lager en liten testscene for akseptansetestene.

Kjøres med Blender:

    blender -b --factory-startup --python tests/lag_testscene.py -- <mappe>

Resultat i <mappe>:
    scene.blend              kube med tekstur, kamera, lys, bilde 1–24
    teksturer/rutenett.png   teksturen, lagret som ekstern fil (ikke pakket)
"""

import math
import os
import sys

import bpy


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if not args:
        raise SystemExit("Bruk: blender -b --factory-startup --python lag_testscene.py -- <mappe>")
    mappe = os.path.abspath(args[0])
    os.makedirs(os.path.join(mappe, "teksturer"), exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.frame_start = 1
    scene.frame_end = 24
    scene.render.fps = 24

    # Tekstur som ekstern fil, slik at innpakkingen ved innsending blir testet.
    tekstur_sti = os.path.join(mappe, "teksturer", "rutenett.png")
    bilde = bpy.data.images.new("rutenett", 256, 256)
    bilde.generated_type = "COLOR_GRID"
    bilde.filepath_raw = tekstur_sti
    bilde.file_format = "PNG"
    bilde.save()
    bpy.data.images.remove(bilde)
    bilde = bpy.data.images.load(tekstur_sti)

    bpy.ops.mesh.primitive_cube_add(size=2)
    kube = bpy.context.active_object
    materiale = bpy.data.materials.new("rutenett")
    materiale.use_nodes = True
    noder = materiale.node_tree.nodes
    bsdf = next(n for n in noder if n.type == "BSDF_PRINCIPLED")
    tekstur = noder.new("ShaderNodeTexImage")
    tekstur.image = bilde
    materiale.node_tree.links.new(tekstur.outputs["Color"], bsdf.inputs["Base Color"])
    kube.data.materials.append(materiale)

    # Kuben roterer, slik at bildene i en animasjon er forskjellige.
    kube.rotation_euler = (0, 0, 0)
    kube.keyframe_insert("rotation_euler", frame=1)
    kube.rotation_euler = (0, 0, math.radians(90))
    kube.keyframe_insert("rotation_euler", frame=24)

    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -1))

    bpy.ops.object.light_add(type="SUN", rotation=(math.radians(40), 0, math.radians(30)))
    bpy.context.active_object.data.energy = 3

    bpy.ops.object.camera_add(location=(6, -6, 4))
    kamera = bpy.context.active_object
    kamera.rotation_euler = (math.radians(65), 0, math.radians(45))
    scene.camera = kamera

    verden = bpy.data.worlds.new("verden")
    verden.color = (0.05, 0.05, 0.08)
    scene.world = verden

    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(mappe, "scene.blend"), relative_remap=True)
    print(f"Testscene lagret i {mappe}")


main()
