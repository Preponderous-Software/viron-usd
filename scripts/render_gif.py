"""Render a recorded run to an animated GIF with headless Blender (the README animation).

    python scripts/render_gif.py runs/showcase/run.usda --out docs/images/replay.gif \
        --blender /path/to/blender [--still docs/images/replay.png] [--mp4 replay.mp4]

Two halves in one file:

* **Outside Blender** (any Python with ``usd-core`` and Pillow): reads the stage's time range,
  ``timeCodesPerSecond`` and each entity's ``displayColor``, runs Blender in the background on
  this same file, then stamps a tick counter on every frame and assembles the GIF.
* **Inside Blender** (``blender -b -P render_gif.py -- --inner params.json``): imports the stage
  with **Set Frame Range**, sets the scene frame rate to the stage's ``timeCodesPerSecond``
  (Blender's importer leaves it at 24 fps — docs/PLAYBACK.md § 5), gives each entity a material
  in its recorded colour and each grid a checkerboard, and renders one PNG per tick with
  Cycles on the CPU (no GPU or OpenGL needed).

Blender does not import animated ``visibility`` (docs/PLAYBACK.md § 5), so this suits runs in
which every entity stays placed. A value-clip ``run.usda`` is read directly by Blender here;
``viron-usd flatten`` first if in doubt.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKGROUND = (244, 245, 247)  # flat backdrop the transparent Blender frames are composited onto


# -- inside Blender ---------------------------------------------------------------------------


def _inner(params_path: str) -> None:
    import bpy  # type: ignore[import-not-found]
    from mathutils import Matrix, Vector  # type: ignore[import-not-found]

    params = json.loads(Path(params_path).read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.usd_import(filepath=params["stage"], set_frame_range=True)
    scene = bpy.context.scene
    scene.render.fps = max(1, round(params["fps"]))
    scene.render.fps_base = scene.render.fps / params["fps"]
    # An optional "last" renders only a prefix of the run (handy for checking one frame).
    scene.frame_start, scene.frame_end = params["start"], params.get("last", params["end"])

    def material(name: str, rgb) -> "bpy.types.Material":
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (*rgb, 1.0)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6
        return mat

    colors = params["colors"]  # entity id (str) -> rgb
    order = {e: i for i, e in enumerate(sorted(colors, key=int))}
    light, dark = material("cell_light", (0.62, 0.63, 0.65)), material("cell_dark", (0.45, 0.46, 0.49))
    xs, ys = [], []
    for obj in list(scene.objects):
        if obj.type != "MESH":
            continue
        # Entities import as one object per e_<id> (Xform merged with its cube), carrying a
        # Transform Cache constraint whose object_path names the USD prim.
        names = [obj.name] + [getattr(c, "object_path", "") for c in obj.constraints]
        found = [m.group(1) for n in names for m in [re.search(r"(?:^|/)e_(\d+)(?:$|/|\.)", n)] if m]
        entity = found[0] if found and found[0] in colors else None
        if entity is not None:
            # The cube's geometry is static (only its displayColor is time-sampled, and the
            # material below replaces it), so drop the per-frame mesh cache the importer adds.
            # Fanned-out cubes in one cell overlap with coplanar tops, which Cycles shades as
            # black patches; a per-entity height change of under 2% keeps the faces apart.
            for mod in [m for m in obj.modifiers if m.type == "MESH_SEQUENCE_CACHE"]:
                obj.modifiers.remove(mod)
            obj.data.transform(Matrix.Scale(1.0 + 0.0015 * order[entity], 4, (0.0, 0.0, 1.0)))
            obj.data.materials.clear()
            obj.data.materials.append(material(f"entity_{entity}", colors[entity]))
            continue
        # A grid's cell mesh: checkerboard by face (face index = x * columns + y).
        mesh = obj.data
        mesh.materials.clear()
        mesh.materials.append(light)
        mesh.materials.append(dark)
        columns = params["columns"]  # Viron gives every grid of an environment the same size
        for poly in mesh.polygons:
            x, y = divmod(poly.index, columns)
            poly.material_index = (x + y) % 2
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            xs.append(world.x)
            ys.append(world.y)

    # Camera: perspective, looking down at the grids from the front at a steep angle.
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    width, depth = max(xs) - min(xs), max(ys) - min(ys)
    cam_data = bpy.data.cameras.new("camera")
    cam_data.lens = 70
    cam = bpy.data.objects.new("camera", cam_data)
    scene.collection.objects.link(cam)
    distance = max(width, depth * 1.78) * 2.05
    cam.location = (cx, cy - distance * 0.55, distance * 0.85)
    direction = Vector((cx, cy - depth * 0.04, 0)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    # Cycles on the CPU: it needs no GPU or OpenGL, so it runs on headless machines where
    # Workbench and Eevee cannot (for example under WSL with software GL).
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = params["samples"]
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 3
    sun_data = bpy.data.lights.new("sun", "SUN")
    sun_data.energy = 2.2
    sun_data.angle = 0.2
    sun = bpy.data.objects.new("sun", sun_data)
    sun.rotation_euler = (0.6, 0.25, 0.4)
    scene.collection.objects.link(sun)
    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.92, 0.93, 0.95, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.45
    scene.world = world
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = params["width"], params["height"]
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True  # composited onto a flat background later
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(Path(params["frames"]) / "f_")
    bpy.ops.render.render(animation=True)


# -- outside Blender --------------------------------------------------------------------------


def _read_stage(stage_path: Path) -> dict:
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(stage_path))
    start, end = int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode())
    colors, columns, entities = {}, 1, 0
    for prim in stage.Traverse():
        entity_id = prim.GetAttribute("viron:entityId")
        if entity_id and entity_id.HasAuthoredValue():
            entities += 1
            geom = UsdGeom.Cube(prim.GetChild("geom"))
            value = geom.GetDisplayColorAttr().Get(start) if geom else None
            if value:
                colors[str(entity_id.Get())] = [float(c) for c in value[0]]
        cols = prim.GetAttribute("viron:columns")
        if cols and cols.HasAuthoredValue():
            columns = int(cols.Get())
    return {
        "start": start,
        "end": end,
        "fps": float(stage.GetTimeCodesPerSecond()),
        "colors": colors,
        "columns": columns,
        "entities": entities,
    }


def _stamp(frame_path: Path, tick: int, total: int, scale: int):
    from PIL import Image, ImageDraw, ImageFont

    rgba = Image.open(frame_path).convert("RGBA")
    img = Image.new("RGBA", rgba.size, BACKGROUND + (255,))
    img.alpha_composite(rgba)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)
    size = 15 * scale
    try:
        font = ImageFont.truetype("DejaVuSansMono-Bold.ttf", size)
    except OSError:
        font = ImageFont.load_default(size=size)
    text = f"tick {tick:>3} / {total}"
    pad = 6 * scale
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.rounded_rectangle((pad, pad, pad + w + 2 * pad, pad + h + 2 * pad), radius=4 * scale, fill=(40, 40, 46))
    draw.text((2 * pad - box[0], 2 * pad - box[1]), text, font=font, fill=(245, 245, 245))
    return img


def _shared_palette(frames):
    """One GIF palette for every frame, so the entity colours do not shimmer.

    The scene is mostly grey board, so a plain median cut spends nearly every slot on greys and
    washes the entities out. Saturated pixels (the entities) and the rest are quantised
    separately, from a sample of frames, and the two palettes are joined.
    """
    from PIL import Image

    sample = frames[:: max(1, len(frames) // 12)]
    pixels = [px for im in sample for px in im.resize((im.width // 2, im.height // 2)).getdata()]

    def saturated(px):
        return max(px) - min(px) > 40

    def quantised(group, n):
        if not group:
            return []
        strip = Image.new("RGB", (len(group), 1))
        strip.putdata(group)
        pal = strip.quantize(colors=n, method=Image.Quantize.MEDIANCUT).getpalette()[: 3 * n]
        return pal

    colours = quantised([px for px in pixels if saturated(px)], 96)
    colours += quantised([px for px in pixels if not saturated(px)], 32)
    palette = Image.new("P", (1, 1))
    palette.putpalette(colours[: 3 * 256])
    return palette


def main(argv: list[str] | None = None) -> None:
    if "--inner" in sys.argv:
        _inner(sys.argv[sys.argv.index("--inner") + 1])
        return
    p = argparse.ArgumentParser(description="Render a recorded viron-usd run to an animated GIF.")
    p.add_argument("stage", type=Path)
    p.add_argument("--out", type=Path, required=True, help="output .gif")
    p.add_argument("--blender", default="blender", help="Blender executable (default: blender on PATH)")
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--samples", type=int, default=16, help="Cycles samples per frame (denoised)")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--still", type=Path, help="also save one stamped frame as PNG")
    p.add_argument("--still-tick", type=int, help="tick for --still (default: middle of the run)")
    p.add_argument("--mp4", type=Path, help="also encode an MP4 with ffmpeg (if on PATH or --ffmpeg)")
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--keep-frames", type=Path, help="copy the raw Blender frames here")
    args = p.parse_args(argv)

    info = _read_stage(args.stage)
    with tempfile.TemporaryDirectory() as tmp:
        frames = Path(tmp) / "frames"
        frames.mkdir()
        params = {
            **info,
            "stage": str(args.stage.resolve()),
            "frames": str(frames),
            "width": args.width,
            "height": args.height,
            "samples": args.samples,
        }
        params_path = Path(tmp) / "params.json"
        params_path.write_text(json.dumps(params))
        subprocess.run(
            [args.blender, "-b", "--factory-startup", "--threads", str(args.threads),
             "-P", str(Path(__file__).resolve()), "--", "--inner", str(params_path)],
            check=True,
        )
        paths = sorted(frames.glob("f_*.png"))
        if not paths:
            raise SystemExit("Blender rendered no frames")
        if args.keep_frames:
            shutil.copytree(frames, args.keep_frames, dirs_exist_ok=True)
        total = info["end"]
        stamped = [_stamp(path, info["start"] + i, total, 1) for i, path in enumerate(paths)]

        from PIL import Image

        palette = _shared_palette(stamped)
        gif_frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in stamped]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        duration = round(1000 / info["fps"])
        gif_frames[0].save(args.out, save_all=True, append_images=gif_frames[1:], duration=duration,
                           loop=0, optimize=True, disposal=1)
        if args.still:
            tick = args.still_tick if args.still_tick is not None else (info["start"] + info["end"]) // 2
            stamped[tick - info["start"]].save(args.still)
        if args.mp4 and shutil.which(args.ffmpeg):
            stamped_dir = Path(tmp) / "stamped"
            stamped_dir.mkdir()
            for i, im in enumerate(stamped):
                im.save(stamped_dir / f"s_{i:05d}.png")
            subprocess.run(
                [args.ffmpeg, "-y", "-loglevel", "error", "-framerate", str(info["fps"]),
                 "-i", str(stamped_dir / "s_%05d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-crf", "23", "-movflags", "+faststart", str(args.mp4)],
                check=True,
            )
    print(f"{args.out}: {len(paths)} frames at {info['fps']:g} fps, {info['entities']} entities, "
          f"{args.out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
