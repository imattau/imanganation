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

    model, tags = ms.evaluation(load_models())
    assert model.file.endswith(".onnx") and model.urls and model.sha256
    assert tags.file.endswith(".csv") and tags.urls
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


def test_compare_lines_up_two_reports():
    a = {"summary": {"score": 0.5, "by_kind": {"tag": 0.5}, "by_case": {"sit": 0.5},
                     "by_check": {"sit: sitting": 0.5}}}
    b = {"summary": {"score": 0.75, "by_kind": {"tag": 1.0}, "by_case": {"sit": 0.75},
                     "by_check": {"sit: sitting": 1.0, "sit: new": 0.0}}}
    rows = ev.compare(a, b)
    assert rows[0] == ("overall", 0.5, 0.75)
    assert ("sit: new", None, 0.0) in rows
