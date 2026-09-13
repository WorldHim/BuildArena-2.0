# Build-history animation rendering

The renderer takes a **valid-only build-history JSON** as its sole required content input. It replays every construction operation, imports the geometry, keeps the model in fixed world coordinates, renders a continuous camera orbit, detects and skips background ghost frames, retimes step subtitles, and validates the final MP4.

You do not need to supply a BSG file, Blender scene, or list of bad frames. The output is a construction presentation, not a recording of gameplay or physics simulation.

## External dependencies and exact versions

**This repository provides only the rendering pipeline, scene-generation code, and deflicker code. It does not bundle Blender, BesiegeCreationImporter source, or Besiege game assets.** Obtain these dependencies separately from their respective sources. Do not commit their installation directories, source checkouts, or game assets to this repository.

To reproduce the verified results, use this combination:

| Dependency | Required version for reproducing the verified results | Source |
| --- | --- | --- |
| Blender | **5.1.1** | Download 5.1.1 from the [official Blender previous versions page](https://www.blender.org/download/previous-versions/), then install or extract it separately |
| BesiegeCreationImporter | **Source commit `c2c2b8b5d1171c03aee05c2f888c394aa3775345`** | The [specified upstream commit](https://github.com/arkangel-dev/BesiegeCreationImporter/tree/c2c2b8b5d1171c03aee05c2f888c394aa3775345), fetched automatically by the renderer or checked out manually as shown below |
| Besiege assets | `Besiege_Data/Skins` from your local game installation | Install the game and configure `BESIEGE_DATA_PATH` as described in the main README |

The importer's `bl_info` version is **2.0.5**, but that version number alone does not identify the source revision. **Use the full commit hash above; do not substitute an upstream Release package or the latest default-branch code.**

The current runtime check only enforces a minimum Blender version of 5.0.1. This does not guarantee compatibility with every newer version. End-to-end verification used **Blender 5.1.1 with the importer commit above**. Other combinations require separate verification.

## Installation and usage

First complete the game-asset setup in the main README, including `BESIEGE_DATA_PATH` and the other required paths in `.env`. Then install the Python rendering dependencies:

```powershell
uv sync --extra render
```

Install **Blender 5.1.1** separately. The renderer checks `--blender`, `BLENDER_PATH`, the system PATH, and standard Windows Blender installation directories, in that order. If several versions are installed, explicitly select the 5.1.1 executable:

```powershell
$env:BLENDER_PATH = 'C:/Program Files/Blender Foundation/Blender 5.1/blender.exe'
& $env:BLENDER_PATH --version
```

The first output line should be `Blender 5.1.1`. The `5.1` directory name is not a substitute for checking the actual executable version.

Run the complete pipeline with one command:

```powershell
uv run --extra render python scripts/render_history.py control/examples/rocket_orbit_return/machine.json
```

The module entry point is also available:

```powershell
uv run --extra render python -m buildarena.rendering control/examples/rocket_orbit_return/machine.json
```

The default output is `.local/renders/<JSON-name>-<content-hash>/animation.mp4`. The content hash distinguishes different histories with the same filename. To save frames and output on another drive:

```powershell
uv run --extra render python scripts/render_history.py control/examples/shuttle_booster_recovery/machine.json --output 'D:/BuildArena-Renders/shuttle'
```

On the first run, Git fetches [BesiegeCreationImporter](https://github.com/arkangel-dev/BesiegeCreationImporter) at the verified source commit `c2c2b8b5d1171c03aee05c2f888c394aa3775345` into `.local/render_dependencies/`. The source is loaded inside a separate Blender process. It is not installed into your user add-on directory and does not change Blender user preferences. The asset-name compatibility patch for the current game's `ReactionSteeringBlock` is applied only to the isolated cache.

The first download requires Git and network access. The repository's `.gitignore` excludes `.local/`, so downloaded dependencies are not committed as repository source. `uv sync --extra render` installs Python dependencies only; it does not install Blender. Importer source is fetched separately when rendering first runs.

### Manual checkout and offline use

To prepare the importer in advance, run these commands on a machine with network access:

```powershell
git init .local/external/BesiegeCreationImporter
git -C .local/external/BesiegeCreationImporter fetch --depth 1 https://github.com/arkangel-dev/BesiegeCreationImporter c2c2b8b5d1171c03aee05c2f888c394aa3775345
git -C .local/external/BesiegeCreationImporter checkout --detach FETCH_HEAD
git -C .local/external/BesiegeCreationImporter rev-parse HEAD
```

The last command must print `c2c2b8b5d1171c03aee05c2f888c394aa3775345`. Keep the checkout under `.local/` or outside the repository. You can copy this directory to an offline machine, then pass its location explicitly:

```powershell
uv run --extra render python scripts/render_history.py control/examples/rocket_orbit_return/machine.json --importer-source .local/external/BesiegeCreationImporter
```

`--importer-source` must point to the directory containing the importer's `__init__.py`. The renderer copies it into an isolated cache without modifying the original checkout. This option permits custom source and **does not enforce the recommended commit**, so verify the `rev-parse HEAD` output yourself. You do not need to install or enable the add-on manually in Blender's interface.

Blender, the third-party importer, and game assets remain subject to their respective licenses. This repository does not redistribute them.

## Defaults and options

| Setting | Default |
| --- | --- |
| Resolution and frame rate | 1920 x 1080, 30 fps |
| Time per construction step | 0.5 seconds before frame filtering |
| Intro and tail hold | 1.5 seconds each |
| Finished-model showcase | 24 seconds |
| Camera orbit period | One revolution every 24 seconds, independent of construction steps |
| Rendering | EEVEE, 64 samples, cool studio lighting |
| Automatic deflicker | Always runs, with an automatically selected threshold |

All historical states share one camera focus and fixed world coordinates. Moves, rotations, and removals are represented through visibility changes between historical geometry variants, so the animation shows the full history rather than just the final model. Camera clipping distances are tightened according to model size to reduce the risk of EEVEE shadow artifacts caused by an excessively wide depth range.

Optional arguments include `--width`, `--height`, `--fps`, `--samples`, `--step-seconds`, `--intro-seconds`, `--hero-seconds`, `--tail-seconds`, `--orbit-seconds`, and `--besiege-data`. Run `--help` for the complete argument list.

A short preview command still replays every operation in the input:

```powershell
uv run --extra render python scripts/render_history.py your_history.json --output .local/render-preview --width 640 --height 360 --samples 16 --step-seconds 0.1 --intro-seconds 0.2 --hero-seconds 1 --tail-seconds 0.2
```

## What automatic deflicker detects

The detector targets **transient EEVEE ground-shadow spots and ghost artifacts**. It reduces frames to 240 x 135, excludes the model using the projected bounds of all historical geometry over a complete orbit, and measures the background on either side. A brightness percentile over a 15-frame window provides the reference; spatial smoothing and local darkening statistics identify outliers. Broad and narrow models use different smoothing scales. Thresholds are selected from the score distribution without hardcoded machine names or frame numbers.

The output retains the requested frame rate after bad frames are removed. This shortens the video and changes local camera speed. The renderer does not blend frames or generate new images with optical flow. Each construction step must retain at least one frame. Subtitles are retimed using the retained frames, and fades are applied after detection.

If filtering would remove an entire step, remove more than 65% of the frames, or leave insufficient background for analysis, the pipeline reports an error and preserves the images. It does not report an incomplete construction sequence as a successful result. After inspecting `flicker_metrics.npz`, you can override the threshold with `--flicker-threshold <positive-number>`; a higher value removes fewer frames. This option can reuse existing renders.

This is not a universal flicker repair algorithm. Overlapping model geometry, material errors, and persistent shadow defects may not be detected through background statistics. Low frame rates and fast camera motion can also change its behavior. The default frame rate and orbit speed were checked against the historical frame sequences of the three demonstration films.

## Resuming and output files

Repeat the same command to resume. Each image is written to `.part.png` before being published as a completed PNG. On resume, published images are checked for dimensions and PNG integrity; corrupt frames are quarantined and rendered again. An operating-system lock protects the output directory. If an interrupted controller leaves Blender or FFmpeg running, the next invocation asks you to wait for that process to exit rather than allowing concurrent writes to the same directory.

Use a new output directory if the input content, render settings, Blender version, importer source, or pipeline code changes. Mismatched caches are rejected. Changing only the deflicker threshold can reuse existing renders. A completed video with matching configuration and hash is returned immediately.

| Output | Purpose |
| --- | --- |
| `animation.mp4` | Final deflickered video with optional step subtitles, disabled by default |
| `validation.json` | Full decode verification, frame count, frame rate, resolution, subtitle count, hash, and removal statistics |
| `status.json`, stage logs, and progress files | Current stage and failure diagnostics |
| `scene.blend`, `scene_verification.json` | Animation scene with packed assets and geometry/framing verification |
| `frames/frame_*.png` | Original rendered frames, always retained |
| `frame_decisions.csv` | Original frame number, darkening score, keep/drop decision, and output frame number |
| `steps.srt` | Retimed step subtitles |
| `flicker_metrics.npz` | Background mask, darkening scores, threshold, and keep mask |
| `unfiltered.mp4`, `analysis.gray` | Intermediate video and analysis data for resuming and diagnosis |
| `timeline.json`, `all_variants.bsg`, `final.bsg`, `original_history.json` | History replay artifacts |

The final MP4 is first written to a temporary file and published as `animation.mp4` only after full decode verification. Do not treat an `.encoding.mp4` file as a finished video. To avoid repeatedly reading large PNG sequences, the pipeline encodes an intermediate video at CRF 16 and then encodes the filtered final video. This involves two lossy encoding passes; original PNGs remain unchanged. Long histories require substantial disk space.

## Verification

```powershell
uv run --extra render python -m unittest discover -s tests -p 'test_rendering*.py' -v
```

Tests cover distinguishing background ghosts from central model motion, temporal chunk boundaries, normal lighting changes, subtitle retiming, protection against removing entire steps, input validation, and process locks. Actual Blender rendering still requires a local Blender installation and game assets.

Implementation verification record (2026-09-11):

- Nine automated tests passed, including actual FFmpeg filtering, full MP4 decoding, subtitle extraction, and intermediate-video cache reuse.
- Blender 5.1.1 replayed a six-step history containing additions, a move, and a removal, rendered 111 frames, and automatically produced the final video.
- A deliberately damaged PNG was repaired on resume; the contents and modification times of the other 110 frames remained unchanged. Completed-video reuse and rejection of mismatched settings also passed.
- Regression checks on the complete thumbnail sequences of the three previous films produced exactly the same frame decisions as their manually calibrated versions: 585 frames removed from the car, 735 from the rocket, and 3,409 from the shuttle. The three full-length films were not rendered again for this check.
