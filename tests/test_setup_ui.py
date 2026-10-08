"""The Set Up Models dialog's text and button states, from GET /setup reports."""

from __future__ import annotations

from gimp.imanganation import setup_ui


def _report(states=("missing", "missing"), task=None, **extra):
    rows = [
        {"role": "checkpoint", "feature": "rendering (required)", "file": "noobaiXL.safetensors",
         "size": 7_105_349_958, "state": states[0], "license": "FAIR AI Public License 1.0-SD",
         "license_url": "https://freedevproject.org/faipl-1.0-sd/", "downloadable": True},
        {"role": "upscaler (realesrgan)", "feature": "hi-res upscale (Refine)",
         "file": "RealESRGAN_x4plus_anime_6B.pth", "size": 17_938_799, "state": states[1],
         "license": "BSD-3-Clause", "license_url": "https://x/LICENSE", "downloadable": True},
    ]
    missing = sum(r["size"] for r in rows if r["state"] != "present")
    return {"models": rows, "missing_bytes": missing, "free_bytes": 50_000_000_000,
            "ready": not missing, "task": task or {"kind": None, "state": "idle"},
            "comfy_paths_changed": False, **extra}


def test_a_fresh_install_asks_for_the_checkpoint_first():
    report = _report()
    assert setup_ui.headline(report) == ("Rendering needs the checkpoint first. 7.1 GB to "
                                         "download, 50.0 GB free.")
    assert setup_ui.actions(report) == {"download": "Download (7.1 GB)",
                                        "download_enabled": True, "link_enabled": True,
                                        "cancel_enabled": False}
    assert setup_ui.should_prompt(report)
    assert "FAIR AI Public License 1.0-SD" in setup_ui.licence_note(report)
    assert "BSD-3-Clause" in setup_ui.licence_note(report)
    assert [setup_ui.row_state(r, report["task"]) for r in report["models"]] == [
        "Missing", "Missing"]


def test_while_downloading_progress_and_buttons_follow_the_task():
    task = {"kind": "download", "state": "running", "current": "noobaiXL.safetensors",
            "done": 3_552_674_979, "total": 7_123_288_757, "file_done": 3_552_674_979,
            "file_total": 7_105_349_958, "finished": [], "errors": []}
    report = _report(task=task)
    assert setup_ui.headline(report) == "Downloading 3.6 GB of 7.1 GB…"
    fraction, text = setup_ui.progress(report)
    assert round(fraction, 2) == 0.5 and text == "noobaiXL.safetensors  49%"
    assert setup_ui.row_state(report["models"][0], task) == "Downloading 50%"
    assert setup_ui.row_state(report["models"][1], task) == "Missing"
    actions = setup_ui.actions(report)
    assert actions["cancel_enabled"] and not actions["download_enabled"]
    assert not actions["link_enabled"]
    assert not setup_ui.should_prompt(report)  # never pop up over a running download


def test_errors_pause_and_restart_hints():
    failed = {"kind": "download", "state": "error", "finished": ["RealESRGAN_x4plus_anime_6B.pth"],
              "errors": ["could not download noobaiXL.safetensors:\n  https://…: HTTP 503"]}
    report = _report(("missing", "present"), task=failed, comfy_paths_changed=True)
    assert setup_ui.row_state(report["models"][0], failed) == "Failed (see below)"
    lines = setup_ui.details(report)
    assert any("HTTP 503" in line for line in lines)
    assert any("Download again to retry" in line for line in lines)
    assert any("restart GIMP" in line for line in lines)
    paused = _report(task={"kind": "download", "state": "cancelled", "errors": []})
    assert setup_ui.details(paused) == [
        "Download paused. Download again to resume where it stopped."]


def test_linking_and_a_ready_install():
    linked = _report(("present", "present"),
                     task={"kind": "link", "state": "done", "errors": [],
                           "finished": ["noobaiXL.safetensors (hard link)"]})
    assert setup_ui.headline(linked) == "All models are ready."
    assert setup_ui.details(linked) == ["Linked 1 file: noobaiXL.safetensors (hard link)"]
    assert setup_ui.actions(linked)["download_enabled"] is False
    assert setup_ui.actions(linked)["link_enabled"] is False
    assert not setup_ui.should_prompt(linked) and setup_ui.licence_note(linked) == ""
    nothing = _report(task={"kind": "link", "state": "done", "errors": [], "finished": []})
    assert setup_ui.details(nothing) == ["No matching files found in that folder."]
    searching = _report(task={"kind": "link", "state": "running"})
    assert setup_ui.progress(searching) == (-1.0, "Checking files…")


