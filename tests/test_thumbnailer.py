import hashlib
import re

import pytest
from PIL import Image

from app.thumbnailer import ThumbnailError, generate_thumbnail, new_thumbnail_name
from tests.conftest import make_broken_png, make_png

NAME_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}\.webp$")


def _open(out_dir, name):
    img = Image.open(out_dir / name)
    img.load()
    return img


@pytest.mark.parametrize("max_edge", [768, 512])
def test_large_image_is_shrunk_to_max_edge_keeping_aspect(tmp_path, max_edge):
    # 確認事項 #1: verified against the configured max edge (768 default; 512 also passes).
    src = make_png(tmp_path / "big.png", size=(2048, 1024))
    out = tmp_path / "thumbs"

    name = generate_thumbnail(src, out, max_edge=max_edge, quality=80)

    img = _open(out, name)
    assert img.format == "WEBP"
    assert max(img.size) == max_edge
    assert img.size == (max_edge, max_edge // 2)


def test_tall_image_uses_height_as_long_edge(tmp_path):
    src = make_png(tmp_path / "tall.png", size=(300, 900))
    name = generate_thumbnail(src, tmp_path / "t", max_edge=768, quality=80)
    assert _open(tmp_path / "t", name).size == (256, 768)


def test_small_image_is_not_upscaled(tmp_path):
    src = make_png(tmp_path / "small.png", size=(300, 200))
    name = generate_thumbnail(src, tmp_path / "t", max_edge=768, quality=80)
    assert _open(tmp_path / "t", name).size == (300, 200)


def test_exact_edge_is_kept(tmp_path):
    src = make_png(tmp_path / "edge.png", size=(768, 100))
    name = generate_thumbnail(src, tmp_path / "t", max_edge=768, quality=80)
    assert _open(tmp_path / "t", name).size == (768, 100)


def test_alpha_is_preserved(tmp_path):
    src = make_png(tmp_path / "rgba.png", size=(100, 100), mode="RGBA")
    name = generate_thumbnail(src, tmp_path / "t", max_edge=768, quality=80)
    img = _open(tmp_path / "t", name)
    assert img.mode == "RGBA"
    assert img.getpixel((50, 50))[3] < 255


def test_name_format_and_uniqueness(tmp_path):
    src = make_png(tmp_path / "n.png")
    a = generate_thumbnail(src, tmp_path / "t", 768, 80)
    b = generate_thumbnail(src, tmp_path / "t", 768, 80)
    assert NAME_RE.match(a) and NAME_RE.match(b)
    assert a != b
    assert NAME_RE.match(new_thumbnail_name())
    assert (tmp_path / "t" / a).parent == tmp_path / "t"  # flat output


def test_source_is_untouched(tmp_path):
    src = make_png(tmp_path / "src.png", size=(1000, 500))
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    mtime = src.stat().st_mtime_ns
    generate_thumbnail(src, tmp_path / "t", 768, 80)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert src.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("kind", ["garbage", "truncated"])
def test_broken_input_raises_and_leaves_no_file(tmp_path, kind):
    src = make_broken_png(tmp_path / "bad.png", kind=kind)
    out = tmp_path / "t"
    with pytest.raises(ThumbnailError):
        generate_thumbnail(src, out, 768, 80)
    assert not out.exists() or list(out.iterdir()) == []
