"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_llm_staging(monkeypatch):
    """Renders in tests never call the real LLM (render/staging.py): staging is
    unavailable unless a test installs its own, so prompts keep the script's prose."""
    def unavailable(*args, **kwargs):
        raise RuntimeError("no LLM in tests")

    monkeypatch.setattr("manganation.render.panel.stage", unavailable)
