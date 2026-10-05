"""Panel staging: the LLM's answer -> clean tags, cached (no real LLM)."""

from __future__ import annotations

from manganation.render import staging
from manganation.script.schema import PanelSpec

SPEC = PanelSpec(page=1, panel=1, characters=["Yuki", "Akira"], camera="two-shot",
                 action="Yuki stands over him, hands on her hips. Kenji calls off-panel.")


def test_answer_is_cleaned_to_tags():
    answer = {
        "characters": [
            {"name": "yuki", "pose": ["Standing", "hands_on_own_hips", "Yuki stands"],
             "expression": ["grin", "grin"]},
            {"name": "Kenji", "pose": ["shouting"], "expression": []},  # off-panel
        ],
        "shared": ["she grabs his wrist and pulls him along the corridor", "bento"],
        "setting": ["rooftop", "indoors", "outdoors", "sunset"],
    }
    s = staging.from_answer(answer, SPEC)
    assert s.characters == {"Yuki": {"pose": ["standing", "hands on own hips"],
                                     "expression": ["grin"]}}
    assert s.pose("Akira") == [] and s.expression("Akira") == []
    assert s.shared == ["bento"]                    # sentences dropped
    assert s.setting == ["rooftop", "sunset"]       # a contradiction helps neither


def test_stage_asks_once_then_uses_the_cache(tmp_path):
    class FakeLLM:
        calls = 0

        def chat_json(self, messages, *, schema=None, timeout=0):
            FakeLLM.calls += 1
            assert messages[-1]["content"].startswith("Visible characters: Yuki, Akira")
            return {"characters": [{"name": "Akira", "pose": ["sitting"],
                                    "expression": []}],
                    "shared": [], "setting": ["rooftop", "outdoors"]}

    first = staging.stage(SPEC, "Roof", client=FakeLLM(), cache=tmp_path)
    again = staging.stage(SPEC, "Roof", client=FakeLLM(), cache=tmp_path)
    assert first == again and first.pose("Akira") == ["sitting"]
    assert FakeLLM.calls == 1
    edited = SPEC.model_copy(update={"action": "Yuki sits beside him."})
    staging.stage(edited, "Roof", client=FakeLLM(), cache=tmp_path)
    assert FakeLLM.calls == 2  # new text, new tags
