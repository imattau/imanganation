"""Model update checks: newer files on Hugging Face, pinned per user, never silently."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from manganation import model_updates as mu
from manganation import models_setup as ms
from manganation.web.api import create_app

OLD, NEW = "a" * 64, "b" * 64
COMMIT = "c" * 40


def _models():
    return {
        "taggers": {"wd": {
            "id": "wd.onnx", "subdir": "taggers", "display": "WD tagger",
            "license": {"name": "Apache-2.0", "url": "https://x"},
            "download": {"url": "https://huggingface.co/o/tag/resolve/rev1/wd.onnx",
                         "size": 10, "sha256": OLD}}},
        "checkpoints": {
            "primary": {"id": "ck.safetensors", "subdir": "checkpoints",
                        "download": {"url": "https://huggingface.co/o/ck/resolve/r/dir/ck.safetensors",
                                     "mirrors": ["https://elsewhere/ck"], "size": 5,
                                     "sha256": OLD}},
            "fallbacks": [{"id": "other.safetensors"}]},
        "trials": {"t": {"model": {"id": "t", "download": {
            "url": "https://huggingface.co/o/t/resolve/r/t", "size": 1, "sha256": OLD}}}},
    }


def _server(files, *, status=200, seen=None):
    """A fake Hugging Face: ``files`` is {repo: {path: oid-or-None}}."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status)
        repo = "/".join(request.url.path.split("/")[3:5])
        if request.url.path.endswith("/revision/main"):
            return httpx.Response(200, json={"sha": COMMIT})
        paths = __import__("json").loads(request.content)["paths"]
        return httpx.Response(200, json=[
            {"path": p, **({"lfs": {"oid": files[repo][p], "size": 99}} if files[repo][p] else {})}
            for p in paths if p in files.get(repo, {})])
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def root(tmp_path):
    for sub, name in (("taggers", "wd.onnx"), ("checkpoints", "ck.safetensors")):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / name).write_bytes(b"x")
    return tmp_path


def test_a_changed_lfs_hash_is_an_update_with_a_pinned_url(root):
    client = _server({"o/tag": {"wd.onnx": NEW}, "o/ck": {"dir/ck.safetensors": NEW}})
    found = {u.path: u for u in mu.check(_models(), root, client=client)}
    assert set(found) == {"taggers.wd", "checkpoints.primary"}
    trials = _server({"o/t": {"t": NEW}})
    (root / "t").write_bytes(b"x")
    models = _models()
    models["trials"]["t"]["model"].update(id="t", subdir="")
    (only,) = mu.check(models, root, client=trials)
    assert (only.path, only.kind) == ("trials.t.model", "manual")
    wd = found["taggers.wd"]
    assert (wd.kind, wd.sha256, wd.base_sha256, wd.size, wd.old_size, wd.subdir) == (
        "auto", NEW, OLD, 99, 10, "taggers")
    assert wd.url == f"https://huggingface.co/o/tag/resolve/{COMMIT}/wd.onnx"
    assert found["checkpoints.primary"].kind == "manual"
    assert found["checkpoints.primary"].url.endswith(f"/{COMMIT}/dir/ck.safetensors")


def test_unchanged_missing_and_non_lfs_files_are_not_updates(root):
    (root / "checkpoints" / "ck.safetensors").unlink()  # not installed: nothing to update
    same = _server({"o/tag": {"wd.onnx": OLD}})
    assert mu.check(_models(), root, client=same) == []
    plain = _server({"o/tag": {"wd.onnx": None}})  # no LFS object: no hash to compare
    assert mu.check(_models(), root, client=plain) == []


def test_unreachable_is_an_error_but_one_bad_repo_is_not(root):
    with pytest.raises(httpx.HTTPError):
        mu.check(_models(), root, client=_server({}, status=503))
    good = {"o/tag": {"wd.onnx": NEW}}
    client = _server(good)  # o/ck has no files entry -> an empty answer, not an error
    assert [u.path for u in mu.check(_models(), root, client=client)] == ["taggers.wd"]


def test_a_pin_replaces_the_shipped_one_until_the_app_ships_another(root):
    update = mu.Update(path="taggers.wd", display="d", file="wd.onnx", kind="auto", size=99,
                       old_size=10, url="https://huggingface.co/o/tag/resolve/new/wd.onnx",
                       sha256=NEW, base_sha256=OLD)
    pins_path = root / "pins.yaml"
    mu.write_pin(update, pins_path)
    pinned = mu.apply_pins(_models(), mu.read_pins(pins_path))
    assert pinned["taggers"]["wd"]["download"] == {
        "url": update.url, "size": 99, "sha256": NEW}
    # the app now ships a different pin for this file: the shipped one wins
    shipped = _models()
    shipped["taggers"]["wd"]["download"]["sha256"] = "d" * 64
    assert mu.apply_pins(shipped, mu.read_pins(pins_path))["taggers"]["wd"][
        "download"]["sha256"] == "d" * 64
    # mirrors held the old file
    models = _models()
    models["taggers"]["wd"]["download"]["mirrors"] = ["https://m"]
    assert "mirrors" not in mu.apply_pins(models, mu.read_pins(pins_path))[
        "taggers"]["wd"]["download"]


