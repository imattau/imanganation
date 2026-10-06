"""Location references: keys, registry, prompts, and the continuity score."""

from __future__ import annotations

import pytest
from PIL import Image

from manganation import locations as lc
from manganation.evaluate import suite as ev
from manganation.render.panel import prose_prompt
from manganation.script.schema import PanelSpec


@pytest.mark.parametrize("place, key", [
    ("School rooftop — late afternoon", "school rooftop"),
    ("School rooftop — cont.", "school rooftop"),
    ("the stairwell", "stairwell"),
    ("Kitchen (CONT'D)", "kitchen"),
    ("Harbor pier, dawn", "harbor pier"),
])
def test_a_place_is_keyed_without_its_time(place, key):
    assert lc.location_key(place) == key


def test_registry_round_trip(tmp_path):
    reg = lc.LocationRegistry.from_path(tmp_path)
    reg.root.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(reg.root / "school-rooftop.png")
    reg.add(lc.Location(key="school rooftop", name="School rooftop — late afternoon",
                        image="school-rooftop.png", details=["chain-link fence"]))
    again = lc.LocationRegistry.from_path(tmp_path)
    assert again.reference_path("School rooftop — cont.") == reg.root / "school-rooftop.png"
    assert again.reference_path("the stairwell") is None


def test_location_prompt_is_an_empty_establishing_view():
    text = lc.location_prompt("the stairwell", ["stairs", "indoors", "concrete walls"])
    assert "establishing view of the stairwell, with stairs, concrete walls" in text
    assert "An interior" in text and "No people" in text


def test_prose_prompt_points_at_the_location_image():
    spec = PanelSpec(page=1, panel=1, characters=["Akira"], scene_heading="School rooftop")
    text = prose_prompt(spec, {"Akira": ["1boy"]}, {"Akira": 1}, location_ref=2)
    assert "Setting: School rooftop, exactly the place shown in <image2>" in text


def test_continuity_compares_panels_set_in_one_place():
    def result(case, seed, tags, pal):
        return {"case": case, "seed": seed, "background": tags, "palette": pal}
    same = {"sky": 0.8, "chain-link fence": 0.9}
    results = [result("a", 1, same, [0.5, 0.5]), result("b", 1, same, [0.5, 0.5]),
               result("c", 1, {"stairs": 0.9}, [1.0, 0.0]),
               result("a", 2, same, [1.0, 0.0]), result("b", 2, {"stairs": 0.9}, [0.0, 1.0])]
    places = {"a": "roof", "b": "roof", "c": "stairwell"}
    scores = ev.continuity(results, places)
    assert scores["roof"] == [1.0, 0.0]  # identical at seed 1, nothing shared at seed 2
    assert "stairwell" not in scores     # one panel: nothing to compare


def test_background_tags_ignore_the_people():
    probs = {"sky": 0.6, "white hair": 0.9, "chain-link fence": 0.5, "night sky": 0.2,
             "skirt": 0.8}
    assert ev.background_tags(probs) == {"sky": 0.6, "chain-link fence": 0.5,
                                         "night sky": 0.2}
