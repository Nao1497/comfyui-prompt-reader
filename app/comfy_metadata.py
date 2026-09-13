"""Extract generation metadata from a ComfyUI API-format ``prompt`` graph (design.md §4).

The graph is ``{node_id: {"class_type": str, "inputs": {name: literal | [node_id, index]}}}``.
Everything here walks links backwards from the sampler node; nothing is keyed on
``class_type`` except the SaveImage lookup used to disambiguate several samplers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

MAX_DEPTH = 32
PARAM_KEYS = ("seed", "steps", "cfg", "sampler_name", "scheduler")
FULL_FIELDS = (
    "positive_prompt", "negative_prompt", "model_name",
    "seed", "steps", "cfg", "sampler_name", "scheduler",
)


@dataclass
class ExtractedMetadata:
    positive_prompt: str | None = None
    negative_prompt: str | None = None
    model_name: str | None = None
    seed: int | None = None
    steps: int | None = None
    cfg: float | None = None
    sampler_name: str | None = None
    scheduler: str | None = None
    gen_width: int | None = None
    gen_height: int | None = None
    status: str = "partial"  # "full" | "partial"; "none" is decided by the caller

    def finalize(self) -> "ExtractedMetadata":
        self.status = "full" if all(getattr(self, f) is not None for f in FULL_FIELDS) else "partial"
        return self


def is_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and not isinstance(value[0], bool)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


class Graph:
    def __init__(self, prompt: dict[str, Any]):
        self.nodes: dict[str, dict[str, Any]] = {
            str(k): v for k, v in prompt.items() if isinstance(v, dict)
        }

    def node(self, node_id: Any) -> dict[str, Any] | None:
        return self.nodes.get(str(node_id))

    @staticmethod
    def inputs(node: dict[str, Any]) -> dict[str, Any]:
        inputs = node.get("inputs")
        return inputs if isinstance(inputs, dict) else {}

    def resolve(self, value: Any, visited: set[str], depth: int) -> Any:
        """Link -> target node dict (or None if missing/cyclic/too deep); literal -> itself."""
        if not is_link(value):
            return value
        node_id = str(value[0])
        if depth >= MAX_DEPTH or node_id in visited:
            return None
        visited.add(node_id)
        return self.node(node_id)

    def sorted_ids(self) -> list[str]:
        return sorted(self.nodes, key=_id_sort_key)


def _id_sort_key(node_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(node_id))
    except ValueError:
        return (1, node_id)


# --- sampler ------------------------------------------------------------------

def find_sampler(graph: Graph) -> dict[str, Any] | None:
    candidates = [
        nid for nid in graph.sorted_ids()
        if {"positive", "negative"} <= set(Graph.inputs(graph.nodes[nid]))
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return graph.nodes[candidates[0]]
    candidate_set = set(candidates)
    for nid in graph.sorted_ids():
        class_type = str(graph.nodes[nid].get("class_type", ""))
        if class_type in ("SaveImage", "PreviewImage") or "SaveImage" in class_type:
            images = Graph.inputs(graph.nodes[nid]).get("images")
            found = _first_upstream(graph, images, candidate_set)
            if found is not None:
                return graph.nodes[found]
    return graph.nodes[candidates[0]]


def _first_upstream(graph: Graph, start: Any, targets: set[str]) -> str | None:
    """Depth-first walk backwards over every link input; return the first target id hit."""
    visited: set[str] = set()
    stack: list[tuple[Any, int]] = [(start, 0)]
    while stack:
        value, depth = stack.pop()
        if not is_link(value) or depth >= MAX_DEPTH:
            continue
        nid = str(value[0])
        if nid in visited:
            continue
        visited.add(nid)
        if nid in targets:
            return nid
        node = graph.node(nid)
        if node is None:
            continue
        for child in reversed(list(Graph.inputs(node).values())):
            stack.append((child, depth + 1))
    return None


# --- literal resolution -------------------------------------------------------

def resolve_literal(graph: Graph, value: Any, key: str) -> Any:
    """Return a literal for ``value``; follow links through nodes exposing ``key``/``value``."""
    visited: set[str] = set()
    depth = 0
    while is_link(value):
        node = graph.resolve(value, visited, depth)
        depth += 1
        if node is None:
            return None
        inputs = Graph.inputs(node)
        if key in inputs:
            value = inputs[key]
        elif "value" in inputs:
            value = inputs["value"]
        else:
            literals = [v for v in inputs.values() if not is_link(v)]
            if len(literals) == 1:
                value = literals[0]
            else:
                return None
    return value


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# --- generation parameters ----------------------------------------------------

def extract_params(graph: Graph, sampler: dict[str, Any], meta: ExtractedMetadata) -> None:
    inputs = Graph.inputs(sampler)
    seed_value = inputs["seed"] if "seed" in inputs else inputs.get("noise_seed")
    seed_key = "seed" if "seed" in inputs else "noise_seed"
    meta.seed = _as_int(resolve_literal(graph, seed_value, seed_key)) if seed_value is not None else None
    meta.steps = _as_int(resolve_literal(graph, inputs.get("steps"), "steps"))
    meta.cfg = _as_float(resolve_literal(graph, inputs.get("cfg"), "cfg"))
    meta.sampler_name = _as_str(resolve_literal(graph, inputs.get("sampler_name"), "sampler_name"))
    meta.scheduler = _as_str(resolve_literal(graph, inputs.get("scheduler"), "scheduler"))


# --- model name ---------------------------------------------------------------

def extract_model_name(graph: Graph, sampler: dict[str, Any]) -> str | None:
    value = Graph.inputs(sampler).get("model")
    visited: set[str] = set()
    depth = 0
    while is_link(value):
        node = graph.resolve(value, visited, depth)
        depth += 1
        if node is None:
            return None
        inputs = Graph.inputs(node)
        for key in ("ckpt_name", "unet_name"):
            if key in inputs:
                return _as_str(resolve_literal(graph, inputs[key], key))
        value = inputs.get("model")  # relay node (LoRA loader etc.)
    return None


# --- generation resolution ----------------------------------------------------

_LATENT_RELAY_KEYS = ("latent_image", "samples", "latent")


def extract_resolution(graph: Graph, sampler: dict[str, Any]) -> tuple[int | None, int | None]:
    value = Graph.inputs(sampler).get("latent_image")
    visited: set[str] = set()
    depth = 0
    while is_link(value):
        node = graph.resolve(value, visited, depth)
        depth += 1
        if node is None:
            return None, None
        inputs = Graph.inputs(node)
        if "width" in inputs and "height" in inputs:
            width = _as_int(resolve_literal(graph, inputs["width"], "width"))
            height = _as_int(resolve_literal(graph, inputs["height"], "height"))
            return width, height
        value = next((inputs[k] for k in _LATENT_RELAY_KEYS if is_link(inputs.get(k))), None)
    return None, None


# --- prompt text --------------------------------------------------------------

PROMPT_JOINER = "\n\n"


def extract_prompt_text(graph: Graph, value: Any) -> str | None:
    """Follow a positive/negative link and return the text found, verbatim (FR-9).

    A node with a string ``text`` input contributes that string. A linked ``text``
    is followed (primitive nodes). A conditioning-processing node (any node whose
    inputs carry links under a key containing "conditioning") is expanded
    recursively and the strings found are joined, in input order, by two newlines.
    """
    texts = _collect_texts(graph, value, set(), 0)
    if not texts:
        return None
    return PROMPT_JOINER.join(texts)


def _collect_texts(graph: Graph, value: Any, visited: set[str], depth: int) -> list[str]:
    node = graph.resolve(value, visited, depth) if is_link(value) else None
    if node is None:
        return []
    inputs = Graph.inputs(node)
    if "text" in inputs:
        text = resolve_literal(graph, inputs["text"], "text")
        return [text] if isinstance(text, str) else []
    texts: list[str] = []
    for key, child in inputs.items():
        if "conditioning" in key.lower() and is_link(child):
            texts.extend(_collect_texts(graph, child, visited, depth + 1))
    return texts


# --- LoRA usage ---------------------------------------------------------------

def normalize_lora_name(name: str) -> str:
    """ComfyUI stores lora_name relative to the LoRA folder; normalise separators."""
    return name.replace("\\", "/").strip().lstrip("/")


def extract_loras(prompt: dict[str, Any]) -> list["LoraUsage"]:
    """Every node with a string ``lora_name`` input, in node-id order, de-duplicated by name."""
    from app.models import LoraUsage

    graph = Graph(prompt if isinstance(prompt, dict) else {})
    seen: set[str] = set()
    usages: list[LoraUsage] = []
    for nid in graph.sorted_ids():
        inputs = Graph.inputs(graph.nodes[nid])
        raw = inputs.get("lora_name")
        if not isinstance(raw, str) or not raw.strip():
            continue
        name = normalize_lora_name(raw)
        if name in seen:
            continue
        seen.add(name)
        strength_model = _as_float(resolve_literal(graph, inputs.get("strength_model", inputs.get("strength")), "strength_model"))
        strength_clip = _as_float(resolve_literal(graph, inputs.get("strength_clip"), "strength_clip"))
        usages.append(LoraUsage(name=name, strength_model=strength_model, strength_clip=strength_clip))
    return usages


# --- entry point --------------------------------------------------------------

def extract(prompt: dict[str, Any]) -> ExtractedMetadata:
    """Extract metadata from an API-format prompt graph. Never raises on odd graphs."""
    meta = ExtractedMetadata()
    graph = Graph(prompt if isinstance(prompt, dict) else {})
    sampler = find_sampler(graph)
    if sampler is None:
        return meta.finalize()
    sampler_inputs = Graph.inputs(sampler)
    meta.positive_prompt = extract_prompt_text(graph, sampler_inputs.get("positive"))
    meta.negative_prompt = extract_prompt_text(graph, sampler_inputs.get("negative"))
    extract_params(graph, sampler, meta)
    meta.model_name = extract_model_name(graph, sampler)
    meta.gen_width, meta.gen_height = extract_resolution(graph, sampler)
    return meta.finalize()
