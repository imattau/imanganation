"""manganation setup: which models, linking files the user has, resumable verified downloads."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from manganation import models_setup as ms
from manganation.config import load_models, load_settings

PAYLOAD = bytes(range(256)) * 4000  # ~1 MB, not a multiple of the chunk size


def _model(urls=("https://huggingface.co/a/b/resolve/r/m.safetensors",), payload=PAYLOAD):
    return ms.ModelFile(role="checkpoint", feature="rendering", file="m.safetensors",
                        subdir="checkpoints", size=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest(), urls=list(urls))


def _server(payload=PAYLOAD, *, ignore_range=False, status=None, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status:
            return httpx.Response(status)
        start = 0
        if "range" in request.headers and not ignore_range:
            start = int(request.headers["range"].split("=")[1].rstrip("-"))
            return httpx.Response(206, content=payload[start:])
        return httpx.Response(200, content=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_manifest_covers_everything_the_settings_need():
    models = ms.needed(load_settings(), load_models())
    assert [m.role for m in models][:3] == ["checkpoint", "ip-adapter (noob_mark1)",
                                             "clip vision"]  # most useful first
    for m in models:
        assert m.urls and m.size and len(m.sha256) == 64 and m.license, m.role
        assert all("/resolve/main/" not in u for u in m.urls)  # pinned revisions
    ipa = next(m for m in models if m.role.startswith("ip-adapter"))
    assert len(ipa.urls) == 2  # a fallback mirror for the third-party upload


def test_download_verifies_and_moves_into_place(tmp_path):
    model, done = _model(), []
    path = ms.download(model, tmp_path, client=_server(),
                       progress=lambda d, n: done.append((d, n)))
    assert path == tmp_path / "checkpoints/m.safetensors" and path.read_bytes() == PAYLOAD
    assert done[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not path.with_name("m.safetensors.part").exists()


def test_an_interrupted_download_resumes_with_a_range_request(tmp_path):
    model, seen = _model(), []
    part = tmp_path / "checkpoints/m.safetensors.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:300_000])
    path = ms.download(model, tmp_path, client=_server(seen=seen))
    assert seen[0].headers["range"] == "bytes=300000-"
    assert path.read_bytes() == PAYLOAD


def test_a_server_that_ignores_range_starts_over(tmp_path):
    model = _model()
    part = tmp_path / "checkpoints/m.safetensors.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:300_000])
    path = ms.download(model, tmp_path, client=_server(ignore_range=True))
    assert path.read_bytes() == PAYLOAD


def test_a_corrupt_download_is_discarded_and_the_mirror_tried(tmp_path):
    good = "https://mirror.example/m.safetensors"
    bad = _model(urls=("https://huggingface.co/x/resolve/r/m.safetensors", good))

    def handler(request):
        body = PAYLOAD if request.url.host == "mirror.example" else b"x" * len(PAYLOAD)
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = ms.download(bad, tmp_path, client=client)
    assert path.read_bytes() == PAYLOAD

    only_bad = _model(urls=("https://huggingface.co/x/resolve/r/m.safetensors",))
    with pytest.raises(ms.SetupError, match="SHA-256 mismatch"):
        ms.download(only_bad, tmp_path / "b", client=client)
    assert not (tmp_path / "b/checkpoints/m.safetensors").exists()
    assert not (tmp_path / "b/checkpoints/m.safetensors.part").exists()  # can't resume junk


def test_http_errors_are_reported_per_source(tmp_path):
    with pytest.raises(ms.SetupError, match="HTTP 404"):
        ms.download(_model(), tmp_path, client=_server(status=404))
    with pytest.raises(ms.SetupError, match="no download source"):
        ms.download(_model(urls=()), tmp_path)


def test_hf_token_goes_to_hugging_face_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    seen = []
    ms.download(_model(), tmp_path / "a", client=_server(seen=seen))
    assert seen[-1].headers["authorization"] == "Bearer hf_secret"
    ms.download(_model(urls=("https://mirror.example/m",)), tmp_path / "b",
                client=_server(seen=seen))
    assert "authorization" not in seen[-1].headers


def test_hf_endpoint_redirects_hugging_face_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.example/")
    seen = []
    ms.download(_model(), tmp_path, client=_server(seen=seen))
    assert str(seen[0].url) == "https://hf-mirror.example/a/b/resolve/r/m.safetensors"


def test_state_checks_size_and_optionally_hash(tmp_path):
    model = _model()
    assert ms.state(model, tmp_path) == "missing"
    path = model.path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(PAYLOAD[:-1])
    assert ms.state(model, tmp_path) == "wrong size"
    path.write_bytes(bytes(len(PAYLOAD)))
    assert ms.state(model, tmp_path) == "present"  # size alone is cheap
    assert ms.state(model, tmp_path, verify=True) == "corrupt"


def test_existing_files_are_found_by_content_and_linked(tmp_path):
    model = _model()
    library = tmp_path / "ComfyUI/models/checkpoints"
    library.mkdir(parents=True)
    (library / "noobai-renamed.safetensors").write_bytes(PAYLOAD)  # any name
    (library / "same-size-other.safetensors").write_bytes(bytes(len(PAYLOAD)))
    found = ms.find_existing([model], [tmp_path / "ComfyUI"])
    assert found == {"checkpoint": library / "noobai-renamed.safetensors"}
    root = tmp_path / "models"
    assert ms.link_into_place(found["checkpoint"], model.path(root)) == "hard link"
    assert ms.state(model, root, verify=True) == "present"


def test_comfy_paths_follow_the_models_folder(tmp_path):
    config = tmp_path / "paths.yaml"
    config.write_text("imanganation:\n    base_path: /home/someone-else/models\n")
    assert ms.write_comfy_paths(config, tmp_path / "models")
    text = config.read_text()
    assert f"base_path: {(tmp_path / 'models').resolve()}" in text
    for key in ("checkpoints", "ipadapter", "clip_vision: ipadapter", "controlnet",
                "upscale_models"):
        assert key in text
    assert not ms.write_comfy_paths(config, tmp_path / "models")  # already right


def test_cli_check_reports_missing_files_and_exits_nonzero(tmp_path, monkeypatch):
    from manganation import cli, config

    settings = load_settings().model_copy(deep=True)
    settings.paths.models_dir = str(tmp_path / "models")
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # setup notes the engine's home
    result = CliRunner().invoke(cli.app, ["setup", "--check"], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "missing" in result.output and "to download" in result.output
    assert "FAIR AI Public License" in result.output
    assert not Path(tmp_path / "models").exists()  # --check changes nothing
    assert (tmp_path / "home/.config/imanganation/engine-home").read_text().strip() == str(
        config.REPO_ROOT)  # how a packaged GIMP finds this checkout


def test_cli_engine_adds_an_optional_engines_files(tmp_path, monkeypatch):
    from manganation import cli, config

    settings = load_settings().model_copy(deep=True)
    settings.paths.models_dir = str(tmp_path / "models")
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env = {"COLUMNS": "300"}
    plain = CliRunner().invoke(cli.app, ["setup", "--check"], env=env)
    assert "Optional engines (add with --engine): qwen_image_21" in plain.output
    assert "Qwen Research" not in plain.output.split("Optional engines")[0]

    qwen = CliRunner().invoke(cli.app, ["setup", "--check", "--engine", "qwen_image_21"],
                              env=env)
    assert qwen.exit_code == 1
    assert "qwen_image_21" not in qwen.output.split("Optional engines")[1].split("\n")[0]
    assert "Qwen Research" in qwen.output  # its licence is listed before downloading

    bad = CliRunner().invoke(cli.app, ["setup", "--check", "--engine", "nope"], env=env)
    assert bad.exit_code == 2 and "z_anime" in bad.output


# --- the engine's /setup endpoints --------------------------------------------


def _wait(client, until=lambda t: t["state"] != "running"):
    import time

    for _ in range(200):
        report = client.get("/setup").json()
        if until(report["task"]):
            return report
        time.sleep(0.01)
    raise AssertionError("setup task did not finish")


def _app(tmp_path, models, *, fetch=None, find=None):
    from fastapi.testclient import TestClient

    from manganation.web.api import create_app

    runner = ms.SetupRunner(tmp_path / "models", lambda: models, fetch=fetch, find=find,
                            comfy_paths=tmp_path / "paths.yaml")
    return TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                 setup_runner=runner))


def _fake_fetch(model, root, progress=None):
    path = model.path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    if progress:
        progress(len(PAYLOAD), len(PAYLOAD))
    return path


def test_setup_reports_then_downloads_what_is_missing(tmp_path):
    client = _app(tmp_path, [_model()], fetch=_fake_fetch)
    report = client.get("/setup").json()
    assert not report["ready"] and report["missing_bytes"] == len(PAYLOAD)
    row = report["models"][0]
    assert (row["state"], row["downloadable"], row["feature"]) == ("missing", True, "rendering")

    started = client.post("/setup/download", json={})
    assert started.status_code == 202 and started.json()["kind"] == "download"
    report = _wait(client)
    assert report["task"]["state"] == "done" and report["task"]["finished"] == ["m.safetensors"]
    assert report["task"]["done"] == len(PAYLOAD)
    assert report["ready"] and report["models"][0]["state"] == "present"
    assert report["comfy_paths_changed"]  # ComfyUI's paths now name this models folder
    assert str((tmp_path / "models").resolve()) in (tmp_path / "paths.yaml").read_text()


def test_one_setup_task_at_a_time_and_cancel_keeps_the_part(tmp_path):
    import threading

    release = threading.Event()

    def slow_fetch(model, root, progress=None):
        while not release.wait(0.01):
            progress(1, model.size)  # raises Cancelled once cancel is asked

    client = _app(tmp_path, [_model()], fetch=slow_fetch)
    client.post("/setup/download", json={})
    assert client.post("/setup/download", json={}).status_code == 409
    assert client.post("/setup/link", json={"folders": [str(tmp_path)]}).status_code == 409
    client.post("/setup/cancel")
    report = _wait(client)
    release.set()
    assert report["task"]["state"] == "cancelled" and not report["ready"]


def test_failed_downloads_are_reported_and_others_continue(tmp_path):
    second = ms.ModelFile(role="upscaler", feature="hi-res", file="u.pth",
                          subdir="upscale_models", size=len(PAYLOAD),
                          sha256=hashlib.sha256(PAYLOAD).hexdigest(), urls=["https://x/u"])

    def fetch(model, root, progress=None):
        if model.file == "m.safetensors":
            raise ms.SetupError("could not download m.safetensors: HTTP 503")
        return _fake_fetch(model, root, progress)

    client = _app(tmp_path, [_model(), second], fetch=fetch)
    client.post("/setup/download", json={})
    report = _wait(client)
    assert report["task"]["state"] == "error"
    assert report["task"]["finished"] == ["u.pth"]
    assert "HTTP 503" in report["task"]["errors"][0]


def test_setup_refuses_a_download_that_does_not_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "free_bytes", lambda root: 10)
    response = _app(tmp_path, [_model()], fetch=_fake_fetch).post("/setup/download", json={})
    assert response.status_code == 507 and "disk space" in response.json()["detail"]


def test_setup_links_files_the_user_already_has(tmp_path):
    library = tmp_path / "ComfyUI"
    library.mkdir()
    (library / "whatever.safetensors").write_bytes(PAYLOAD)
    client = _app(tmp_path, [_model()])
    assert client.post("/setup/link", json={"folders": [str(tmp_path / "nope")]}
                       ).status_code == 400
    client.post("/setup/link", json={"folders": [str(library)]})
    report = _wait(client)
    assert report["task"]["state"] == "done" and report["ready"]
    assert report["task"]["finished"] == ["m.safetensors (hard link)"]


def test_a_sandboxed_engine_never_records_itself_as_the_host_install(tmp_path, monkeypatch):
    from manganation import config

    monkeypatch.setenv("HOME", str(tmp_path))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: True if str(self) == "/.flatpak-info"
                        else real_exists(self))
    config.remember_engine_home()
    assert not (tmp_path / ".config/imanganation/engine-home").exists()


# --- the renderer (ComfyUI + PyTorch) as part of setup, as in the Flatpak --------------


class FakeInstaller:
    def __init__(self, log, *, fail=None, state=None):
        self.log, self.fail, self.state = log, fail, state if state is not None else {}

    def ready(self):
        return self.state.get("ready", False)

    def plan(self):
        self.log("Checking the GPU")
        return "plan"

    def install(self, plan):
        self.log("Installing PyTorch (cu130); a few GB…")
        if self.fail:
            raise ms.SetupError(self.fail)

    def verify(self):
        return "2.14.1+cu130 | NVIDIA GeForce RTX 5060 Ti"

    def mark_ready(self):
        self.state["ready"] = True


def _renderer_app(tmp_path, fail=None):
    from fastapi.testclient import TestClient

    from manganation.web.api import create_app

    state = {}
    runner = ms.SetupRunner(tmp_path / "models", lambda: [_model()], fetch=_fake_fetch,
                            renderer=lambda log: FakeInstaller(log, fail=fail, state=state))
    return TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                 setup_runner=runner)), state


def test_the_renderer_is_the_first_row_and_installs_before_the_models(tmp_path):
    client, state = _renderer_app(tmp_path)
    report = client.get("/setup").json()
    row = report["models"][0]
    assert (row["role"], row["state"], row["size"]) == ("renderer", "missing",
                                                         ms.RENDERER_BYTES)
    assert report["missing_bytes"] == ms.RENDERER_BYTES + len(PAYLOAD)
    started = client.post("/setup/download", json={}).json()
    assert started["renderer"] and started["phase"] == "renderer"
    report = _wait(client)
    task = report["task"]
    assert task["state"] == "done" and task["phase"] == "models"
    assert task["finished"] == ["renderer (2.14.1+cu130 | NVIDIA GeForce RTX 5060 Ti)",
                                "m.safetensors"]
    assert report["ready"] and state["ready"]


def test_a_failed_renderer_is_reported_and_the_models_still_download(tmp_path):
    client, state = _renderer_app(tmp_path, fail="the NVIDIA driver is too old")
    client.post("/setup/download", json={})
    report = _wait(client)
    assert report["task"]["state"] == "error"
    assert report["task"]["errors"] == ["renderer: the NVIDIA driver is too old"]
    assert report["task"]["finished"] == ["m.safetensors"]
    assert report["models"][0]["state"] == "missing" and not report["ready"]


def test_the_renderer_counts_towards_the_disk_space_check(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "free_bytes", lambda root: ms.RENDERER_BYTES)  # models won't fit
    client, _ = _renderer_app(tmp_path)
    response = client.post("/setup/download", json={})
    assert response.status_code == 507 and "disk space" in response.json()["detail"]


def test_comfyui_starts_right_after_the_renderer_installs(tmp_path):
    from fastapi.testclient import TestClient

    from manganation.web.api import create_app

    state, started = {}, []
    runner = ms.SetupRunner(tmp_path / "models", lambda: [], fetch=_fake_fetch,
                            renderer=lambda log: FakeInstaller(log, state=state),
                            on_renderer_ready=lambda: started.append(True))
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                   setup_runner=runner))
    client.post("/setup/download", json={})
    _wait(client)
    assert started == [True] and state["ready"]


def test_inside_the_flatpak_data_and_projects_leave_the_read_only_app(tmp_path, monkeypatch):
    from manganation import config

    monkeypatch.delenv("IMANGANATION_DATA", raising=False)
    monkeypatch.delenv("IMANGANATION_PROJECTS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".var/app/x/data"))
    monkeypatch.setattr(config, "in_flatpak", lambda: True)
    data = tmp_path / ".var/app/x/data/imanganation"
    assert config.data_root() == data
    assert config.models_root() == data / "models"
    assert config.outputs_root() == data / "outputs"
    assert config.comfy_paths_file() == data / "comfyui_extra_model_paths.yaml"
    assert config.projects_root() == tmp_path / "Imanganation"
    monkeypatch.setattr(config, "in_flatpak", lambda: False)
    assert config.data_root() == config.REPO_ROOT  # a checkout: unchanged
    assert config.projects_root() == config.REPO_ROOT / "projects"


def test_use_files_i_have_finds_the_optional_engines_files_too(tmp_path, monkeypatch):
    from manganation import engines

    qwen_vae = bytes(range(255, -1, -1)) * 3000  # another size: a different file
    extra = ms.ModelFile(role="qwen_image_21 vae", feature="Qwen-Image 2.1 engine (VAE)",
                         file="qwen_vae.safetensors", subdir="vae", size=len(qwen_vae),
                         sha256=hashlib.sha256(qwen_vae).hexdigest(), urls=["https://x/v"])
    monkeypatch.setattr(engines, "files", lambda engine, models, settings:
                        [extra] if engine.id == "qwen_image_21" else [])
    library = tmp_path / "ComfyUI"
    (library / "vae").mkdir(parents=True)
    (library / "checkpoint.safetensors").write_bytes(PAYLOAD)
    (library / "vae" / "downloaded-before.safetensors").write_bytes(qwen_vae)
    client = _app(tmp_path, [_model()])
    client.post("/setup/link", json={"folders": [str(library)]})
    report = _wait(client)
    assert report["task"]["state"] == "done"
    assert sorted(report["task"]["finished"]) == ["m.safetensors (hard link)",
                                                  "qwen_vae.safetensors (hard link)"]
    assert (tmp_path / "models" / "vae" / "qwen_vae.safetensors").read_bytes() == qwen_vae
