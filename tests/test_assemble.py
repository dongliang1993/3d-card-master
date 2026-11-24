"""Contract tests for scripts/assemble_theme.py (generated art -> ready card)."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

SKILL = Path(__file__).resolve().parents[1]
CREATE = SKILL / "scripts" / "create_card.py"
ASSEMBLE = SKILL / "scripts" / "assemble_theme.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=300)


@pytest.fixture
def card(tmp_path):
    out = tmp_path / "card"
    proc = run(str(CREATE), str(out))
    assert proc.returncode == 0, proc.stderr
    return out


def solid_disc(size, background, fill=(232, 128, 150)):
    """An element drawn on a flat backdrop (or transparent when background is None)."""
    mode = "RGBA" if background is None else "RGB"
    img = Image.new(mode, size, (0, 0, 0, 0) if background is None else background)
    ImageDraw.Draw(img).ellipse([size[0] * 0.2, size[1] * 0.2, size[0] * 0.8, size[1] * 0.8], fill=fill)
    return img


def checkerboard(size, cell=16):
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    yy, xx = np.mgrid[0 : size[1], 0 : size[0]]
    arr[:] = np.where((((yy // cell) + (xx // cell)) % 2 == 0)[..., None], 255, 204)
    return Image.fromarray(arr)


@pytest.fixture
def theme(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    Image.new("RGB", (1024, 1434), (240, 230, 214)).save(raw / "base.png")  # not exactly 940x1316
    solid_disc((300, 300), (255, 0, 255)).save(raw / "cake.png")
    solid_disc((200, 200), None).save(raw / "balloon.png")
    checker = checkerboard((200, 200))
    ImageDraw.Draw(checker).ellipse([40, 40, 160, 160], fill=(207, 164, 78))
    checker.save(raw / "star.jpg", quality=95)
    spec = {
        "base": "raw/base.png",
        "items": [
            {"id": "cake", "image": "raw/cake.png", "box": [320, 420, 300], "lift": 0.022},
            {"id": "balloon", "image": "raw/balloon.png", "box": [80, 110, 180], "lift": 0.018, "bob": 0.0014},
            {"id": "star", "image": "raw/star.jpg", "box": [770, 140, 90], "lift": 0.012},
        ],
        "motifs": [{"image": "raw/star.jpg", "px": 320}],
        "glow": {"at": [470, 438], "color": "#ffcf7a", "size": 0.5},
        "particles": {"kind": "petals", "area": [210, 170, 520, 560], "count": 30},
        "backLine": "Happy Birthday",
    }
    path = tmp_path / "theme.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_assembles_generated_art_into_a_ready_card(card, theme):
    proc = run(str(ASSEMBLE), str(card), str(theme))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    art = card / "art"
    layout = json.loads((art / "layout.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in layout["items"]] == ["cake", "balloon", "star"]
    assert layout["winterEffects"] is False
    assert layout["glow"]["at"] == [470, 438]
    assert layout["particles"]["kind"] == "petals"
    assert layout["backLine"] == "Happy Birthday"
    assert layout["items"][1]["bob"] == 0.0014
    for item in layout["items"]:
        assert (card / item["src"]).is_file()
    assert (card / layout["motifs"][0]["src"]).is_file()

    assert Image.open(art / "cover-base.webp").size == (940, 1316)
    assert Image.open(art / "cover.webp").size == (940, 1316)

    for name in ("cake", "balloon", "star"):
        element = Image.open(art / "elements" / f"{name}.webp").convert("RGBA")
        alpha = np.asarray(element)[..., 3]
        assert alpha.min() == 0, f"{name}: backdrop was not removed"
        assert alpha.max() == 255
    cake = np.asarray(Image.open(art / "elements" / "cake.webp").convert("RGBA")).astype(int)
    opaque = cake[cake[..., 3] > 250]
    assert (np.minimum(opaque[:, 0], opaque[:, 2]) - opaque[:, 1] < 80).all(), "magenta fringe left"

    manifest = json.loads((art / "baked" / "manifest.json").read_text(encoding="utf-8"))
    assert {e["name"] for e in manifest["entries"]} == {"base", "layer-cake", "layer-balloon", "layer-star"}


def test_removes_stale_winter_art(card, theme):
    assert (card / "art" / "elements" / "moon.webp").exists()
    assert run(str(ASSEMBLE), str(card), str(theme)).returncode == 0
    assert sorted(p.name for p in (card / "art" / "elements").iterdir()) == ["balloon.webp", "cake.webp", "star.webp"]
    assert not list((card / "art" / "baked").glob("*moon*"))


def test_cover_shows_elements_at_their_boxes(card, theme):
    assert run(str(ASSEMBLE), str(card), str(theme)).returncode == 0
    cover = Image.open(card / "art" / "cover.webp").convert("RGB")
    cx, cy = 320 + 150, 420 + 150  # centre of the cake disc box
    r, g, b = cover.getpixel((cx, cy))
    assert r > 200 and g < 170, (r, g, b)


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"items": []}, "items"),
        ({"items": [{"id": "x", "image": "raw/cake.png", "box": [900, 10, 300], "lift": 0.02}]}, "box"),
        ({"items": [{"id": "x", "image": "raw/cake.png", "box": [10, 10, 100], "lift": 0.5}]}, "lift"),
        ({"items": [{"id": "x", "image": "raw/nope.png", "box": [10, 10, 100], "lift": 0.02}]}, "not found"),
        ({"items": [{"id": "../x", "image": "raw/cake.png", "box": [10, 10, 100], "lift": 0.02}]}, "id"),
        ({"base": "raw/missing.png"}, "base"),
    ],
)
def test_invalid_theme_fails_before_touching_card(card, theme, patch, message):
    spec = {**json.loads(theme.read_text(encoding="utf-8")), **patch}
    theme.write_text(json.dumps(spec), encoding="utf-8")
    before = (card / "art" / "layout.json").read_bytes()
    proc = run(str(ASSEMBLE), str(card), str(theme))
    assert proc.returncode != 0
    assert message in proc.stderr.lower()
    assert (card / "art" / "layout.json").read_bytes() == before
    assert (card / "art" / "elements" / "moon.webp").exists()


def test_refuses_a_directory_that_is_not_a_card(tmp_path, theme):
    target = tmp_path / "not-a-card"
    (target / "art" / "elements").mkdir(parents=True)
    keep = target / "art" / "elements" / "keep.webp"
    keep.write_bytes(b"x")
    proc = run(str(ASSEMBLE), str(target), str(theme))
    assert proc.returncode != 0
    assert "card" in proc.stderr.lower()
    assert keep.exists()


def _base_with_corner_marks(size):
    img = Image.new("RGB", size, (240, 230, 214))
    d = ImageDraw.Draw(img)
    w, h = size
    for x, y in ((0, 0), (w - 40, 0), (0, h - 40), (w - 40, h - 40)):
        d.rectangle([x, y, x + 39, y + 39], fill=(207, 164, 78))
    return img


@pytest.mark.parametrize("size", [(1024, 1536), (940, 1316), (1000, 1300)])
def test_near_card_ratio_base_keeps_its_border(card, theme, size):
    _base_with_corner_marks(size).save(theme.parent / "raw" / "base.png")
    assert run(str(ASSEMBLE), str(card), str(theme)).returncode == 0
    base = Image.open(card / "art" / "cover-base.webp").convert("RGB")
    assert base.size == (940, 1316)
    for x, y in ((5, 5), (934, 5), (5, 1310), (934, 1310)):
        r, g, b = base.getpixel((x, y))
        assert abs(r - 207) < 20 and abs(g - 164) < 20, f"corner {x},{y} was cropped: {(r, g, b)}"


def test_far_off_ratio_base_is_cropped_not_squashed(card, theme):
    img = Image.new("RGB", (1600, 1000), (240, 230, 214))
    ImageDraw.Draw(img).ellipse([600, 300, 1000, 700], fill=(232, 128, 150))  # a circle in the middle
    img.save(theme.parent / "raw" / "base.png")
    assert run(str(ASSEMBLE), str(card), str(theme)).returncode == 0
    arr = np.asarray(Image.open(card / "art" / "cover-base.webp").convert("RGB")).astype(int)
    pink = (np.abs(arr - (232, 128, 150)).max(axis=2) < 25)
    ys, xs = np.nonzero(pink)
    width, height = xs.max() - xs.min(), ys.max() - ys.min()
    assert abs(width - height) / height < 0.05, "circle got distorted"