def test_checks_run_once_a_day_and_can_be_switched_off(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    state = mu.read_state(path)
    assert mu.due(state)
    assert not mu.due({**state, "checked_at": time.time() - 60})
    assert not mu.due({**state, "enabled": False})
    monkeypatch.setenv("IMANGANATION_NO_UPDATE_CHECK", "1")
    assert not mu.enabled(state)


def test_run_check_remembers_findings_and_skips_only_that_version(root, tmp_path):
    path = tmp_path / "state.json"
    client = _server({"o/tag": {"wd.onnx": NEW}, "o/ck": {"dir/ck.safetensors": OLD}})
    state = mu.run_check(_models(), root, state_path=path, client=client)
    assert [u["path"] for u in state["updates"]] == ["taggers.wd"] and state["checked_at"]
    state["skipped"] = {"taggers.wd": NEW}
    mu.write_state(state, path)
    assert mu.offered(mu.read_state(path)) == []
    newer = _server({"o/tag": {"wd.onnx": "e" * 64}, "o/ck": {"dir/ck.safetensors": OLD}})
    state = mu.run_check(_models(), root, state_path=path, client=newer)
    assert state["skipped"] == {}  # a newer version than the skipped one: offered again
    assert [u["path"] for u in mu.offered(state)] == ["taggers.wd"]
    # offline keeps what was found and says why
    state = mu.run_check(_models(), root, state_path=path, client=_server({}, status=503))
    assert state["error"] and len(state["updates"]) == 1


def _update(**kw):
    base = dict(path="taggers.wd", display="d", file="wd.onnx", kind="auto", size=4,
                old_size=10, url="https://huggingface.co/o/tag/resolve/new/wd.onnx",
                sha256=NEW, base_sha256=OLD, subdir="taggers")
    return mu.Update(**{**base, **kw})


def _wait(runner):
    for _ in range(300):
        task = runner.report()["task"]
        if task["state"] != "running":
            return task
        time.sleep(0.01)
    raise AssertionError("update did not finish")


def test_an_update_downloads_over_the_old_file_then_pins(tmp_path):
    (tmp_path / "taggers").mkdir()
    (tmp_path / "taggers" / "wd.onnx").write_bytes(b"old")
    seen, pinned = [], []

    def fetch(model, root, progress=None):
        seen.append(model)
        model.path(root).write_bytes(b"new!")
        if progress:
            progress(4, 4)

    runner = ms.SetupRunner(tmp_path, lambda: [], fetch=fetch, on_updated=pinned.append)
    runner.start_update([_update()])
    task = _wait(runner)
    assert task["state"] == "done" and task["kind"] == "update"
    assert seen[0].urls == [_update().url] and seen[0].sha256 == NEW
    assert (tmp_path / "taggers" / "wd.onnx").read_bytes() == b"new!"
    assert [u.path for u in pinned] == ["taggers.wd"]


def test_a_failed_update_is_not_pinned(tmp_path):
    pinned = []

    def fetch(model, root, progress=None):
        raise ms.SetupError("SHA-256 mismatch")

    runner = ms.SetupRunner(tmp_path, lambda: [], fetch=fetch, on_updated=pinned.append)
    runner.start_update([_update()])
    task = _wait(runner)
    assert task["state"] == "error" and pinned == []


def test_update_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("IMANGANATION_DATA", str(tmp_path))
    monkeypatch.delenv("IMANGANATION_NO_UPDATE_CHECK", raising=False)
    mu.write_state({**mu.read_state(), "updates": [_update().__dict__], "checked_at": time.time()})
    pinned = []

    def fetch(model, root, progress=None):
        model.path(root).parent.mkdir(parents=True, exist_ok=True)
        model.path(root).write_bytes(b"new!")

    runner = ms.SetupRunner(tmp_path / "models", lambda: [], fetch=fetch,
                            on_updated=pinned.append)
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                   setup_runner=runner))
    view = client.get("/updates").json()
    assert view["offered"] == ["taggers.wd"] and view["enabled"] and not view["checking"]
    # a check inside the daily window does nothing
    assert client.post("/updates/check", json={}).json()["checking"] is False
    assert client.post("/updates/skip", json={"paths": ["taggers.wd"]}).json()["offered"] == []
    assert client.post("/updates/apply", json={"paths": ["nope"]}).status_code == 404
    assert client.post("/updates/apply", json={"paths": ["taggers.wd"]}).status_code == 202
    _wait(runner)
    assert [u.path for u in pinned] == ["taggers.wd"]
    off = client.post("/updates/enabled", json={"enabled": False}).json()
    assert off["enabled"] is False
