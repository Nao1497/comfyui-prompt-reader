"""Builders for ComfyUI API-format prompt graphs used in tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"

POSITIVE = "masterpiece, 1girl, standing in a field"
NEGATIVE = "lowres, bad anatomy"


def standard(positive: str = POSITIVE, negative: str = NEGATIVE) -> dict[str, Any]:
    """CheckpointLoaderSimple -> KSampler with CLIPTextEncode x2, EmptyLatentImage, SaveImage."""
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 872341905, "steps": 20, "cfg": 7.0,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]}},
    }


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def without(graph: dict[str, Any], *node_ids: str) -> dict[str, Any]:
    g = copy.deepcopy(graph)
    for nid in node_ids:
        g.pop(nid, None)
    return g


def with_loras(graph: dict[str, Any], *loras: tuple[str, float, float]) -> dict[str, Any]:
    """Chain LoraLoader nodes between the checkpoint and the sampler."""
    g = copy.deepcopy(graph)
    prev_model = ["4", 0]
    prev_clip = ["4", 1]
    for i, (name, sm, sc) in enumerate(loras):
        nid = str(100 + i)
        g[nid] = {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": name, "strength_model": sm, "strength_clip": sc, "model": prev_model, "clip": prev_clip},
        }
        prev_model = [nid, 0]
        prev_clip = [nid, 1]
    g["3"]["inputs"]["model"] = prev_model
    g["6"]["inputs"]["clip"] = prev_clip
    g["7"]["inputs"]["clip"] = prev_clip
    return g
