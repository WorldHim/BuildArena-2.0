"""Blender subprocess worker; each completed PNG is published atomically."""
from pathlib import Path
import json
import time

import bpy

scene = bpy.context.scene
folder = Path(bpy.data.filepath).parent
frames = folder / "frames"
frames.mkdir(exist_ok=True)
started = time.monotonic()
total = scene.frame_end
for frame in range(1, total + 1):
    final = frames / f"frame_{frame:04d}.png"
    if not final.is_file():
        scene.frame_set(frame)
        temporary = frames / f"frame_{frame:04d}.part.png"
        scene.render.filepath = str(temporary)
        bpy.ops.render.render(write_still=True)
        temporary.replace(final)
    state = {"frame": frame, "total_frames": total,
             "elapsed_seconds": time.monotonic() - started,
             "status": "frames_complete" if frame == total else "rendering"}
    temporary = folder / "render_progress.tmp"
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(folder / "render_progress.json")
print("ALL_FRAMES_COMPLETE", total, flush=True)
