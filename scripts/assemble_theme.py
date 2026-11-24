#!/usr/bin/env python3
"""Turn a folder of generated art into a ready-to-preview card theme.

Usage:
    python3 assemble_theme.py CARD_DIR THEME_JSON [--tooth 1.5] [--relief 1.15]
                                                 [--no-gold-foil]

THEME_JSON (paths relative to the JSON file):

    {
      "base": "raw/base.png",
      "items": [
        {"id": "cake", "image": "raw/cake.png", "box": [320, 420, 300], "lift": 0.022},
        {"id": "balloon", "image": "raw/balloon.png", "box": [80, 110, 180],
         "lift": 0.018, "bob": 0.0014}
      ],
      "motifs": [{"image": "raw/badge.png", "px": 520}],
      "glow": {...}, "particles": {...}, "live": {...}, "backLine": "..."
    }

Each element may sit on pure #FF00FF, on a drawn grey/white checkerboard, or
already be a transparent PNG; the backdrop is detected per image. Everything
is validated and processed in memory first: on any error the card is left
exactly as it was. On success the script replaces art/elements and art/baked,
writes art/cover-base.webp, art/cover.webp and art/layout.json, then bakes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bake_card import bake_card  # noqa: E402
from cutout_element import _crop_and_cap, remove_backdrop  # noqa: E402

COVER_W, COVER_H = 940, 1316
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
LIFT_RANGE = (0.004, 0.05)
BOB_MAX = 0.005
PASSTHROUGH_KEYS = ("glow", "particles", "live", "backLine")
# Bases this close to 5:7 (e.g. 2:3 from common image tools) are scaled to fit;
# a slight squash beats cropping away the embossed border and corner flourishes.
STRETCH_TOLERANCE = 0.10


class ThemeError(Exception):
    """A user-facing problem with the theme or card directory."""


@dataclass(frozen=True)
class Element:
    name: str
    image: Image.Image


# ── validation ──────────────────────────────────────────────────────────────


def check_card_dir(card: Path) -> None:
    if not (card / "card.json").is_file() or not (card / "index.html").is_file():
        raise ThemeError(
            f"{card} is not a card directory (no card.json / index.html); "
            "create one first with create_card.py"
        )


def resolve_image(theme_dir: Path, rel: object, label: str) -> Path:
    if not isinstance(rel, str) or not rel:
        raise ThemeError(f"{label}: image path must be a non-empty string")
    path = (theme_dir / rel).resolve()
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ThemeError(f"{label}: unsupported image type {path.suffix}; use png, jpg or webp")
    if not path.is_file():
        raise ThemeError(f"{label}: image not found: {path}")
    return path


def check_number(value: object, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ThemeError(f"{label} must be a number")
    if not low <= value <= high:
        raise ThemeError(f"{label} {value} is outside {low}..{high}")
    return float(value)


def check_item(item: object, index: int, seen: set[str]) -> dict:
    label = f"items[{index}]"
    if not isinstance(item, dict):
        raise ThemeError(f"{label} must be an object")
    item_id = item.get("id")
    if not isinstance(item_id, str) or not ID_PATTERN.match(item_id):
        raise ThemeError(f"{label}: id must be lowercase letters, digits or '-' (got {item_id!r})")
    if item_id in seen:
        raise ThemeError(f"{label}: duplicate id {item_id!r}")
    box = item.get("box")
    if not (isinstance(box, list) and len(box) == 3):
        raise ThemeError(f"{label} ({item_id}): box must be [x, y, width]")
    x = check_number(box[0], f"{item_id} box x", 0, COVER_W - 1)
    check_number(box[1], f"{item_id} box y", 0, COVER_H - 1)
    width = check_number(box[2], f"{item_id} box width", 8, COVER_W)
    if x + width > COVER_W:
        raise ThemeError(f"{item_id} box runs past the right edge ({x + width:.0f} > {COVER_W})")
    check_number(item.get("lift"), f"{item_id} lift", *LIFT_RANGE)
    if "bob" in item:
        check_number(item["bob"], f"{item_id} bob", 0, BOB_MAX)
    return item


def load_theme(theme_path: Path) -> dict:
    try:
        spec = json.loads(theme_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ThemeError(f"could not read theme {theme_path}: {err}") from err
    if not isinstance(spec, dict):
        raise ThemeError("theme must be a JSON object")
    items = spec.get("items")
    if not isinstance(items, list) or not items:
        raise ThemeError("theme items must list at least one foreground element")
    seen: set[str] = set()
    for index, item in enumerate(items):
        seen.add(check_item(item, index, seen)["id"])
    motifs = spec.get("motifs", [])
    if not isinstance(motifs, list):
        raise ThemeError("motifs must be a list")
    for index, motif in enumerate(motifs):
        if not isinstance(motif, dict):
            raise ThemeError(f"motifs[{index}] must be an object")
        check_number(motif.get("px"), f"motifs[{index}] px", 32, 1024)
    return spec


# ── image processing (in memory) ────────────────────────────────────────────


def cut_element(path: Path) -> Image.Image:
    with Image.open(path) as src:
        img = src.copy()
    try:
        return _crop_and_cap(remove_backdrop(img), 1024)
    except ValueError as err:
        raise ThemeError(f"{path.name}: {err}") from err


def build_elements(theme_dir: Path, spec: dict) -> dict[Path, Element]:
    """Cut every referenced image once; name files after the first item using them."""
    elements: dict[Path, Element] = {}
    for item in spec["items"]:
        path = resolve_image(theme_dir, item.get("image"), f"item {item['id']}")
        if path not in elements:
            elements[path] = Element(item["id"], cut_element(path))
    for index, motif in enumerate(spec.get("motifs", [])):
        path = resolve_image(theme_dir, motif.get("image"), f"motifs[{index}]")
        if path not in elements:
            elements[path] = Element(f"motif-{index + 1}", cut_element(path))
    return elements


def item_images(theme_dir: Path, spec: dict, elements: dict[Path, Element]) -> dict[str, Image.Image]:
    """Map item id -> cut image, checking each placed layer fits on the cover."""
    by_id = {}
    for item in spec["items"]:
        image = elements[(theme_dir / item["image"]).resolve()].image
        height = item["box"][2] * image.height / image.width
        if item["box"][1] + height > COVER_H:
            raise ThemeError(f"{item['id']} box runs past the bottom edge")
        by_id[item["id"]] = image
    return by_id


def load_base(theme_dir: Path, rel: object) -> Image.Image:
    path = resolve_image(theme_dir, rel, "base")
    with Image.open(path) as src:
        rgb = src.convert("RGB")
    ratio_gap = abs(rgb.width / rgb.height - COVER_W / COVER_H) / (COVER_W / COVER_H)
    if ratio_gap <= STRETCH_TOLERANCE:
        return rgb.resize((COVER_W, COVER_H), Image.LANCZOS)
    return ImageOps.fit(rgb, (COVER_W, COVER_H), Image.LANCZOS)


def compose_cover(base: Image.Image, items: list[dict], elements: dict[str, Image.Image]) -> Image.Image:
    cover = base.convert("RGBA")
    for item in items:
        element = elements[item["id"]]
        x, y, width = item["box"]
        size = (round(width), max(1, round(width * element.height / element.width)))
        cover.alpha_composite(element.resize(size, Image.LANCZOS), (round(x), round(y)))
    return cover.convert("RGB")


# ── writing ─────────────────────────────────────────────────────────────────


def build_layout(spec: dict, src_of: dict[Path, str], theme_dir: Path) -> dict:
    def src(rel: str) -> str:
        return src_of[(theme_dir / rel).resolve()]

    items = [
        {
            "id": item["id"],
            "src": src(item["image"]),
            "box": item["box"],
            "lift": item["lift"],
            **({"bob": item["bob"]} if "bob" in item else {}),
        }
        for item in spec["items"]
    ]
    motifs = [{"src": src(m["image"]), "px": m["px"]} for m in spec.get("motifs", [])]
    extras = {key: spec[key] for key in PASSTHROUGH_KEYS if key in spec}
    return {
        "base": "./art/cover-base.webp",
        "cover": "./art/cover.webp",
        "size": [COVER_W, COVER_H],
        "winterEffects": bool(spec.get("winterEffects", False)),
        "items": items,
        "motifs": motifs,
        **extras,
    }


def write_card(card: Path, base: Image.Image, cover: Image.Image,
               elements: dict[Path, Element], layout: dict) -> None:
    art = card / "art"
    for stale in (art / "elements", art / "baked"):
        if stale.exists():
            shutil.rmtree(stale)
    (art / "elements").mkdir(parents=True)
    for element in elements.values():
        element.image.save(art / "elements" / f"{element.name}.webp", "WEBP", quality=92, method=6)
    base.save(art / "cover-base.webp", "WEBP", quality=92, method=6)
    cover.save(art / "cover.webp", "WEBP", quality=92, method=6)
    (art / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assemble(card: Path, theme_path: Path, *, tooth: float, relief: float, gold_foil: bool) -> int:
    check_card_dir(card)
    spec = load_theme(theme_path)
    theme_dir = theme_path.parent.resolve()
    base = load_base(theme_dir, spec.get("base"))
    elements = build_elements(theme_dir, spec)
    src_of = {path: f"./art/elements/{el.name}.webp" for path, el in elements.items()}
    cover = compose_cover(base, spec["items"], item_images(theme_dir, spec, elements))
    layout = build_layout(spec, src_of, theme_dir)
    try:
        write_card(card, base, cover, elements, layout)
    except OSError as err:
        raise ThemeError(f"could not write theme into {card}: {err}") from err
    print(f"Assembled {len(spec['items'])} layers into {card / 'art'}")
    return bake_card(card, gold_foil=gold_foil, tooth=tooth, relief=relief)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble generated art into a card theme.")
    parser.add_argument("card_dir", help="card directory made by create_card.py")
    parser.add_argument("theme", help="theme JSON describing base, items and effects")
    parser.add_argument("--tooth", type=float, default=1.5, help="cold-press cotton tooth strength")
    parser.add_argument("--relief", type=float, default=1.15, help="normal relief strength")
    parser.add_argument("--no-gold-foil", action="store_true", help="disable gold-foil detection")
    args = parser.parse_args(argv)
    try:
        return assemble(
            Path(args.card_dir).resolve(),
            Path(args.theme).resolve(),
            tooth=args.tooth,
            relief=args.relief,
            gold_foil=not args.no_gold_foil,
        )
    except ThemeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
