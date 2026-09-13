"""One history JSON -> resumable Blender frames -> automatically filtered MP4."""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from buildarena.paths import PROJECT_ROOT, get_besiege_data_path
from .history import prepare, read_history

PACKAGE = Path(__file__).resolve().parent
IMPORTER_URL = "https://github.com/arkangel-dev/BesiegeCreationImporter"
IMPORTER_COMMIT = "c2c2b8b5d1171c03aee05c2f888c394aa3775345"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, data) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def config_digest(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


@contextlib.contextmanager
def job_lock(folder: Path):
    """OS-owned lock is released even if the renderer/controller is killed."""
    with (folder / ".render.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Another render job is using {folder}") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def run(command: list[str], log: Path) -> None:
    record = log.with_suffix(".process.json")
    with log.open("a", encoding="utf-8") as stream:
        stream.write("\n" + repr(command) + "\n")
        stream.flush()
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, creationflags=FLAGS)
        write_json(record, {"pid": process.pid, "command": command})
        try:
            while process.poll() is None:
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    print(f"Working; log: {log}", flush=True)
            if process.returncode:
                raise RuntimeError(f"Process exited {process.returncode}; see {log}")
        except BaseException:
            # A normal Ctrl+C must not leave a competing child writing this job.
            if process.poll() is None:
                process.terminate()
                process.wait()
            raise
        finally:
            record.unlink(missing_ok=True)


def check_orphan_processes(folder: Path) -> None:
    """A hard-killed controller may leave Blender/FFmpeg alive; never race it."""
    for record in folder.glob("*.process.json"):
        pid = json.loads(record.read_text(encoding="utf-8"))["pid"]
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, never terminate
            if handle:
                try:
                    alive = kernel.WaitForSingleObject(handle, 0) != 0
                finally:
                    kernel.CloseHandle(handle)
            else:
                alive = ctypes.get_last_error() == 5  # access denied: be conservative
        else:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        if alive:
            raise RuntimeError(f"A previous subprocess ({pid}) is still running; see {record}. Retry after it exits.")
        record.unlink()


def find_blender(value: str | None) -> str:
    requested = value or os.environ.get("BLENDER_PATH")
    if requested:
        result = shutil.which(requested) or (str(Path(requested).resolve()) if Path(requested).is_file() else None)
    else:
        result = shutil.which("blender")
        if not result and os.name == "nt":
            candidates = list((Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Blender Foundation").glob("Blender */blender.exe"))
            candidates.sort(key=lambda p: tuple(map(int, re.findall(r"\d+", p.parent.name))), reverse=True)
            result = str(candidates[0]) if candidates else None
    if not result:
        raise FileNotFoundError("Install Blender 5.0.1+ and put blender on PATH, or set BLENDER_PATH / --blender.")
    return result


def tree_digest(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not any(p in {".git", "__pycache__"} for p in path.relative_to(folder).parts):
            digest.update(path.relative_to(folder).as_posix().encode())
            digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def importer_source(source: str | None, besiege_data: Path) -> Path:
    cache = PROJECT_ROOT / ".local/render_dependencies"
    cache.mkdir(parents=True, exist_ok=True)
    if source:
        source_path = Path(source).resolve()
        if not (source_path / "__init__.py").is_file():
            raise ValueError("--importer-source must contain the importer's __init__.py.")
        identity = "custom-" + tree_digest(source_path)[:20]
    else:
        identity = IMPORTER_COMMIT
    template = besiege_data / "Skins/Template"
    modern_steering = (template / "ReactionSteeringBlock").is_dir() and not (template / "ReactionSteerBlock").exists()
    identity += "-modern" if modern_steering else "-original"
    target = cache / identity
    target.mkdir(exist_ok=True)
    with job_lock(target):
        check_orphan_processes(target)
        package = target / "BesiegeCreationImporter"
        if not (target / "ready.json").exists():
            # Incomplete downloads are harmless; don't mutate the user's source tree.
            if source:
                shutil.copytree(source_path, package, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(".git", "__pycache__"))
            else:
                package.mkdir(exist_ok=True)
                for arguments in [["init", str(package)], ["-C", str(package), "fetch", "--depth", "1", IMPORTER_URL, IMPORTER_COMMIT],
                                  ["-C", str(package), "checkout", "--detach", "FETCH_HEAD"]]:
                    run(["git", *arguments], target / "download.log")
            write_json(target / "ready.json", {"repository": IMPORTER_URL if not source else str(source_path), "revision": identity})
        # The pinned importer calls this asset ReactionSteerBlock; current game
        # skins call it ReactionSteeringBlock. Patch only our isolated checkout.
        mapping = package / "object_transform_data_converted_v2.json"
        data = json.loads(mapping.read_text(encoding="utf-8"))
        if modern_steering:
            node = data.get("101", {})
            if node.get("code_name") == "ReactionSteerBlock":
                node["code_name"] = "ReactionSteeringBlock"
                for component in node.get("components", []):
                    if component.get("base_source") == "ReactionSteerBlock":
                        component["base_source"] = "ReactionSteeringBlock"
                write_json(mapping, data)
        return package


def validate_video(ffmpeg: str, path: Path, count: int, config: dict, subtitles: int | None = None) -> dict:
    import imageio_ffmpeg
    result = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                             "-progress", "pipe:1", "-f", "null", "-"],
                            capture_output=True, text=True, check=True, creationflags=FLAGS)
    counts = re.findall(r"frame=(\d+)", result.stdout)
    if result.stderr.strip() or not counts or int(counts[-1]) != count:
        raise RuntimeError(f"Video verification failed: expected {count} frames; {result.stderr}")
    reader = imageio_ffmpeg.read_frames(str(path))
    try:
        metadata = next(reader)
    finally:
        reader.close()
    if metadata["size"] != (config["width"], config["height"]) or abs(metadata["fps"] - config["fps"]) > .001:
        raise RuntimeError(f"Video resolution/fps mismatch: {metadata}")
    if subtitles is not None:
        result = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:s:0", "-f", "srt", "pipe:1"],
                                capture_output=True, check=True, creationflags=FLAGS)
        if result.stdout.count(b" --> ") != subtitles:
            raise RuntimeError("Encoded subtitle cue count differs from the build history.")
    return {"frames": count, "fps": config["fps"], "resolution": [config["width"], config["height"]],
            "duration_seconds": count / config["fps"], "full_decode_passed": True,
            "sha256": sha256(path), "size_bytes": path.stat().st_size}


