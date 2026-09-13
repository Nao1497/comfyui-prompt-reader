import copy

from app.comfy_metadata import extract
from tests.prompt_graphs import load_fixture, standard, without


def test_standard_graph_params_model_resolution():
    meta = extract(load_fixture("standard"))
    assert meta.seed == 872341905
    assert meta.steps == 20
    assert meta.cfg == 7.0
    assert meta.sampler_name == "euler"
    assert meta.scheduler == "normal"
    assert meta.model_name == "sd_xl_base_1.0.safetensors"
    assert (meta.gen_width, meta.gen_height) == (1024, 1024)


def test_ksampler_advanced_uses_noise_seed():
    g = standard()
    inputs = g["3"]["inputs"]
    del inputs["seed"]
    inputs["noise_seed"] = 42
    g["3"]["class_type"] = "KSamplerAdvanced"
    assert extract(g).seed == 42


def test_no_sampler_gives_all_none_partial():
    meta = extract(without(standard(), "3"))
    assert meta.status == "partial"
    assert all(
        getattr(meta, f) is None
        for f in ("seed", "steps", "cfg", "sampler_name", "scheduler", "model_name", "gen_width", "gen_height")
    )


def test_empty_and_odd_inputs_do_not_raise():
    assert extract({}).status == "partial"
    assert extract({"1": "not a node", "2": 3}).status == "partial"
    assert extract({"3": {"class_type": "KSampler", "inputs": "bad"}}).status == "partial"


def _two_samplers(save_points_to: str):
    g = standard()
    second = copy.deepcopy(g["3"])
    second["inputs"]["seed"] = 1
    g["30"] = second
    g["31"] = {"class_type": "VAEDecode", "inputs": {"samples": ["30", 0], "vae": ["4", 2]}}
    g["9"]["inputs"]["images"] = [save_points_to, 0]
    return g


def test_multiple_samplers_prefers_the_one_feeding_save_image():
    assert extract(_two_samplers("31")).seed == 1
    assert extract(_two_samplers("8")).seed == 872341905


def test_multiple_samplers_without_save_image_uses_smallest_id():
    g = _two_samplers("31")
    del g["9"]
    assert extract(g).seed == 872341905  # node "3" < node "30"


def test_lora_relay_reaches_ckpt_name():
    g = standard()
    g["10"] = {
        "class_type": "LoraLoader",
        "inputs": {"lora_name": "detail.safetensors", "strength_model": 0.8, "model": ["4", 0], "clip": ["4", 1]},
    }
    g["3"]["inputs"]["model"] = ["10", 0]
    assert extract(g).model_name == "sd_xl_base_1.0.safetensors"


def test_unet_loader_uses_unet_name():
    g = standard()
    g["4"] = {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors", "weight_dtype": "default"}}
    assert extract(g).model_name == "flux1-dev.safetensors"


def test_latent_without_size_gives_none():
    g = standard()
    g["5"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["11", 0], "vae": ["4", 2]}}
    meta = extract(g)
    assert (meta.gen_width, meta.gen_height) == (None, None)


def test_latent_relay_reaches_size():
    g = standard()
    g["12"] = {"class_type": "LatentBatch", "inputs": {"samples": ["5", 0], "amount": 2}}
    g["3"]["inputs"]["latent_image"] = ["12", 0]
    meta = extract(g)
    assert (meta.gen_width, meta.gen_height) == (1024, 1024)


def test_cycle_terminates():
    g = standard()
    g["4"] = {"class_type": "Loop", "inputs": {"model": ["13", 0]}}
    g["13"] = {"class_type": "Loop", "inputs": {"model": ["4", 0]}}
    meta = extract(g)
    assert meta.model_name is None
    assert meta.seed == 872341905


def test_linked_param_is_resolved_through_primitive():
    g = standard()
    g["14"] = {"class_type": "PrimitiveNode", "inputs": {"value": 35}}
    g["3"]["inputs"]["steps"] = ["14", 0]
    g["15"] = {"class_type": "IntConstant", "inputs": {"steps": 99}}
    g["3"]["inputs"]["cfg"] = ["16", 0]
    g["16"] = {"class_type": "FloatConstant", "inputs": {"cfg": 4.5}}
    meta = extract(g)
    assert meta.steps == 35
    assert meta.cfg == 4.5


def test_deep_chain_stops_at_limit():
    g = standard()
    prev = "4"
    for i in range(40):
        nid = f"r{i}"
        g[nid] = {"class_type": "Relay", "inputs": {"model": [prev, 0]}}
        prev = nid
    g["3"]["inputs"]["model"] = [prev, 0]
    assert extract(g).model_name is None


# --- prompt text (TASK-11) ----------------------------------------------------

RAW_POSITIVE = "  masterpiece, 1girl,\r\n (detailed \\(eyes\\)),\n\n\t日本語の説明  \n"
RAW_NEGATIVE = "\nlowres, bad anatomy,  \\n literal backslash-n  "


def test_prompts_are_verbatim():
    meta = extract(standard(RAW_POSITIVE, RAW_NEGATIVE))
    assert meta.positive_prompt == RAW_POSITIVE
    assert meta.negative_prompt == RAW_NEGATIVE
    assert meta.status == "full"


def test_text_linked_to_primitive_is_followed():
    g = standard()
    g["20"] = {"class_type": "PrimitiveNode", "inputs": {"value": "from primitive"}}
    g["6"]["inputs"]["text"] = ["20", 0]
    assert extract(g).positive_prompt == "from primitive"


def test_conditioning_combine_joins_in_order():
    g = standard()
    g["21"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "second part", "clip": ["4", 1]}}
    g["22"] = {
        "class_type": "ConditioningCombine",
        "inputs": {"conditioning_1": ["6", 0], "conditioning_2": ["21", 0]},
    }
    g["3"]["inputs"]["positive"] = ["22", 0]
    meta = extract(g)
    assert meta.positive_prompt == "masterpiece, 1girl, standing in a field\n\nsecond part"
    assert meta.negative_prompt == "lowres, bad anatomy"


def test_controlnet_apply_passes_through():
    g = standard()
    g["23"] = {
        "class_type": "ControlNetApply",
        "inputs": {"conditioning": ["6", 0], "control_net": ["24", 0], "image": ["25", 0], "strength": 1.0},
    }
    g["3"]["inputs"]["positive"] = ["23", 0]
    assert extract(g).positive_prompt == "masterpiece, 1girl, standing in a field"


def test_missing_negative_encoder_gives_partial():
    g = without(standard(), "7")
    meta = extract(g)
    assert meta.positive_prompt == "masterpiece, 1girl, standing in a field"
    assert meta.negative_prompt is None
    assert meta.status == "partial"


def test_non_string_text_is_ignored():
    g = standard()
    g["6"]["inputs"]["text"] = 123
    meta = extract(g)
    assert meta.positive_prompt is None
    assert meta.status == "partial"
