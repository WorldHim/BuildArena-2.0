"""Replay valid operations into a union of immutable geometry variants."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET


def read_history(path: Path) -> list[dict]:
    # Keep this check independent of Machine/game initialization.
    if path.suffix.lower() != ".json" or path.name.lower() == "full.json" or path.name.lower().endswith("_full.json"):
        raise ValueError("Use a valid-only history JSON, not a BSG or full-action trace.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise ValueError("History must be a non-empty operation list.")
    for i, operation in enumerate(data, 1):
        if not isinstance(operation, dict) or not isinstance(operation.get("op"), str) or not isinstance(operation.get("params"), dict):
            raise ValueError(f"Invalid operation at step {i}: expected op and params.")
    if data[0]["op"] != "start" or any(item["op"] == "start" for item in data[1:]):
        raise ValueError("History must start with exactly one start operation.")
    return data


def prepare(history: list[dict], folder: Path, config: dict) -> dict:
    from buildarena.build import Machine

    machine = Machine(name="assembly", save_dir=str(folder / "replay"),
                      do_collision=False, write_full_history=False)
    unknown = sorted({op["op"] for op in history} - machine.operations.keys())
    if unknown:
        raise ValueError(f"Unknown build operations: {unknown}")
    variants, nodes, steps = {}, {}, []
    with (folder / "replay.log").open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log):
        for index, operation in enumerate(history, 1):
            before = len(machine.operation_history)
            result = machine.operations[operation["op"]](**operation["params"])
            if len(machine.operation_history) != before + 1:
                raise RuntimeError(f"Step {index} ({operation['op']}) failed: {str(result)[:400]}")
            # Do not let each intermediate state acquire a different ground offset.
            xml = machine.to_xml(spawn_y=0.0)
            root = ET.fromstring(xml)
            active = []
            by_guid = {block.guid: block for block in machine.blocks.values()}
            for node in root.find("Blocks"):
                block = by_guid[node.get("guid")]
                canonical = copy.deepcopy(node)
                canonical.set("guid", str(block.local_id))
                digest = hashlib.sha256(ET.tostring(canonical)).hexdigest()
                if digest not in variants:
                    guid = str(uuid.uuid5(uuid.NAMESPACE_URL, "buildarena-render/" + digest))
                    canonical.set("guid", guid)
                    variants[digest] = {"guid": guid, "build_id": str(block.local_id),
                                        "block_type": block.name, "first_step": index}
                    nodes[guid] = canonical
                active.append(variants[digest]["guid"])
            steps.append({"step": index, "op": operation["op"], "params": operation["params"],
                          "active": active, "block_count": len(active)})
    (folder / "final.bsg").write_text(xml, encoding="utf-8")
    union = copy.deepcopy(root)
    union.remove(union.find("Blocks"))
    container = ET.SubElement(union, "Blocks")
    container.extend(nodes.values())
    ET.indent(union)
    ET.ElementTree(union).write(folder / "all_variants.bsg", encoding="utf-8", xml_declaration=True)
    timeline = {key: config[key] for key in ["fps", "step_frames", "intro_frames", "hero_frames", "tail_frames"]}
    timeline.update(name="assembly", steps=steps, variants=list(variants.values()),
                    final_block_count=len(machine.blocks), global_spawn_y=0.0)
    temporary = folder / "timeline.json.tmp"
    temporary.write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    temporary.replace(folder / "timeline.json")
    return timeline
