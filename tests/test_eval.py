"""Render-accuracy eval: checks, suites, reports (no GPU, no tagger model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from manganation.config import REPO_ROOT, load_models
from manganation.evaluate import suite as ev
from manganation.evaluate.tagger import tag_name

# --- checks -------------------------------------------------------------------


def test_count_passes_on_the_expected_people():
    probs = {"1boy": 0.9, "1girl": 0.8, "solo": 0.01}
    assert ev.check_count(["1boy", "1girl"], probs, 0.35)["ok"]


@pytest.mark.parametrize("probs, detail", [
    ({"1boy": 0.9}, "no 1girl"),                               # someone was dropped
    ({"1boy": 0.9, "1girl": 0.9, "2boys": 0.6}, "saw 2boys"),  # someone was cloned
    ({"1boy": 0.9, "1girl": 0.9, "solo": 0.5}, "saw solo"),    # the pair merged
])
def test_count_fails_on_dropped_extra_or_merged_figures(probs, detail):
    result = ev.check_count(["1boy", "1girl"], probs, 0.35)
    assert not result["ok"] and detail in result["detail"]


def test_count_allows_the_tags_that_agree_with_it():
    assert ev.check_count(["1girl"], {"1girl": 0.9, "solo": 0.9}, 0.35)["ok"]
    assert ev.check_count(["2girls"], {"2girls": 0.9, "multiple girls": 0.9}, 0.35)["ok"]


def test_tag_alternatives_and_forbidden_tags():
    probs = {"smile": 0.6, "grin": 0.1}
    assert ev.check_tag("grin|smile", probs, 0.35)["ok"]
    assert not ev.check_tag("grin", probs, 0.35)["ok"]
    assert ev.check_tag("grin", probs, 0.35, forbid=True)["ok"]
    assert not ev.check_tag("grin|smile", probs, 0.35, forbid=True)["ok"]
    assert ev.check_tag("frown", probs, 0.35)["detail"] == "frown 0.00"


def test_tagger_names_match_prompt_spelling():
    assert tag_name("hands_on_hips") == "hands on hips"
    assert tag_name("^_^") == "^_^"


# --- the shipped suite ----------------------------------------------------------


def test_rooftop_suite_resolves_against_the_script():
    suite = ev.load_suite(REPO_ROOT / "config/eval/rooftop.yaml")
    specs = ev.panel_specs(suite)
    assert list(specs) == [c.id for c in suite.cases]
    drag = specs["p2-3-drags-down-stairs"]
    assert drag.characters == ["Yuki", "Akira"] and drag.location == "the stairwell"


def test_eval_tagger_is_registered_but_not_needed_to_render():
    from manganation import models_setup as ms
    from manganation.config import load_settings

    model, tags, detector = ms.evaluation(load_models())
    assert model.file.endswith(".onnx") and model.urls and model.sha256
    assert tags.file.endswith(".csv") and tags.urls and tags.sha256
    assert detector.file.endswith(".onnx") and detector.urls and detector.sha256
    assert model.file not in {m.file for m in ms.needed(load_settings(), load_models())}


# --- scoring a run --------------------------------------------------------------


class FakeTagger:
    vocabulary = {"1boy", "1girl", "solo", "sitting", "standing", "rooftop"}

    def __init__(self, by_image: dict[str, dict[str, float]]):
        self.by_image = by_image

    def tags(self, image):
        return self.by_image[Path(image).name]


def _suite(tmp_path: Path) -> ev.Suite:
    script = tmp_path / "script.md"
    script.write_text("PAGE 1\n[SCENE: Roof]\n\nPANEL 1\n[CHARACTERS: Akira]\n[ACTION]\n"
                      "Akira sits.\n")
    return ev.Suite(name="t", path=tmp_path / "t.yaml", script=script, identity=tmp_path,
                    seeds=[1, 2], cases=[ev.Case(id="sit", page=1, panel=1, frame=(64, 64),
                                                 tags=["sitting", "rooftp"],
                                                 forbid=["standing"], count=["1boy"])])


def test_score_all_writes_a_report_and_sheet(tmp_path):
    suite = _suite(tmp_path)
    for seed in suite.seeds:
        Image.new("RGB", (64, 64), "grey").save(tmp_path / f"sit-s{seed}.png")
    tagger = FakeTagger({
        "sit-s1.png": {"1boy": 0.9, "sitting": 0.8},                 # count, sitting
        "sit-s2.png": {"1boy": 0.9, "standing": 0.7, "solo": 0.9},   # count only
    })
    report = ev.score_all(suite, tmp_path, tagger, label="x", snapshot={})

    assert json.loads((tmp_path / "report.json").read_text())["label"] == "x"
    assert (tmp_path / "sheet.png").exists()
    s1, s2 = report["results"]
    assert [c["ok"] for c in s1["checks"]] == [True, True, False, True]
    assert [c["ok"] for c in s2["checks"]] == [True, False, False, False]
    assert report["summary"]["score"] == 0.5
    assert report["summary"]["by_check"]["sit: sitting"] == 0.5
    assert report["unknown_tags"] == ["rooftp"]  # a typo can never pass


def _report(scores_ok: list[list[bool]]) -> dict:
    results = [{"case": "sit", "score": sum(oks) / len(oks),
                "checks": [{"label": f"c{i}", "kind": "tag", "ok": ok}
                           for i, ok in enumerate(oks)]} for oks in scores_ok]
    return {"results": results, "summary": ev.summarize(results)}


def test_compare_lines_up_two_reports_with_a_noise_margin():
    a = _report([[True, False], [False, False], [True, True]])
    b = _report([[True, True], [True, False], [True, True]])
    rows = {row: rest for row, *rest in ev.compare(a, b)}
    before, after, margin = rows["overall"]
    assert (before, after) == (0.5, 0.833)
    assert margin is not None and margin > after - before  # 3 images: it's noise
    assert rows["sit: c1"][:2] == [0.333, 0.667]  # summaries round to 3 places


def test_noise_margin_shrinks_with_more_images():
    few = ev._margin([0.5, 1.0, 0.75], [0.75, 1.0, 0.5])
    many = ev._margin([0.5, 1.0, 0.75] * 8, [0.75, 1.0, 0.5] * 8)
    assert many < few / 2


# --- per-figure checks ------------------------------------------------------------


def test_nms_keeps_people_side_by_side_but_drops_duplicates():
    from manganation.evaluate.detector import Box, suppress

    a, dup = Box(0, 0, 100, 200, 0.9), Box(2, 0, 102, 200, 0.8)
    close = Box(40, 0, 140, 200, 0.85)  # a second person half behind the first
    assert suppress([a, dup, close], 0.7) == [a, close]


class FakeDetector:
    def __init__(self, boxes):
        self.boxes = boxes

    def figures(self, image):
        return self.boxes


class CropTagger:
    """Tags a crop by where it starts: in the black left half Akira-like, else Yuki."""
    vocabulary = {"1boy", "1girl", "brown hair", "skirt", "pants", "white hair"}

    def __init__(self, left, right):
        self.left, self.right = left, right

    def tags(self, image):
        return self.left if image.getpixel((0, 0)) == (0, 0, 0) else self.right


def _pair_suite(tmp_path):
    suite = _suite(tmp_path)
    suite.characters = {"Akira": {"tags": ["1boy", "brown hair"], "forbid": ["skirt"]},
                        "Yuki": {"tags": ["1girl", "white hair"], "forbid": ["pants"]}}
    return suite


def _two_tone(tmp_path):
    img = Image.new("RGB", (100, 50), "white")
    img.paste((0, 0, 0), (0, 0, 50, 50))  # left half black
    path = tmp_path / "pair.png"
    img.save(path)
    return path


def test_figures_are_matched_to_characters_and_bleed_fails(tmp_path):
    from manganation.evaluate.detector import Box

    suite, image = _pair_suite(tmp_path), _two_tone(tmp_path)
    boxes = [Box(0, 0, 50, 50, 0.9), Box(50, 0, 100, 50, 0.9)]
    akira = {"1boy": 0.9, "brown hair": 0.9, "skirt": 0.8}   # wearing Yuki's skirt
    yuki = {"1girl": 0.9, "white hair": 0.9}
    checks, figures = ev.score_figures(image, ["Yuki", "Akira"], suite,
                                       CropTagger(akira, yuki), FakeDetector(boxes))
    assert [f["character"] for f in figures] == ["Akira", "Yuki"]
    failed = [c["label"] for c in checks if not c["ok"]]
    assert failed == ["Akira: not skirt"]


def test_a_clone_or_a_missing_figure_fails(tmp_path):
    from manganation.evaluate.detector import Box

    suite, image = _pair_suite(tmp_path), _two_tone(tmp_path)
    yuki = {"1girl": 0.9, "white hair": 0.9}
    checks, _ = ev.score_figures(image, ["Yuki", "Akira"], suite, CropTagger(yuki, yuki),
                                 FakeDetector([Box(50, 0, 100, 50, 0.9)]))
    assert checks[0] == {"label": "figures: 2", "kind": "figures", "ok": False,
                         "detail": "found 1"}
    assert {c["detail"] for c in checks if c["label"].startswith("Akira")} == {"no figure"}


def test_an_overlapped_figure_skips_its_forbids(tmp_path):
    from manganation.evaluate.detector import Box

    suite, image = _pair_suite(tmp_path), _two_tone(tmp_path)
    # Akira half behind Yuki: his crop shows her skirt, which says nothing about him
    boxes = [Box(0, 0, 80, 50, 0.9), Box(50, 0, 100, 50, 0.9)]
    akira = {"1boy": 0.9, "brown hair": 0.9, "skirt": 0.8}
    yuki = {"1girl": 0.9, "white hair": 0.9}
    checks, figures = ev.score_figures(image, ["Yuki", "Akira"], suite,
                                       CropTagger(akira, yuki), FakeDetector(boxes))
    assert figures[0]["overlapped"] and all(c["ok"] for c in checks)
    assert "Akira: not skirt" not in [c["label"] for c in checks]
