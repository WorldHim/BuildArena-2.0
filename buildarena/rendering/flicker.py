"""Spatially masked temporal darkening detector and frame-time remapping.

The detector targets transient EEVEE background shadows, not all render defects.
The central model envelope is excluded so building edits do not count as flicker.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import percentile_filter, uniform_filter


def background_mask(shape: tuple[int, int], bounds: list[float]) -> np.ndarray:
    height, width = shape
    left, right = bounds
    # Normalized camera bounds include all historical variants throughout an orbit.
    mask = np.zeros(shape, dtype=bool)
    # Broad, low machines cast legitimate shadows into the near foreground;
    # keep that strip out of the background sample too.
    bottom = .925 if right - left > .4 else .963
    mask[round(height * .355):round(height * bottom), :max(0, int((left - .03) * width))] = True
    mask[round(height * .355):round(height * bottom), min(width, int((right + .03) * width)):] = True
    if mask.sum() < height * width * .05:
        raise ValueError("Insufficient clear background for deflicker; increase camera framing margin.")
    return mask


def darkening_scores(frames: np.ndarray, mask: np.ndarray, chunk_size: int = 300) -> np.ndarray:
    if frames.ndim != 3 or frames.shape[1:] != mask.shape or not len(frames):
        raise ValueError("Expected non-empty grayscale frames matching the background mask.")
    # Broad machines leave less background: use a wider spatial filter and a
    # less extreme tail statistic to reject edge/lighting aliasing. Narrow
    # machines permit detecting the small isolated spots missed by that tail.
    broad_model = mask.mean() < .35
    kernel, percentile = (5, 98) if broad_model else (3, 99.5)
    result = np.empty(len(frames), dtype=np.float32)
    for start in range(0, len(frames), chunk_size):
        end = min(len(frames), start + chunk_size)
        lo, hi = max(0, start - 7), min(len(frames), end + 7)
        local = frames[lo:hi]
        reference = percentile_filter(local, 85, size=(15, 1, 1), mode="nearest")
        darkening = uniform_filter(reference.astype(np.float32) - local, size=(1, kernel, kernel))
        result[start:end] = np.percentile(darkening[start-lo:end-lo, mask], percentile, axis=1)
    return result


def choose_threshold(scores: np.ndarray) -> float:
    """Use a separated bright/dark cluster if present, otherwise a robust floor.

    No per-machine names, frame indices, or thresholds are baked into the pipeline.
    """
    ordered = np.sort(scores)
    lo, hi = int(len(ordered) * .20), max(1, int(len(ordered) * .995))
    gaps = np.diff(ordered)
    if hi > lo + 1:
        index = lo + int(np.argmax(gaps[lo:hi-1]))
        if gaps[index] >= 5 and ordered[index+1] >= 1.8 * max(ordered[index], 1):
            return float((ordered[index] + ordered[index+1]) / 2)
    baseline = ordered[:max(1, int(len(ordered) * .30))]
    median = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - median)))
    return max(7.0, median + 6 * 1.4826 * mad)


def retained_step_counts(keep: np.ndarray, timeline: dict) -> list[int]:
    intro, duration = timeline["intro_frames"], timeline["step_frames"]
    return [int(keep[intro + i*duration:intro + (i+1)*duration].sum())
            for i in range(len(timeline["steps"]))]


def removal_runs(keep: np.ndarray) -> list[tuple[int, int]]:
    transitions = np.diff(np.r_[False, ~keep, False].astype(np.int8))
    return list(zip(np.flatnonzero(transitions == 1).tolist(),
                    (np.flatnonzero(transitions == -1) - 1).tolist()))


def selection_filter(keep: np.ndarray, fps: int) -> str:
    ranges = "+".join(f"between(n,{a},{b})" for a, b in removal_runs(keep)) or "0"
    return f"select='not({ranges})',setpts=N/({fps}*TB)"


def write_subtitles(path, keep: np.ndarray, timeline: dict) -> int:
    cumulative = np.r_[0, np.cumsum(keep)]
    fps = timeline["fps"]

    def stamp(frame):
        ms = round(int(cumulative[min(frame, len(keep))]) * 1000 / fps)
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"

    lines = []
    for i, step in enumerate(timeline["steps"]):
        start = timeline["intro_frames"] + i * timeline["step_frames"]
        end = start + timeline["step_frames"]
        if cumulative[start] == cumulative[end]:
            raise ValueError(f"Deflicker would remove all frames of step {i+1}.")
        # User-authored notes are not copied into subtitle markup.
        label = f"STEP {i+1:03} / {len(timeline['steps']):03} | {step['op']} | {step['block_count']} blocks"
        lines.append(f"{i+1}\n{stamp(start)} --> {stamp(end)}\n{label}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(lines)
