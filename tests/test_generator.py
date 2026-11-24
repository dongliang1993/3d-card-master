"""Generator contract tests for the 3d-card-master skill.

Covers ``scripts/create_card.py`` and the packaged template under
``assets/template`` (art, vendored three.js, licenses).
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "create_card.py"
TEMPLATE = SKILL / "assets" / "template"
PROJECT_LICENSE = SKILL / "LICENSE"

CARD_KEYS = {"title", "greeting", "message", "signature"}


def run_generator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def make_card(tmp_path: Path, *args: str) -> Path:
    output = tmp_path / "my-card"
    proc = run_generator(str(output), *args)
    assert proc.returncode == 0, f"generator failed:\n{proc.stderr}\n{proc.stdout}"
    return output


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def iter_json_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for item in node:
            yield from iter_json_strings(item)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from iter_json_strings(value)


# ── CLI contract ────────────────────────────────────────────────────────────


def test_requires_output_argument(tmp_path):
    proc = run_generator()
    assert proc.returncode != 0
    assert proc.stderr.strip()


def test_creates_card_json_with_four_editable_keys(tmp_path):
    output = make_card(
        tmp_path,
        "--title", "春日来信",
        "--greeting", "亲爱的妈妈，",
        "--message", "愿你日日有花。\n写给妈妈",
        "--signature", "爱你的我",
    )
    card = load_json(output / "card.json")
    assert set(card) == CARD_KEYS
    assert card["title"] == "春日来信"
    assert card["greeting"] == "亲爱的妈妈，"
    assert card["message"] == "愿你日日有花。\n写给妈妈"
    assert card["signature"] == "爱你的我"


def test_card_json_is_utf8_and_human_editable(tmp_path):
    output = make_card(tmp_path, "--title", "冬至")
    raw = (output / "card.json").read_bytes()
    card = raw.decode("utf-8")  # valid UTF-8
    assert "冬至" in card  # ensure_ascii=False: raw CJK, not \u escapes
    assert "\\u" not in card


def test_default_card_json_is_a_real_sample_without_personal_name(tmp_path):
    output = make_card(tmp_path)
    card = load_json(output / "card.json")
    assert set(card) == CARD_KEYS
    for key in ("title", "greeting", "message"):
        assert card[key].strip(), f"{key} default must be a visible sample"
    assert card["signature"] == "", "signature defaults empty"
    for text in card.values():
        assert "dongliang" not in text and "亮" not in text


def test_explicit_empty_values_beat_defaults(tmp_path):
    output = make_card(tmp_path, "--title", "", "--greeting", "", "--message", "", "--signature", "")
    card = load_json(output / "card.json")
    assert card == {"title": "", "greeting": "", "message": "", "signature": ""}


def test_creates_missing_output_parents(tmp_path):
    output = tmp_path / "deep" / "nested" / "card"
    proc = run_generator(str(output))
    assert proc.returncode == 0, proc.stderr
    assert (output / "card.json").is_file()


def test_accepts_existing_empty_output_dir(tmp_path):
    output = tmp_path / "card"
    output.mkdir()
    proc = run_generator(str(output))
    assert proc.returncode == 0, proc.stderr
    assert (output / "card.json").is_file()


# ── never clobber / never recurse ───────────────────────────────────────────


def test_refuses_non_empty_output_dir(tmp_path):
    output = tmp_path / "card"
    output.mkdir()
    keeper = output / "keep.txt"
    keeper.write_text("precious", encoding="utf-8")
    before = keeper.read_text(encoding="utf-8")
    proc = run_generator(str(output))
    assert proc.returncode != 0
    assert "refus" in (proc.stderr + proc.stdout).lower() or "exist" in (
        proc.stderr + proc.stdout
    ).lower()
    assert keeper.read_text(encoding="utf-8") == before
    assert not (output / "card.json").exists()


def test_refuses_output_on_existing_file(tmp_path):
    output = tmp_path / "card"
    output.write_text("i am a file", encoding="utf-8")
    proc = run_generator(str(output))
    assert proc.returncode != 0
    assert output.read_text(encoding="utf-8") == "i am a file"


def test_refuses_output_inside_template():
    proc = run_generator(str(TEMPLATE / "nested"))
    assert proc.returncode != 0
    assert not (TEMPLATE / "nested").exists()


def test_refuses_template_inside_output(tmp_path):
    proc = run_generator(str(SKILL / "assets"))
    assert proc.returncode != 0
    assert not (SKILL / "assets" / "card.json").exists()


def test_does_not_touch_template_when_run_repeatedly(tmp_path):
    before = sorted(str(p.relative_to(TEMPLATE)) for p in TEMPLATE.rglob("*"))
    make_card(tmp_path)
    output2 = tmp_path / "another"
    proc = run_generator(str(output2))
    assert proc.returncode == 0, proc.stderr
    after = sorted(str(p.relative_to(TEMPLATE)) for p in TEMPLATE.rglob("*"))
    assert before == after


def test_preview_hint_quotes_paths(tmp_path):
    output = tmp_path / "my card"
    proc = run_generator(str(output))
    assert proc.returncode == 0, proc.stderr
    hint = next(line for line in proc.stdout.splitlines() if line.startswith("Preview:"))
    resolved = str(output.resolve())
    assert shlex.quote(resolved) in hint
    assert shlex.split(hint[len("Preview: "):])[-1] == resolved


# ── packaged template contents ──────────────────────────────────────────────


def test_template_packages_full_winter_moonlight_art():
    for rel in ("cover.webp", "cover-base.webp"):
        assert (TEMPLATE / "art" / rel).is_file(), rel
        assert (TEMPLATE / "art" / rel).stat().st_size > 10_000, rel
    elements = sorted(p.name for p in (TEMPLATE / "art" / "elements").glob("*.webp"))
    assert len(elements) >= 10, f"expected bundled layered elements, got {len(elements)}"
    baked = sorted(p.name for p in (TEMPLATE / "art" / "baked").glob("*.webp"))
    assert len(baked) >= 20, f"expected bundled baked maps, got {len(baked)}"


def test_layout_json_uses_local_art_paths():
    layout = load_json(TEMPLATE / "art" / "layout.json")
    assert layout["base"] == "./art/cover-base.webp"
    assert layout["cover"] == "./art/cover.webp"
    assert isinstance(layout["size"], list) and len(layout["size"]) == 2
    assert len(layout["items"]) >= 10
    for text in iter_json_strings(layout):
        assert "/assets/cards/winter-moonlight/" not in text
    ids = [it["id"] for it in layout["items"]]
    assert len(ids) == len(set(ids)), "layer ids must stay unique"


def test_baked_manifest_uses_local_art_paths():
    manifest = load_json(TEMPLATE / "art" / "baked" / "manifest.json")
    for text in iter_json_strings(manifest):
        assert "/assets/cards/winter-moonlight/" not in text
    assert any("./art/" in text for text in iter_json_strings(manifest))


def test_output_art_references_all_resolve(tmp_path):
    output = make_card(tmp_path)
    layout = load_json(output / "art" / "layout.json")
    refs = [layout["base"], layout["cover"]]
    refs += [it["src"] for it in layout["items"]]
    refs += [m["src"] for m in layout.get("motifs", [])]
    for ref in refs:
        assert ref.startswith("./art/"), ref
        assert (output / ref).is_file(), f"missing referenced art: {ref}"
    manifest = load_json(output / "art" / "baked" / "manifest.json")
    baked_dir = output / "art" / "baked"
    for entry in manifest["entries"]:
        for field in ("normal", "surface", "src"):
            if field in entry:
                assert (baked_dir / entry[field]).is_file(), f"{entry['name']}: {entry[field]}"
        for text in iter_json_strings(entry):
            if text.startswith("./"):
                path = text[len("layer|"):] if text.startswith("layer|") else text
                path = path.split("|", 1)[0]
                assert (output / path).is_file(), text


def test_output_has_no_original_source_art_references(tmp_path):
    output = make_card(tmp_path)
    for path in output.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "/assets/cards/" not in text, f"{path.name} still points at source art"
        assert "public/assets" not in text, f"{path.name} references the project tree"


def test_vendor_packages_official_local_three_js():
    module = TEMPLATE / "vendor" / "three.module.min.js"
    core = TEMPLATE / "vendor" / "three.core.min.js"
    assert module.is_file() and core.is_file()
    text = module.read_text(encoding="utf-8")
    assert 'from"./three.core.min.js"' in text, "module must import the packaged core"


def test_vendor_carries_mit_license():
    vendor_license = TEMPLATE / "vendor" / "LICENSE"
    assert vendor_license.is_file()
    text = vendor_license.read_text(encoding="utf-8")
    assert "MIT License" in text


def test_template_license_copies_project_license_with_notice():
    text = (TEMPLATE / "LICENSE").read_text(encoding="utf-8")
    assert "Required Notice" in text
    assert "PolyForm Noncommercial" in text
    assert text == PROJECT_LICENSE.read_text(encoding="utf-8")


def test_output_contains_license_and_vendor(tmp_path):
    output = make_card(tmp_path)
    assert (output / "LICENSE").is_file()
    assert (output / "vendor" / "three.module.min.js").is_file()
    assert (output / "vendor" / "three.core.min.js").is_file()
    assert (output / "vendor" / "LICENSE").is_file()
    assert (output / "art" / "layout.json").is_file()


def test_output_ui_files_copied_when_present_in_template(tmp_path):
    output = make_card(tmp_path)
    for name in ("index.html", "scene.js", "app.js"):
        assert (output / name).read_bytes() == (TEMPLATE / name).read_bytes(), name


def test_unicode_args_survive_subprocess(tmp_path):
    output = make_card(
        tmp_path, "--message", "第一行\n第二行 <em>强调</em>", "--signature", "—— 小明"
    )
    card = load_json(output / "card.json")
    assert card["message"] == "第一行\n第二行 <em>强调</em>"
    assert card["signature"] == "—— 小明"


@pytest.mark.skipif(not os.access(SKILL, os.W_OK), reason="repo not writable")
def test_relative_output_argument_resolved_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proc = run_generator("card-here")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "card-here" / "card.json").is_file()


REPO_URL = "https://github.com/dongliang1993/3d-card-master"


def test_success_output_invites_star_once(tmp_path):
    proc = run_generator(str(tmp_path / "starred"))
    assert proc.returncode == 0
    star_lines = [line for line in proc.stdout.splitlines() if REPO_URL in line]
    assert len(star_lines) == 1
    assert "Star" in star_lines[0]


def test_failure_output_has_no_star_invite(tmp_path):
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "keep.txt").write_text("x")
    proc = run_generator(str(tmp_path / "busy"))
    assert proc.returncode != 0
    assert REPO_URL not in proc.stdout + proc.stderr


def test_template_carries_repo_credit_link():
    html = (TEMPLATE / "index.html").read_text(encoding="utf-8")
    assert f'href="{REPO_URL}"' in html
    assert 'id="credit"' in html
    assert 'rel="noopener noreferrer"' in html


# ── background music ────────────────────────────────────────────────────────


def write_tone(path: Path, seconds: float = 1.0) -> Path:
    import math
    import struct
    import wave

    rate = 8000
    frames = b"".join(
        struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
    return path


def test_music_is_copied_and_referenced(tmp_path):
    tone = write_tone(tmp_path / "我的歌.WAV")
    output = make_card(tmp_path, "--music", str(tone))
    copied = output / "music" / "bgm.wav"
    assert copied.read_bytes() == tone.read_bytes()
    card = load_json(output / "card.json")
    assert card["music"] == "./music/bgm.wav"
    assert CARD_KEYS <= set(card)


def test_music_rejects_unsupported_extension(tmp_path):
    bad = tmp_path / "song.txt"
    bad.write_text("not audio")
    output = tmp_path / "card"
    proc = run_generator(str(output), "--music", str(bad))
    assert proc.returncode != 0
    assert "music" in proc.stderr.lower()
    assert not output.exists()


def test_music_rejects_missing_file(tmp_path):
    output = tmp_path / "card"
    proc = run_generator(str(output), "--music", str(tmp_path / "missing.mp3"))
    assert proc.returncode != 0
    assert "music" in proc.stderr.lower()
    assert not output.exists()


def test_card_without_music_has_no_music_dir(tmp_path):
    output = make_card(tmp_path)
    assert not (output / "music").exists()
    assert "music" not in load_json(output / "card.json")
