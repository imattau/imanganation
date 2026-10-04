"""Project container: the worked example is consistent, and broken references are caught."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from manganation.project_container import integrity_errors
from manganation.script.schema import PanelSpec

DOCS = Path(__file__).resolve().parents[1] / "docs"
EXAMPLE = json.loads((DOCS / "project-container.example.json").read_text())


def _broken(mutate):
    doc = copy.deepcopy(EXAMPLE)
    mutate(doc)
    return integrity_errors(doc)


def test_example_is_consistent():
    assert integrity_errors(EXAMPLE) == []


def test_example_panels_map_onto_the_engine_panelspec():
    """The container's panel fields are PanelSpec's, so the engine can take them inline."""
    for p in EXAMPLE["panels"]:
        spec = PanelSpec(page=p["label"]["page"], panel=p["label"]["panel"],
                         characters=[c["name"] for c in p["characters"]],
                         **{k: p[k] for k in ("scene_heading", "location", "action", "camera",
                                              "expressions", "dialogue", "sfx", "notes",
                                              "flashback", "aspect_ratio", "seed")})
        assert spec.action == p["action"]


def test_duplicate_script_labels_are_fine_ids_are_not():
    labels = [(p["label"]["page"], p["label"]["panel"]) for p in EXAMPLE["panels"]]
    assert labels.count((2, 1)) == 2  # legal: labels are display only
    errs = _broken(lambda d: d["panels"][4].update(id=d["panels"][2]["id"]))
    assert any("used more than once" in e for e in errs)


def test_looping_take_history_is_caught():
    def loop(d):
        d["takes"]["tk_000001"]["parent"] = "tk_000003"
        d["takes"]["tk_000001"]["kind"] = "refine"
    assert any("loops" in e for e in _broken(loop))


def test_wrong_origin_and_dangling_parent_are_caught():
    assert any("origin is" in e for e in _broken(
        lambda d: d["takes"]["tk_000003"].update(origin="tk_000002")))
    assert any("unknown take" in e for e in _broken(
        lambda d: d["takes"]["tk_000002"].update(parent="tk_999999")))


def test_panel_take_page_and_cursor_references_are_checked():
    assert any("active_take" in e for e in _broken(
        lambda d: d["panels"][0].update(active_take="tk_000004")))
    assert any("unknown page" in e for e in _broken(
        lambda d: d["panels"][0]["placement"].update(page="pg_zzzzzz")))
    assert any("not listed by its panel" in e for e in _broken(
        lambda d: d["panels"][0]["takes"].remove("tk_000002")))
    assert any("cursor" in e for e in _broken(
        lambda d: d["cursor"].update(next_panel="pnl_gone00")))
    assert any("another panel" in e for e in _broken(
        lambda d: d["takes"]["tk_000004"].update(kind="refine", parent="tk_000001",
                                                origin="tk_000001")))


def test_missing_files_are_reported_with_a_root(tmp_path):
    errs = integrity_errors(EXAMPLE, root=tmp_path)
    assert "missing file: takes/pnl_r8k2m1-tk_000001.png" in errs
    assert "missing file: masks/tk_000003.png" in errs
    assert "missing file: script/rooftop.md" in errs
