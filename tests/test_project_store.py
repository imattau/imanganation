"""The GIMP plug-in's stdlib-only project persistence boundary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from gimp.imanganation.project_store import (
    ProjectFileError,
    load_project,
    new_id,
    save_project,
)
from manganation.project_container import integrity_errors


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / "docs/project-container.example.json").read_text())
SCHEMA = json.loads((ROOT / "docs/project-container.schema.json").read_text())


def test_example_round_trips_with_unknown_keys_and_schema(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    document["future_extension"] = {"retained": [1, 2, 3]}
    save_project(tmp_path, document)

    loaded = load_project(tmp_path)
    assert loaded["future_extension"] == {"retained": [1, 2, 3]}
    jsonschema.validate(loaded, SCHEMA)
    assert integrity_errors(loaded) == []
    assert not (tmp_path / "project.json.tmp").exists()


@pytest.mark.parametrize("bad_path", ["/etc/passwd", "../outside.png", "takes\\image.png", "C:/image.png"])
def test_manifest_rejects_non_project_relative_paths(tmp_path, bad_path):
    document = copy.deepcopy(EXAMPLE)
    document["takes"]["tk_000001"]["file"] = bad_path
    with pytest.raises(ProjectFileError, match="relative path|stay inside"):
        save_project(tmp_path, document)


def test_generated_ids_match_the_manifest_prefixes():
    for prefix in ("prj_", "pnl_", "pg_", "tk_"):
        identifier = new_id(prefix)
        assert identifier.startswith(prefix)
        assert len(identifier[len(prefix):]) >= 6
        assert identifier[len(prefix):].islower()
        assert identifier[len(prefix):].isalnum()


def test_load_rejects_unknown_versions(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    document["version"] = 2
    (tmp_path / "project.json").write_text(json.dumps(document))
    with pytest.raises(ProjectFileError, match="unsupported project version"):
        load_project(tmp_path)