def test_only_the_checkpoint_missing_opens_the_dialog_by_itself():
    assert not setup_ui.should_prompt(_report(("present", "missing")))
    assert not setup_ui.should_prompt(None)  # engine not up yet
    assert setup_ui.actions(None)["download_enabled"] is False
    damaged = _report(("wrong size", "present"))
    assert setup_ui.row_state(damaged["models"][0], damaged["task"]) == (
        "Damaged: will download again")


def _renderer_row(state="missing"):
    return {"role": "renderer", "feature": "renderer: ComfyUI and PyTorch for your GPU (required)",
            "file": "ComfyUI + PyTorch", "size": 5_500_000_000, "state": state,
            "license": "GPL-3.0 (ComfyUI), BSD-3-Clause (PyTorch), NVIDIA CUDA EULA",
            "license_url": "https://x/LICENSE", "downloadable": True}


def test_the_renderer_row_installs_first_with_its_own_progress():
    report = _report()
    report["models"].insert(0, _renderer_row())
    report["missing_bytes"] += 5_500_000_000
    assert setup_ui.headline(report).startswith(
        "Rendering needs the renderer and the checkpoint first. 12.6 GB to download")
    assert setup_ui.should_prompt(report)
    installing = {"kind": "download", "state": "running", "phase": "renderer",
                  "current": "Installing PyTorch (cu130); a few GB…", "errors": []}
    busy = {**report, "task": installing}
    assert setup_ui.row_state(report["models"][0], installing) == "Installing…"
    assert setup_ui.row_state(report["models"][1], installing) == "Missing"
    assert setup_ui.progress(busy) == (-1.0, "Installing PyTorch (cu130); a few GB…")
    assert setup_ui.headline(busy).startswith("Installing the renderer")
    failed = {"kind": "download", "state": "error", "phase": "models",
              "finished": ["noobaiXL.safetensors"],
              "errors": ["renderer: the NVIDIA driver is too old"]}
    assert setup_ui.row_state(report["models"][0], failed) == "Failed (see below)"
    done = {"kind": "download", "state": "done", "errors": [],
            "finished": ["renderer (2.14.1+cu130 | NVIDIA GeForce RTX 5060 Ti)"]}
    assert setup_ui.details({**report, "task": done})[0] == (
        "The renderer is installed and starts on your GPU: "
        "2.14.1+cu130 | NVIDIA GeForce RTX 5060 Ti.")
    ready = _report(("present", "present"))
    ready["models"].insert(0, _renderer_row("missing"))
    assert setup_ui.should_prompt(ready)  # no renderer: can't render, so ask


def test_optional_engines_leave_out_the_default_and_size_what_is_missing():
    report = {"engines": [
        {"id": "sdxl", "installed": True},
        {"id": "qwen_image_21", "installed": False, "missing_bytes": 17_300_000_000},
        {"id": "z_anime", "installed": True, "missing_bytes": 0},
        {"id": "face_pass", "installed": False, "missing_bytes": 6_900_000_000},
    ]}
    rows = setup_ui.optional_engines(report)
    assert [r["id"] for r in rows] == ["qwen_image_21", "z_anime", "face_pass"]
    assert setup_ui.optional_size(rows[0]) == "17.3 GB"
    assert setup_ui.optional_size(rows[1]) == ""
    assert setup_ui.optional_engines(None) == []


def test_use_files_i_have_stays_available_while_an_optional_engine_is_missing():
    ready = _report(("present", "present"))
    engines = {"engines": [
        {"id": "sdxl", "installed": True, "missing": []},
        {"id": "qwen_image_21", "installed": False, "missing": ["qwen.safetensors"],
         "missing_bytes": 17_300_000_000}]}
    assert setup_ui.actions(ready)["link_enabled"] is False
    assert setup_ui.actions(ready, engines)["link_enabled"] is True
    engines["engines"][1].update(installed=True, missing=[])
    assert setup_ui.actions(ready, engines)["link_enabled"] is False
