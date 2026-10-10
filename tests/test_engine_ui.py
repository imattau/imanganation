"""Render Engine dialog logic (no GIMP): the project's choice and what's shown."""

from __future__ import annotations

import json

import jsonschema

from gimp.imanganation import engine_ui
from manganation.config import REPO_ROOT

QWEN = {"id": "qwen_image_21", "name": "Qwen-Image 2.1", "speed": "~45 s a panel",
        "licence": "Qwen Research License (non-commercial)", "commercial": False,
        "licence_url": "https://example/LICENSE", "installed": False,
        "missing": ["qwen.safetensors"], "missing_bytes": 17_300_000_000, "install": None}
SDXL = {"id": "sdxl", "name": "NoobAI-XL (default)", "commercial": True,
        "licence": "FAIPL", "installed": True, "missing": [], "install": None}
FACE = {"id": "face_pass", "name": "Face pass", "commercial": True, "installed": True}


def test_a_project_that_never_chose_sends_nothing():
    manifest = {"project": {"id": "prj_abc123"}}
    assert engine_ui.project_render(manifest) == {"engine": "sdxl", "face_pass": False}
    assert engine_ui.job_options(manifest) == {}  # the engine's settings apply
    engine_ui.set_project_render(manifest, "qwen_image_21", True)
    assert engine_ui.job_options(manifest) == {"engine": "qwen_image_21", "face_pass": True}


def test_choice_validates_against_the_container_schema():
    schema = json.loads((REPO_ROOT / "docs/project-container.schema.json").read_text())
    project_schema = schema["properties"]["project"]
    project = {"id": "prj_abc123", "title": "t", "reading_order": "rtl",
               "created": "2026-10-07T00:00:00Z", "modified": "2026-10-07T00:00:00Z"}
    engine_ui.set_project_render({"project": project}, "z_anime", False)
    jsonschema.validate(project, project_schema)
    project["render"]["engine"] = "dalle"
    try:
        jsonschema.validate(project, project_schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("an unknown engine must not validate")


def test_rows_states_and_the_non_commercial_warning():
    report = {"engines": [SDXL, QWEN, FACE]}
    assert [r["id"] for r in engine_ui.engines(report)] == ["sdxl", "qwen_image_21"]
    assert engine_ui.face_pass(report)["id"] == "face_pass"
    assert engine_ui.state(SDXL) == "Installed"
    assert engine_ui.state(QWEN) == "Not installed: 17.3 GB to download"
    assert "NON-COMMERCIAL" in engine_ui.licence_line(QWEN)
    assert "commercial use allowed" in engine_ui.licence_line(SDXL)
    assert "non-commercial use only" in engine_ui.warning(QWEN)
    assert engine_ui.warning(SDXL) == ""
    assert not engine_ui.can_choose(QWEN) and engine_ui.can_install(QWEN)
    running = {**QWEN, "install": {"state": "running", "done": 4.3e9, "total": 17.3e9}}
    assert engine_ui.state(running) == "Installing 24%"
    assert not engine_ui.can_install(running)
    failed = {**QWEN, "install": {"state": "error", "errors": ["peer closed"]}}
    assert engine_ui.state(failed) == "Install failed: peer closed"


def test_a_projects_look_travels_with_its_jobs_and_designs():
    schema = json.loads((REPO_ROOT / "docs/project-container.schema.json").read_text())
    project = {"id": "prj_abc123", "title": "t", "reading_order": "rtl",
               "created": "2026-10-07T00:00:00Z", "modified": "2026-10-07T00:00:00Z"}
    manifest = {"project": project}
    assert engine_ui.project_style(manifest) == {"preset": "default", "text": ""}
    assert engine_ui.style_options(manifest) == {}
    engine_ui.set_project_render(manifest, "sdxl", False,
                                 {"preset": "retro_90s", "text": "  autumn   palette "})
    style = {"preset": "retro_90s", "text": "autumn palette"}
    assert project["render"]["style"] == style
    assert engine_ui.job_options(manifest) == {"engine": "sdxl", "face_pass": False,
                                               "style": style}
    assert engine_ui.style_options(manifest) == {"style": style}
    jsonschema.validate(project, schema["properties"]["project"])
    engine_ui.set_project_render(manifest, "qwen_image_21", True)  # keeps the look
    assert project["render"]["style"] == style
    engine_ui.set_project_render(manifest, "sdxl", False, {"preset": "default", "text": ""})
    assert "style" not in project["render"]  # the default look is no look of its own
    assert engine_ui.style_note({"summary": "Soft.", "measured": "shows in 9/10"}) == (
        "Soft. Measured: shows in 9/10")


def test_the_adult_content_flag_is_stored_and_sent_without_choosing_an_engine():
    schema = json.loads((REPO_ROOT / "docs/project-container.schema.json").read_text())
    project = {"id": "prj_abc123", "title": "t", "reading_order": "rtl",
               "created": "2026-10-07T00:00:00Z", "modified": "2026-10-07T00:00:00Z"}
    manifest = {"project": project}
    engine_ui.set_project_nsfw(manifest, False)
    assert "render" not in project and engine_ui.job_options(manifest) == {}
    engine_ui.set_project_nsfw(manifest, True)
    style = {"preset": "default", "text": "", "nsfw": True}
    assert project["render"] == {"style": style}
    assert engine_ui.job_options(manifest) == {"style": style}
    assert engine_ui.design_options(manifest) == {"style": style}
    jsonschema.validate(project, schema["properties"]["project"])
    engine_ui.set_project_render(manifest, "z_anime", False, engine_ui.project_style(manifest))
    assert project["render"]["style"] == style  # the engine dialog keeps the flag
    engine_ui.set_project_nsfw(manifest, False)
    assert "nsfw" not in project["render"].get("style", {})