def postprocess(folder: Path, config: dict, timeline: dict, scene: dict, stage) -> dict:
    import imageio_ffmpeg
    import numpy as np
    from .flicker import (background_mask, choose_threshold, darkening_scores, removal_runs,
                          retained_step_counts, selection_filter, write_subtitles)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    count = scene["frame_count"]
    master, proxy = folder / "unfiltered.mp4", folder / "analysis.gray"
    marker = folder / "source_validation.json"
    valid_source = False
    if marker.exists() and master.exists() and proxy.exists():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        valid_source = (saved["sha256"] == sha256(master) and proxy.stat().st_size == count*240*135
                        and saved["proxy_sha256"] == sha256(proxy))
    if not valid_source:
        stage("encoding_source", "Read PNGs once to build a video and analysis proxy")
        temporary = folder / "unfiltered.encoding.mp4"
        run([ffmpeg, "-y", "-threads", "4", "-framerate", str(config["fps"]), "-start_number", "1",
             "-i", str(folder / "frames/frame_%04d.png"), "-filter_complex",
             "[0:v]split=2[full][small];[small]scale=240:135,format=gray[gray]",
             "-map", "[full]", "-frames:v", str(count), "-c:v", "libx264", "-threads", "8", "-preset", "fast", "-crf", "16",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
             "-map", "[gray]", "-frames:v", str(count), "-pix_fmt", "gray", "-f", "rawvideo",
             "-progress", str(folder / "source_progress.txt"), str(proxy)], folder / "encode_source.log")
        if proxy.stat().st_size != count*240*135:
            raise RuntimeError("Incomplete frame sequence; see encode_source.log.")
        checked = validate_video(ffmpeg, temporary, count, config)
        checked["proxy_sha256"] = sha256(proxy)
        temporary.replace(master)
        write_json(marker, checked)
    stage("deflicker", "Detect transient background shadows and remap build steps")
    frames = np.memmap(proxy, mode="r", dtype=np.uint8, shape=(count, 135, 240))
    mask = background_mask((135, 240), [scene["screen_bounds_min"][0], scene["screen_bounds_max"][0]])
    scores = darkening_scores(frames, mask)
    threshold = config["flicker_threshold"] or choose_threshold(scores)
    keep = scores <= threshold
    np.savez(folder / "flicker_metrics.npz", scores=scores, mask=mask, keep=keep, threshold=threshold)
    step_counts = retained_step_counts(keep, timeline)
    if not all(step_counts) or keep.mean() < .35:
        missing = [i+1 for i, value in enumerate(step_counts) if not value]
        raise RuntimeError(f"Unsafe deflicker selection: {int((~keep).sum())}/{count} removed; empty steps {missing}. "
                           "Inspect flicker_metrics.npz; retry with --flicker-threshold VALUE. PNGs are retained.")
    with (folder / "frame_decisions.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["original_frame", "darkening_score", "action", "output_frame"])
        cumulative = np.cumsum(keep)
        for i, score in enumerate(scores):
            writer.writerow([i+1, float(score), "keep" if keep[i] else "drop", int(cumulative[i]) if keep[i] else ""])
    subtitles = write_subtitles(folder / "steps.srt", keep, timeline)
    duration = int(keep.sum()) / config["fps"]
    filters = selection_filter(keep, config["fps"])
    # Apply fades only AFTER detection; intended fades are never classified as defects.
    fade_in, fade_out = min(.8, duration/4), min(1.2, duration/4)
    filters += f",fade=t=in:st=0:d={fade_in},fade=t=out:st={duration-fade_out}:d={fade_out}"
    (folder / "select_filter.txt").write_text(filters, encoding="utf-8")
    stage("encoding_final", f"Keeping {keep.sum()}/{count} frames, all {subtitles} build steps")
    temporary = folder / "animation.encoding.mp4"
    run([ffmpeg, "-y", "-i", str(master), "-i", str(folder / "steps.srt"), "-map", "0:v:0", "-map", "1:s:0",
         "-filter_script:v", str(folder / "select_filter.txt"), "-c:v", "libx264", "-threads", "8", "-preset", "fast",
         "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(config["fps"]), "-fps_mode", "cfr", "-c:s", "mov_text",
         "-disposition:s:0", "0", "-metadata:s:s:0", "title=Build steps", "-movflags", "+faststart",
         "-progress", str(folder / "encode_progress.txt"), str(temporary)], folder / "encode_final.log")
    stage("validating", "Decode every output frame and verify the embedded subtitles")
    report = validate_video(ffmpeg, temporary, int(keep.sum()), config, subtitles)
    report.update(original_frames=count, removed_frames=int((~keep).sum()), threshold=float(threshold),
                  config_sha256=config_digest(config),
                  all_steps_preserved=True, build_steps=subtitles, minimum_frames_per_step=min(step_counts),
                  max_consecutive_dropped=max((b-a+1 for a, b in removal_runs(keep)), default=0),
                  output=str(folder / "animation.mp4"))
    temporary.replace(folder / "animation.mp4")
    write_json(folder / "validation.json", report)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("history_json", type=Path, help="Complete valid-only build-history JSON")
    result.add_argument("--output", type=Path, help="Output directory (default .local/renders/<name>-<hash>)")
    result.add_argument("--blender", help="Blender 5.0.1+ executable; otherwise auto-detected")
    result.add_argument("--importer-source", help="Optional offline importer source checkout; otherwise fetch pinned source")
    result.add_argument("--besiege-data", help="Override BESIEGE_DATA_PATH from .env")
    for key, default in [("width", 1920), ("height", 1080), ("fps", 30), ("samples", 64)]:
        result.add_argument(f"--{key}", type=int, default=default)
    for key, default in [("step", .5), ("intro", 1.5), ("hero", 24), ("tail", 1.5), ("orbit", 24)]:
        result.add_argument(f"--{key}-seconds", type=float, default=default)
    result.add_argument("--flicker-threshold", type=float, help="Override automatic background outlier threshold")
    return result


def execute(args) -> Path:
    # Validate the sole content input before downloading dependencies or launching Blender.
    history_path = args.history_json.resolve()
    history = read_history(history_path)
    config = {key: getattr(args, key) for key in ["width", "height", "fps", "samples", "flicker_threshold"]}
    if any(config[key] <= 0 for key in ["width", "height", "fps", "samples"]) or config["width"] % 2 or config["height"] % 2:
        raise ValueError("Width/height must be positive even integers; fps and samples must be positive.")
    for key in ["step", "intro", "hero", "tail", "orbit"]:
        value = getattr(args, key + "_seconds")
        if not math.isfinite(value) or value < 0 or (key in {"step", "orbit"} and round(value * args.fps) < 1):
            raise ValueError(f"Invalid --{key}-seconds value.")
        config[key + "_frames"] = round(value * args.fps)
    if args.flicker_threshold is not None and (not math.isfinite(args.flicker_threshold) or args.flicker_threshold <= 0):
        raise ValueError("--flicker-threshold must be finite and positive.")
    source_hash = sha256(history_path)
    folder = (args.output or PROJECT_ROOT / ".local/renders" / f"{history_path.stem}-{source_hash[:10]}").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with job_lock(folder):
        check_orphan_processes(folder)
        if not (folder / "config.json").exists() and any(p.name not in {".render.lock", "status.json"} for p in folder.iterdir()):
            raise ValueError("Output directory is not empty and has no render config; choose a new --output.")
        def stage(name, message):
            write_json(folder / "status.json", {"stage": name, "message": message, "time": time.time()})
            print(f"[{name}] {message}", flush=True)
        try:
            import scipy  # optional dependency error is reported before expensive work
            blender = find_blender(args.blender)
            besiege = get_besiege_data_path(data_path=args.besiege_data).resolve()
            importer = importer_source(args.importer_source, besiege)
            version = subprocess.check_output([blender, "--version"], text=True, creationflags=FLAGS).splitlines()[0]
            code = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(PACKAGE.glob("*.py")))).hexdigest()
            config.update(blender=blender, blender_version=version, besiege_data=str(besiege),
                          importer_source=str(importer), importer_sha256=tree_digest(importer),
                          source_sha256=source_hash, pipeline_sha256=code)
            config_path = folder / "config.json"
            if config_path.exists():
                old = json.loads(config_path.read_text(encoding="utf-8"))
                # Threshold changes can reuse rendered frames; render-setting changes cannot.
                if {k: v for k, v in old.items() if k != "flicker_threshold"} != {k: v for k, v in config.items() if k != "flicker_threshold"}:
                    raise ValueError("Output belongs to a different input/settings/tool version; choose a new --output directory.")
                validation = folder / "validation.json"
                if old == config and validation.exists():
                    report = json.loads(validation.read_text(encoding="utf-8"))
                    final = folder / "animation.mp4"
                    if (final.is_file() and report.get("config_sha256") == config_digest(config)
                            and report["sha256"] == sha256(final)):
                        stage("complete", str(final))
                        return final
            write_json(config_path, config)
            if history_path != folder / "original_history.json":
                shutil.copyfile(history_path, folder / "original_history.json")
            stage("replay", f"Replay {len(history)} authored operations")
            if not (folder / "timeline.json").exists():
                prepare(history, folder, config)
            timeline = json.loads((folder / "timeline.json").read_text(encoding="utf-8"))
            scene_path = folder / "scene.blend"
            if not scene_path.exists() or not (folder / "scene_verification.json").exists():
                stage("scene", "Build and verify fixed geometry and a continuous camera orbit")
                run([blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                     str(PACKAGE / "blender_scene.py"), "--", str(folder)], folder / "scene.log")
            scene = json.loads((folder / "scene_verification.json").read_text(encoding="utf-8"))
            # Validate old published images when resuming; interrupted .part.png files
            # are never eligible for reuse. Corrupt images are quarantined for inspection.
            from PIL import Image
            frame_dir = folder / "frames"
            for path in frame_dir.glob("frame_*.png"):
                if ".part." in path.name:
                    continue
                try:
                    with Image.open(path) as image:
                        if image.size != (config["width"], config["height"]):
                            raise ValueError("Wrong frame dimensions")
                        image.verify()
                except (OSError, ValueError, SyntaxError):
                    path.replace(path.with_suffix(".corrupt"))
            expected = [frame_dir / f"frame_{frame:04d}.png" for frame in range(1, scene["frame_count"]+1)]
            if not all(path.exists() for path in expected):
                stage("rendering", f"Render/resume {scene['frame_count']} frames")
                # If any source frame needs replacement, invalidate derived video/proxy.
                (folder / "source_validation.json").unlink(missing_ok=True)
                run([blender, "--background", str(scene_path), "--python-exit-code", "1", "--python",
                     str(PACKAGE / "blender_render.py")], folder / "render.log")
            postprocess(folder, config, timeline, scene, stage)
            stage("complete", str(folder / "animation.mp4"))
            return folder / "animation.mp4"
        except BaseException as error:
            stage("interrupted" if isinstance(error, KeyboardInterrupt) else "failed", str(error))
            raise


def main() -> None:
    args = parser().parse_args()
    try:
        execute(args)
    except ModuleNotFoundError as error:
        raise SystemExit(f"Missing dependency {error.name}. Run: uv sync --extra render") from error
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
