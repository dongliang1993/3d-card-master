"""Contract tests for scripts/split_sheet.py (one sticker sheet -> separate elements)."""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

SKILL = Path(__file__).resolve().parents[1]
SPLIT = SKILL / "scripts" / "split_sheet.py"
MAGENTA = (255, 0, 255)
PINK, GOLD, MINT, BLUE, CREAM = (232, 128, 150), (207, 164, 78), (156, 207, 184), (120, 150, 210), (246, 239, 226)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SPLIT), *args], capture_output=True, text=True, timeout=300)


def draw_sheet(background) -> Image.Image:
    """Two rows of elements; a balloon whose thin string must stay attached; specks to drop."""
    img = Image.new("RGB", (1200, 800), background)
    d = ImageDraw.Draw(img)
    d.ellipse([60, 60, 300, 300], fill=PINK)                       # row 1, piece 1
    d.ellipse([440, 40, 600, 220], fill=MINT)                      # row 1, piece 2: balloon
    d.line([520, 220, 530, 360], fill=(150, 120, 110), width=3)    #   ...with its string
    d.polygon([(900, 60), (960, 200), (1100, 200), (990, 280), (1030, 380), (900, 300)], fill=GOLD)  # piece 3
    d.rectangle([80, 480, 420, 560], fill=BLUE)                    # row 2, piece 4: L-shape
    d.rectangle([80, 480, 160, 760], fill=BLUE)
    d.ellipse([220, 610, 400, 760], fill=CREAM)                    # piece 5 inside the L's bbox
    d.ellipse([700, 520, 980, 740], fill=PINK)                     # piece 6
    for x, y in ((640, 420), (1150, 760), (30, 420)):              # specks
        d.ellipse([x, y, x + 3, y + 3], fill=GOLD)
    return img


def pieces(out: Path) -> list[Path]:
    return sorted(out.glob("piece-*.png"))


@pytest.mark.parametrize("backdrop", ["magenta", "transparent"])
def test_splits_sheet_into_reading_order_pieces(tmp_path, backdrop):
    sheet = tmp_path / "sheet.png"
    if backdrop == "magenta":
        draw_sheet(MAGENTA).save(sheet)
    else:
        rgb = draw_sheet(MAGENTA)
        arr = np.asarray(rgb.convert("RGBA")).copy()
        is_bg = (arr[..., 0] == 255) & (arr[..., 1] == 0) & (arr[..., 2] == 255)
        arr[is_bg, 3] = 0
        Image.fromarray(arr).save(sheet)
    out = tmp_path / "pieces"
    proc = run(str(sheet), str(out))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    found = pieces(out)
    assert [p.name for p in found] == [f"piece-0{i}.png" for i in range(1, 7)]
    expected = [PINK, MINT, GOLD, BLUE, CREAM, PINK]
    for path, colour in zip(found, expected):
        rgba = np.asarray(Image.open(path).convert("RGBA")).astype(int)
        assert rgba[0, 0, 3] == 0 and rgba[-1, -1, 3] == 0 or rgba[..., 3].min() == 0
        solid = rgba[rgba[..., 3] > 250][:, :3]
        main = np.median(solid, axis=0)
        assert np.abs(main - colour).max() < 12, (path.name, main, colour)
        assert (np.minimum(solid[:, 0], solid[:, 2]) - solid[:, 1] < 80).all(), f"{path.name}: magenta fringe"
    balloon = Image.open(found[1])
    assert balloon.height > balloon.width * 1.5, "balloon string was split off"
    assert (out / "contact.png").is_file()


def test_piece_excludes_neighbouring_element_in_its_box(tmp_path):
    sheet = tmp_path / "sheet.png"
    draw_sheet(MAGENTA).save(sheet)
    out = tmp_path / "pieces"
    assert run(str(sheet), str(out)).returncode == 0
    cream = np.asarray(Image.open(pieces(out)[4]).convert("RGBA")).astype(int)
    opaque = cream[cream[..., 3] > 128][:, :3]
    blue_like = np.abs(opaque - BLUE).max(axis=1) < 30
    assert not blue_like.any(), "piece picked up pixels of the L-shape around it"
    l_shape = Image.open(pieces(out)[3]).convert("RGBA")
    assert l_shape.size[0] > 300 and l_shape.size[1] > 250


def test_checkerboard_sheet_is_supported(tmp_path):
    yy, xx = np.mgrid[0:800, 0:1200]
    board = np.where((((yy // 20) + (xx // 20)) % 2 == 0)[..., None], 255, 204).astype(np.uint8)
    img = Image.fromarray(np.repeat(board, 3, axis=2))
    d = ImageDraw.Draw(img)
    d.ellipse([100, 100, 400, 400], fill=PINK)
    d.ellipse([700, 300, 1000, 600], fill=GOLD)
    sheet = tmp_path / "sheet.png"
    img.save(sheet)
    out = tmp_path / "pieces"
    proc = run(str(sheet), str(out))
    assert proc.returncode == 0, proc.stderr
    assert len(pieces(out)) == 2


def test_blank_sheet_is_an_error(tmp_path):
    sheet = tmp_path / "sheet.png"
    Image.new("RGB", (600, 400), MAGENTA).save(sheet)
    proc = run(str(sheet), str(tmp_path / "pieces"))
    assert proc.returncode != 0
    assert "no element" in proc.stderr.lower()


def test_refuses_non_empty_output_dir(tmp_path):
    sheet = tmp_path / "sheet.png"
    draw_sheet(MAGENTA).save(sheet)
    out = tmp_path / "pieces"
    out.mkdir()
    (out / "keep.txt").write_text("mine")
    proc = run(str(sheet), str(out))
    assert proc.returncode != 0
    assert "empty" in proc.stderr.lower()
    assert (out / "keep.txt").read_text() == "mine"
    assert not pieces(out)
