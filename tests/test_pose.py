"""Pose library: the rig, the library file, composing figures, the graph and the render."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PIL import Image

from manganation.pose import library
from manganation.pose.draw import draw_figures
from manganation.pose.rig import (JOINT_INDEX, L_WRI, NOSE, R_WRI, Pose, PoseError, bounds,
                                  hold, place, skeleton)
from manganation.render import graphs
from manganation.script.schema import PanelSpec


def test_library_loads_and_every_reference_composes():
    lib = library.load_library()
    assert len(lib["poses"]) >= 15 and lib["pairs"]
    for ref, text in library.catalogue().items():
        assert text
        library.check_reference(ref)
    for ident in lib["poses"]:
        figures = library.compose({"a": ident}, {"a": (0.1, 0.1, 0.8, 0.8)}, 400, 600)
        assert len(figures[0].points) == 18


def test_every_pose_has_prompt_tags():
    for ident in library.load_library()["poses"]:
        assert library.tags_for(ident), ident


def test_skeleton_is_anatomically_ordered():
    pts = skeleton(Pose())
    head, neck, hip = pts[NOSE], pts[JOINT_INDEX["neck"]], pts[JOINT_INDEX["l_hip"]]
    assert head[1] < neck[1] < hip[1] < pts[JOINT_INDEX["l_ank"]][1]  # y is down
    # the figure's left is the viewer's right when it faces the camera
    assert pts[JOINT_INDEX["l_sho"]][0] > pts[JOINT_INDEX["r_sho"]][0]


def test_turning_away_hides_the_face():
    assert skeleton(Pose(yaw=180))[NOSE] is None
    assert skeleton(Pose(yaw=0))[NOSE] is not None


def test_mirror_swaps_sides():
    pose = Pose(yaw=40, l_arm=((90, 0), (90, 0)), r_arm=((10, 80), (5, 80)))
    plain, mirrored = skeleton(pose), skeleton(pose.mirrored())
    # the raised-forward arm moves from the left side to the right, x flips
    assert plain[L_WRI][0] == pytest.approx(-mirrored[R_WRI][0], abs=1e-6)
    assert plain[L_WRI][1] == pytest.approx(mirrored[R_WRI][1], abs=1e-6)


def test_place_fits_the_box_and_rests_on_its_bottom():
    fig = place(Pose(), (0.2, 0.1, 0.5, 0.8), 1000, 1000)
    xs = [p[0] for p in fig.points if p]
    ys = [p[1] for p in fig.points if p]
    assert min(xs) >= 200 - 1 and max(xs) <= 700 + 1
    assert max(ys) == pytest.approx(900, abs=1) and min(ys) >= 100 - 1


def test_hold_puts_the_wrist_on_the_target_and_keeps_bone_lengths():
    fig = place(Pose(), (0.1, 0.1, 0.5, 0.8), 800, 800)
    sho, elb, wri = (JOINT_INDEX[n] for n in ("l_sho", "l_elb", "l_wri"))
    upper, lower = (math.dist(fig.points[sho], fig.points[elb]),
                    math.dist(fig.points[elb], fig.points[wri]))
    target = (fig.points[sho][0] + 90, fig.points[sho][1] + 60, fig.points[sho][2])
    assert hold(fig, "l", target) is False
    assert fig.points[wri] == pytest.approx(target)
    assert math.dist(fig.points[sho], fig.points[elb]) == pytest.approx(upper)
    assert math.dist(fig.points[elb], fig.points[wri]) == pytest.approx(lower)


def test_hold_out_of_reach_moves_the_figure_so_hands_meet():
    fig = place(Pose(), (0.1, 0.1, 0.3, 0.8), 800, 800)
    target = (780.0, 400.0, 0.0)
    assert hold(fig, "r", target) is True
    assert fig.points[JOINT_INDEX["r_wri"]] == pytest.approx(target)


def test_pair_pose_joins_the_two_wrists():
    refs = {"Yuki": "drag_by_wrist.lead", "Akira": "drag_by_wrist.follow"}
    figures = {f.name: f for f in library.compose(refs, {}, 500, 1000)}
    assert figures["Yuki"].points[JOINT_INDEX["r_wri"]] == pytest.approx(
        figures["Akira"].points[JOINT_INDEX["l_wri"]])


def test_mirrored_pair_still_joins_and_flips_sides():
    refs = {"Yuki": "drag_by_wrist.lead@mirror", "Akira": "drag_by_wrist.follow@mirror"}
    figures = {f.name: f for f in library.compose(refs, {}, 500, 1000)}
    assert figures["Yuki"].points[JOINT_INDEX["l_wri"]] == pytest.approx(
        figures["Akira"].points[JOINT_INDEX["r_wri"]])
    plain = {f.name: f for f in library.compose(
        {"Yuki": "drag_by_wrist.lead", "Akira": "drag_by_wrist.follow"}, {}, 500, 1000)}
    assert (plain["Yuki"].points[NOSE][0] < plain["Akira"].points[NOSE][0]) != (
        figures["Yuki"].points[NOSE][0] < figures["Akira"].points[NOSE][0])


def test_bad_references_are_refused():
    with pytest.raises(PoseError):
        library.check_reference("moonwalk")
    with pytest.raises(PoseError):
        library.check_reference("drag_by_wrist.bystander")
    with pytest.raises(PoseError, match="needs a character for: follow"):
        library.compose({"Yuki": "drag_by_wrist.lead"}, {}, 400, 400)
    with pytest.raises(PoseError, match="no region"):
        library.compose({"Yuki": "stand"}, {}, 400, 400)


def test_pose_fields_are_validated():
    with pytest.raises(PoseError, match="unknown fields"):
        Pose.from_dict("x", {"wiggle": 3})
    with pytest.raises(PoseError, match="must be"):
        Pose.from_dict("x", {"l_arm": [1, 2]})


def test_drawing_is_openpose_coloured_on_black():
    figures = library.compose({"a": "stand_hands_on_hips"}, {"a": (0, 0, 1, 1)}, 256, 384)
    image = draw_figures(figures, 256, 384)
    assert image.size == (256, 384) and image.getpixel((0, 0)) == (0, 0, 0)
    colours = {c for _, c in image.getcolors(1 << 20)}
    assert (255, 0, 0) in colours  # the neck-to-shoulder joint dot
    assert len(colours) > 10


def test_with_pose_controlnet_wraps_the_sampler_conditioning():
    g = graphs.txt2img(ckpt="c", prompt="p", negative="n", width=64, height=64, seed=1,
                       prefix="x")
    out = graphs.with_pose_controlnet(g, image="skel.png", controlnet="noob_openpose.safetensors")
    apply = out["pose_apply"]["inputs"]
    assert apply["positive"] == ["2", 0] and apply["strength"] == 0.8
    assert out["5"]["inputs"]["positive"] == ["pose_apply", 0]
    assert out["pose_image"]["inputs"]["image"] == "skel.png"
    assert g["5"]["inputs"]["positive"] == ["2", 0]  # the input graph is untouched


def test_spec_carries_poses_through_a_container_panel():
    from manganation.project_container import panel_to_spec

    spec, _ = panel_to_spec({"label": {"page": 1, "panel": 1},
                             "characters": [{"name": "Yuki"}],
                             "poses": {"Yuki": "wave"}})
    assert spec.poses == {"Yuki": "wave"}
    assert PanelSpec(page=1, panel=1).poses == {}


# ---- through the renderer ------------------------------------------------------------

class _Fake:
    def __init__(self):
        self.graphs, self.uploads = [], {}

    def is_up(self):
        return True

    def upload_image(self, path):
        with Image.open(path) as im:
            self.uploads[Path(path).name] = im.size
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        return [b"x"]


def _render(tmp_path, monkeypatch, poses, characters=("Yuki", "Akira"), with_model=True,
            engine="sdxl"):
    from manganation.characters.registry import CharacterRegistry
    from manganation.render import panel as P

    reg = CharacterRegistry.from_path(tmp_path / "cast")
    for name in characters:
        Image.new("RGB", (8, 8)).save(tmp_path / f"{name}.png")
        reg.add_user_reference(name, str(tmp_path / f"{name}.png"), "base")
    models = tmp_path / "models"
    (models / "controlnet").mkdir(parents=True)
    if with_model:
        (models / "controlnet" / "noob_openpose.safetensors").write_bytes(b"x")
    monkeypatch.setattr("manganation.config.models_root", lambda *_: models)
    fake = _Fake()
    panel = {"id": "pnl_abc123", "characters": [{"name": c} for c in characters],
             "action": "talking", "poses": poses}
    result = P.render_inline(panel, "prj_test01", 500, 1000, seed=1, client=fake,
                             identity=tmp_path / "cast", outputs=tmp_path / "out",
                             engine=engine, face_pass=False)
    return result, fake.graphs[0], fake


def test_render_adds_the_pose_controlnet_at_canvas_size(tmp_path, monkeypatch):
    r, g, fake = _render(tmp_path, monkeypatch, {"Yuki": "drag_by_wrist.lead",
                                                 "akira": "drag_by_wrist.follow"})
    assert g["pose_model"]["inputs"]["control_net_name"] == "noob_openpose.safetensors"
    assert fake.uploads[g["pose_image"]["inputs"]["image"]] == (r.width, r.height)
    assert g["5"]["inputs"]["positive"] == ["pose_apply", 0]  # names match case-blind
    assert g["5"]["inputs"]["model"] == ["apply", 0]  # the regional references remain


def test_poses_are_skipped_with_a_warning_off_the_sdxl_engine(tmp_path, monkeypatch):
    r, g, _ = _render(tmp_path, monkeypatch, {"Yuki": "stand"}, characters=("Yuki",),
                      engine="qwen_image_21")
    assert "pose_apply" not in g
    assert any("poses need the sdxl engine" in w for w in r.warnings)


def test_no_poses_no_pose_controlnet(tmp_path, monkeypatch):
    _, g, _ = _render(tmp_path, monkeypatch, {})
    assert "pose_apply" not in g


def test_missing_model_and_bad_reference_say_what_to_do(tmp_path, monkeypatch):
    from manganation.render.panel import RenderError

    with pytest.raises(RenderError, match="setup --poses"):
        _render(tmp_path, monkeypatch, {"Yuki": "stand"}, characters=("Yuki",),
                with_model=False)
    with pytest.raises(RenderError, match="unknown pose .moonwalk."):
        (tmp_path / "b").mkdir()
        _render(tmp_path / "b", monkeypatch, {"Yuki": "moonwalk"}, characters=("Yuki",))
