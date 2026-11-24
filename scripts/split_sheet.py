#!/usr/bin/env python3
"""Split one generated sticker sheet into separate transparent elements.

Usage:
    python3 split_sheet.py SHEET OUT_DIR [--min-area 0.0015]

SHEET holds several paper-cut elements, well apart, on pure #FF00FF, a
grey/white checkerboard, or a transparent background. Each separate element
is written to OUT_DIR as piece-01.png, piece-02.png, ... in reading order
(top row first, left to right), plus contact.png: a numbered overview so you
can see which piece is which before writing theme.json.

Thin attached parts (balloon strings, stems) stay with their element; specks
smaller than --min-area (fraction of the sheet) are dropped. OUT_DIR must be
new or empty, and nothing is written unless the whole sheet splits cleanly.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutout_element import remove_backdrop  # noqa: E402

LABEL_GRID_PX = 600  # longest side of the coarse grid used for labelling
MAX_PIECES = 40
SOLID_ALPHA = 128


class SheetError(Exception):
    """A user-facing problem with the sheet or output directory."""


@dataclass(frozen=True)
class Piece:
    top: int
    left: int
    bottom: int
    right: int
    image: Image.Image

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


def coarse_mask(solid: np.ndarray, factor: int) -> np.ndarray:
    """Downsample with 'any' so thin strings and stems keep their connection."""
    h, w = solid.shape
    ph, pw = math.ceil(h / factor) * factor, math.ceil(w / factor) * factor
    padded = np.zeros((ph, pw), dtype=bool)
    padded[:h, :w] = solid
    return padded.reshape(ph // factor, factor, pw // factor, factor).any(axis=(1, 3))


def label_components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected component labels for a small boolean grid."""
    labels = np.zeros(mask.shape, dtype=np.int32)
    h, w = mask.shape
    count = 0
    for y0, x0 in zip(*np.nonzero(mask)):
        if labels[y0, x0]:
            continue
        count += 1
        labels[y0, x0] = count
        stack = [(y0, x0)]
        while stack:
            y, x = stack.pop()
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = count
                        stack.append((ny, nx))
    return labels, count


def grow(mask: np.ndarray) -> np.ndarray:
    """One-cell 8-neighbour dilation, to catch an element's soft anti-aliased rim."""
    padded = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for dy in range(3):
        for dx in range(3):
            out |= padded[dy : dy + mask.shape[0], dx : dx + mask.shape[1]]
    return out


def extract_pieces(rgba: np.ndarray, min_area: float) -> list[Piece]:
    h, w = rgba.shape[:2]
    alpha = rgba[..., 3]
    factor = max(1, math.ceil(max(h, w) / LABEL_GRID_PX))
    labels, count = label_components(coarse_mask(alpha > SOLID_ALPHA, factor))

    def full(grid: np.ndarray) -> np.ndarray:
        return np.repeat(np.repeat(grid, factor, axis=0), factor, axis=1)[:h, :w]

    owner = full(labels)
    pieces = []
    for label in range(1, count + 1):
        core = labels == label
        if core.sum() * factor * factor < min_area * h * w:
            continue
        own = full(grow(core)) & ((owner == 0) | (owner == label))
        piece_alpha = np.where(own, alpha, 0)
        ys, xs = np.nonzero(piece_alpha > 2)
        if len(ys) == 0:
            continue
        top, bottom, left, right = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        crop = rgba[top:bottom, left:right].copy()
        crop[..., 3] = piece_alpha[top:bottom, left:right]
        pieces.append(Piece(int(top), int(left), int(bottom), int(right), Image.fromarray(crop, "RGBA")))
    return pieces


def reading_order(pieces: list[Piece]) -> list[Piece]:
    rows: list[list[Piece]] = []
    row_bottom = -1.0
    for piece in sorted(pieces, key=lambda p: p.cy):
        if rows and piece.cy <= row_bottom:
            rows[-1].append(piece)
        else:
            rows.append([piece])
            row_bottom = piece.bottom
    return [piece for row in rows for piece in sorted(row, key=lambda p: p.cx)]


def contact_sheet(pieces: list[Piece], cell: int = 220) -> Image.Image:
    cols = min(4, len(pieces))
    rows = math.ceil(len(pieces) / cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell), (236, 232, 226))
    draw = ImageDraw.Draw(sheet)
    for index, piece in enumerate(pieces):
        x, y = (index % cols) * cell, (index // cols) * cell
        thumb = piece.image.copy()
        thumb.thumbnail((cell - 40, cell - 50))
        sheet.paste(thumb, (x + (cell - thumb.width) // 2, y + 36 + (cell - 50 - thumb.height) // 2), thumb)
        draw.rectangle([x + 2, y + 2, x + cell - 3, y + cell - 3], outline=(200, 194, 186))
        draw.text((x + 10, y + 8), f"piece-{index + 1:02d}  {piece.image.width}x{piece.image.height}", fill=(60, 54, 50))
    return sheet


def check_out_dir(out: Path) -> None:
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SheetError(f"output {out} must be a new or empty directory (existing files are never overwritten)")


def split(sheet_path: Path, out: Path, min_area: float) -> list[Path]:
    check_out_dir(out)
    try:
        with Image.open(sheet_path) as src:
            rgba = remove_backdrop(src.copy())
    except (OSError, ValueError) as err:
        raise SheetError(f"{sheet_path.name}: {err}") from err
    pieces = reading_order(extract_pieces(rgba, min_area))
    if not pieces:
        raise SheetError("no elements found on the sheet; is the backdrop the only thing in it?")
    if len(pieces) > MAX_PIECES:
        raise SheetError(
            f"found {len(pieces)} pieces; elements are probably touching a noisy backdrop. "
            "Regenerate with a flat backdrop and clear gaps between elements"
        )
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, piece in enumerate(pieces, start=1):
        path = out / f"piece-{index:02d}.png"
        piece.image.save(path, optimize=True)
        paths.append(path)
    contact_sheet(pieces).save(out / "contact.png")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Split a sticker sheet into separate elements.")
    parser.add_argument("sheet", help="sheet image (png/jpg/webp)")
    parser.add_argument("out_dir", help="new or empty directory for piece-NN.png files")
    parser.add_argument("--min-area", type=float, default=0.0015,
                        help="drop blobs smaller than this fraction of the sheet (default 0.0015)")
    args = parser.parse_args(argv)
    try:
        paths = split(Path(args.sheet), Path(args.out_dir), args.min_area)
    except SheetError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    for path in paths:
        with Image.open(path) as img:
            print(f"{path.name}  {img.width}x{img.height}")
    print(f"Overview: {Path(args.out_dir) / 'contact.png'} (check which piece is which before writing theme.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
