"""Render-accuracy evaluation: does a render show what its panel asked for?

A suite (``config/eval/*.yaml``) names a script, the character registry to render
with, fixed seeds, and per panel the Danbooru tags its render should (and shouldn't)
have. ``run`` renders every panel at every seed through the engine's own render path,
tags each image with the WD14 tagger, and scores the checks:

- **count**: the people in the picture. Derived from the characters' count tags
  (``1boy`` + ``1girl``), so a dropped, duplicated or merged figure fails it.
- **tags**: each entry must be seen; ``a|b`` passes on either.
- **forbid**: each entry must *not* be seen (a grin on a sigh, the rooftop in the
  stairwell).

Seeds stay fixed, so two runs differ only by what changed in between (prompt, style,
settings, models). ``compare`` puts two reports side by side.

With ``characters`` in the suite (each one's identity ``tags`` and ``forbid``), the
person detector also finds every figure:

- **figures**: as many figures as the panel has characters (a clone or a dropped
  character fails it; the tagger alone counted a three-figure render as two).
- **identity**: each figure is cropped and tagged on its own, figures are matched to
  characters by the best assignment, and each character's tags are checked on their
  own crop. Costume bleed (Akira in Yuki's skirt) fails a ``forbid``.

Without them, the tagger sees the picture as a whole: it can tell that someone is
grinning, not *who*.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from itertools import permutations
from pathlib import Path

import yaml

from manganation.config import REPO_ROOT

DEFAULT_THRESHOLD = 0.35
COUNT_TAG = re.compile(r"^(\d+|6\+)(girl|boy|other)s?$")
GROUP_TAGS = {"solo", "multiple girls", "multiple boys", "multiple others", "no humans"}


@dataclass
class Case:
    id: str
    page: int
    panel: int
    frame: tuple[float, float]
    tags: list[str] = field(default_factory=list)
    forbid: list[str] = field(default_factory=list)
    count: list[str] | None = None  # override; None = from the characters' count tags
    override: dict = field(default_factory=dict)  # PanelSpec fields to replace


@dataclass
class Suite:
    name: str
    path: Path
    script: Path
    identity: Path
    seeds: list[int]
    cases: list[Case]
    reading_order: str = "rtl"
    threshold: float = DEFAULT_THRESHOLD
    # name -> {"tags": [...], "forbid": [...]}, checked on that character's own figure
    characters: dict[str, dict[str, list[str]]] = field(default_factory=dict)


def _repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_suite(path: Path | str) -> Suite:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    cases = []
    for raw in data["cases"]:
        page, panel = (int(n) for n in str(raw["panel"]).split("-"))
        expect = raw.get("expect") or {}
        cases.append(Case(
            id=raw.get("id") or f"p{page}-{panel}", page=page, panel=panel,
            frame=tuple(raw["frame"]), tags=list(expect.get("tags") or []),
            forbid=list(expect.get("forbid") or []), count=expect.get("count"),
            override=dict(raw.get("override") or {})))
    return Suite(
        name=data.get("name") or path.stem, path=path.resolve(),
        script=_repo_path(data["script"]), identity=_repo_path(data["identity"]),
        seeds=[int(s) for s in data["seeds"]], cases=cases,
        reading_order=data.get("reading_order", "rtl"),
        threshold=float(data.get("threshold", DEFAULT_THRESHOLD)),
        characters={name: {"tags": list((c or {}).get("tags") or []),
                           "forbid": list((c or {}).get("forbid") or [])}
                    for name, c in (data.get("characters") or {}).items()})


def panel_specs(suite: Suite) -> dict[str, object]:
    """Case id -> the PanelSpec the script parser makes for it (plus overrides), so the
    eval measures the whole path from script text to picture."""
    from manganation.script.formats.canonical import parse
    from manganation.script.schema import PanelSpec

    parsed = parse(suite.script.read_text())["panels"]
    by_label = {(p["page"], p["panel"]): p for p in parsed}
    fields = set(PanelSpec.model_fields)
    out = {}
    for case in suite.cases:
        panel = by_label.get((case.page, case.panel))
        if panel is None:
            raise ValueError(f"{case.id}: no PAGE {case.page} PANEL {case.panel} in "
                             f"{suite.script}")
        data = {k: v for k, v in {**panel, **case.override}.items() if k in fields}
        out[case.id] = PanelSpec(**data)
    return out


def expected_count(spec, identity: Path) -> list[str]:
    from manganation.render.panel import character_tags, head_count

    return head_count(spec.characters, character_tags(identity, spec.characters))


# --- checks -------------------------------------------------------------------


def _alternatives(entry: str) -> list[str]:
    return [a.strip() for a in entry.split("|") if a.strip()]


def check_count(expected: list[str], probs: dict[str, float], threshold: float) -> dict:
    """The expected count tags are seen, and no other count says otherwise."""
    allowed = set(expected)
    per_kind: dict[str, int] = {}
    for tag in expected:
        m = COUNT_TAG.match(tag)
        if m and m.group(1).isdigit():
            per_kind[m.group(2)] = per_kind.get(m.group(2), 0) + int(m.group(1))
    if sum(per_kind.values()) == 1:
        allowed.add("solo")
    allowed |= {f"multiple {kind}s" for kind, n in per_kind.items() if n > 1}
    seen = {t for t, p in probs.items()
            if p >= threshold and (COUNT_TAG.match(t) or t in GROUP_TAGS)}
    missing = [t for t in expected if probs.get(t, 0.0) < threshold]
    extra = sorted(seen - allowed)
    detail = ", ".join([*(f"no {t}" for t in missing), *(f"saw {t}" for t in extra)])
    return {"label": "count: " + ", ".join(expected), "kind": "count",
            "ok": not missing and not extra, "detail": detail}


def check_tag(entry: str, probs: dict[str, float], threshold: float, *,
              forbid: bool = False) -> dict:
    best = max(_alternatives(entry), key=lambda t: probs.get(t, 0.0))
    p = probs.get(best, 0.0)
    seen = p >= threshold
    return {"label": f"{'not ' if forbid else ''}{entry}", "kind": "forbid" if forbid else "tag",
            "ok": seen != forbid, "detail": f"{best} {p:.2f}"}


def score_image(probs: dict[str, float], case: Case, count: list[str],
                threshold: float) -> list[dict]:
    checks = [check_count(count, probs, threshold)] if count else []
    checks += [check_tag(t, probs, threshold) for t in case.tags]
    checks += [check_tag(t, probs, threshold, forbid=True) for t in case.forbid]
    return checks


COVERED = 0.3  # a figure more covered than this by another one: its crop shows both


def covered(box, others) -> float:
    """Share of ``box`` that other figures' boxes cover (the most covered by any one)."""
    from manganation.evaluate.detector import Box

    def inter(a: Box, b: Box) -> float:
        return (max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
                * max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0)))
    return max((inter(box, o) / box.area for o in others if box.area), default=0.0)


def _identity_checks(name: str, want: dict, probs: dict[str, float],
                     threshold: float, *, forbids: bool = True) -> list[dict]:
    """``forbids`` off for an overlapped figure: its crop holds the other character
    too, so "Akira: not skirt" would judge Yuki's skirt."""
    checks = [check_tag(t, probs, threshold) for t in want["tags"]]
    if forbids:
        checks += [check_tag(t, probs, threshold, forbid=True) for t in want["forbid"]]
    for c in checks:
        c["label"], c["kind"] = f"{name}: {c['label']}", "identity"
    return checks


def score_figures(image: Path, names: list[str], suite: Suite, tagger,
                  detector) -> tuple[list[dict], list[dict]]:
    """-> (checks, figures). Each figure is tagged alone and matched to the character
    whose identity checks it passes best; a character without a figure fails theirs."""
    from PIL import Image

    with Image.open(image) as im:
        im = im.convert("RGB")
        boxes = detector.figures(im)
        crops = [tagger.tags(im.crop((max(0, round(b.x0)), max(0, round(b.y0)),
                                      min(im.width, round(b.x1)),
                                      min(im.height, round(b.y1)))))
                 for b in boxes]
    clear = [covered(b, boxes[:i] + boxes[i + 1:]) <= COVERED for i, b in enumerate(boxes)]
    checks = [{"label": f"figures: {len(names)}", "kind": "figures",
               "ok": len(boxes) == len(names), "detail": f"found {len(boxes)}"}]
    known = [n for n in names if n in suite.characters]
    best: tuple[int, dict[str, int]] = (-1, {})
    # every way to give the known characters distinct figures (or none, if too few)
    slots = list(range(len(boxes))) + [None] * len(known)
    for chosen in dict.fromkeys(permutations(slots, len(known))):  # ordered: ties stable
        passed = sum(c["ok"] for name, f in zip(known, chosen, strict=True) if f is not None
                     for c in _identity_checks(name, suite.characters[name], crops[f],
                                               suite.threshold, forbids=clear[f]))
        if passed > best[0]:
            best = (passed, {n: f for n, f in zip(known, chosen, strict=True)
                             if f is not None})
    owner = {f: n for n, f in best[1].items()}
    for name in known:
        want = suite.characters[name]
        if name in best[1]:
            f = best[1][name]
            checks += _identity_checks(name, want, crops[f], suite.threshold,
                                       forbids=clear[f])
        else:
            checks += [{"label": f"{name}: {t}", "kind": "identity", "ok": False,
                        "detail": "no figure"} for t in want["tags"]]
    figures = [{"box": [round(b.x0), round(b.y0), round(b.x1), round(b.y1)],
                "score": round(b.score, 2), "character": owner.get(i),
                "overlapped": not clear[i]}
               for i, b in enumerate(boxes)]
    return checks, figures


def unknown_tags(suite: Suite, vocabulary: set[str]) -> list[str]:
    """Expectation tags the tagger can never output (a typo, or not a Danbooru tag):
    such a check would always fail (or, forbidden, always pass)."""
    identity = [e for c in suite.characters.values() for e in [*c["tags"], *c["forbid"]]]
    wanted = {t for c in suite.cases for e in [*c.tags, *c.forbid, *(c.count or [])]
              for t in _alternatives(e)}
    wanted |= {t for e in identity for t in _alternatives(e)}
    return sorted(wanted - vocabulary)


# --- running ------------------------------------------------------------------


def _git() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"commit": run("rev-parse", "--short", "HEAD"),
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


def _snapshot() -> dict:
    """What a run was rendered with, so two reports can say why they differ."""
    from manganation.config import load_settings
    from manganation.render.panel import load_style

    defaults = load_settings().defaults
    return {"style": load_style(), "panel": defaults.panel.model_dump(),
            "ipadapter": defaults.ipadapter.model_dump(), "git": _git()}


def render_all(suite: Suite, out_dir: Path, *, client=None, log=print) -> None:
    """Render every case at every seed into ``out_dir`` (skipping images already there,
    so an interrupted run resumes)."""
    from manganation.render.panel import _render
    from manganation.script.schema import ReadingOrder

    out_dir.mkdir(parents=True, exist_ok=True)
    specs = panel_specs(suite)
    for case in suite.cases:
        for seed in suite.seeds:
            out = out_dir / f"{case.id}-s{seed}.png"
            if out.exists():
                continue
            log(f"rendering {case.id} seed {seed}")
            result = _render(specs[case.id], suite.identity, *case.frame,
                             reading_order=ReadingOrder(suite.reading_order), seed=seed,
                             client=client, out=out, seq=None)
            for warning in result.warnings:
                log(f"  warning: {warning}")


def score_all(suite: Suite, out_dir: Path, tagger, *, label: str = "",
              snapshot: dict | None = None, detector=None) -> dict:
    """Tag every render in ``out_dir`` and write ``report.json`` (+ ``sheet.png``).
    With a ``detector`` (and ``characters`` in the suite), figures are checked too."""
    specs = panel_specs(suite)
    results = []
    for case in suite.cases:
        count = case.count if case.count is not None else expected_count(
            specs[case.id], suite.identity)
        for seed in suite.seeds:
            image = out_dir / f"{case.id}-s{seed}.png"
            if not image.exists():
                continue
            probs = tagger.tags(image)
            meta = image.with_suffix(".json")
            prompt = json.loads(meta.read_text()).get("prompt", "") if meta.exists() else ""
            checks = score_image(probs, case, count, suite.threshold)
            figures: list[dict] = []
            if detector is not None and suite.characters:
                more, figures = score_figures(image, specs[case.id].characters, suite,
                                              tagger, detector)
                checks += more
            top = sorted(probs.items(), key=lambda kv: -kv[1])[:30]
            results.append({
                "case": case.id, "seed": seed, "image": image.name, "prompt": prompt,
                "checks": checks, "figures": figures,
                "score": sum(c["ok"] for c in checks) / len(checks) if checks else 1.0,
                "top_tags": {t: round(p, 3) for t, p in top if p >= 0.2}})
    report = {"suite": suite.name, "suite_path": str(suite.path), "label": label,
              "created": datetime.now().astimezone().isoformat(timespec="seconds"),
              "threshold": suite.threshold, "seeds": suite.seeds,
              "unknown_tags": unknown_tags(suite, tagger.vocabulary),
              "config": snapshot if snapshot is not None else _snapshot(),
              "results": results, "summary": summarize(results)}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    contact_sheet(results, out_dir)
    return report


def summarize(results: list[dict]) -> dict:
    """Overall score (share of checks passed), per case, and per check."""
    checks = [c for r in results for c in r["checks"]]
    by_case: dict[str, list[float]] = {}
    for r in results:
        by_case.setdefault(r["case"], []).append(r["score"])
    by_check: dict[str, list[bool]] = {}
    for r in results:
        for c in r["checks"]:
            by_check.setdefault(f"{r['case']}: {c['label']}", []).append(c["ok"])
    by_kind: dict[str, list[bool]] = {}
    for c in checks:
        by_kind.setdefault(c["kind"], []).append(c["ok"])
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    return {"score": mean([c["ok"] for c in checks]), "images": len(results),
            "by_kind": {k: mean(v) for k, v in by_kind.items()},
            "by_case": {k: mean(v) for k, v in by_case.items()},
            "by_check": {k: mean(v) for k, v in by_check.items()}}


def contact_sheet(results: list[dict], out_dir: Path, *, thumb: int = 320) -> Path | None:
    """One row per case, one column per seed, each captioned with what failed."""
    from PIL import Image, ImageDraw

    if not results:
        return None
    cases = list(dict.fromkeys(r["case"] for r in results))
    seeds = list(dict.fromkeys(r["seed"] for r in results))
    caption = 64
    sheet = Image.new("RGB", (len(seeds) * (thumb + 8) + 8,
                              len(cases) * (thumb + caption + 8) + 8), "white")
    draw = ImageDraw.Draw(sheet)
    for r in results:
        x = 8 + seeds.index(r["seed"]) * (thumb + 8)
        y = 8 + cases.index(r["case"]) * (thumb + caption + 8)
        with Image.open(out_dir / r["image"]) as im:
            im = im.convert("RGB")
            scale = min(thumb / im.width, thumb / im.height)
            im.thumbnail((thumb, thumb))
            ox, oy = x + (thumb - im.width) // 2, y + (thumb - im.height) // 2
            sheet.paste(im, (ox, oy))
        for fig in r.get("figures", []):
            x0, y0, x1, y1 = (round(v * scale) for v in fig["box"])
            colour = "lime" if fig["character"] else "red"
            draw.rectangle((ox + x0, oy + y0, ox + x1, oy + y1), outline=colour, width=2)
            draw.text((ox + x0 + 3, oy + y0 + 2), fig["character"] or "?", fill=colour)
        passed = sum(c["ok"] for c in r["checks"])
        failed = [c["label"] for c in r["checks"] if not c["ok"]]
        text = f"{r['case']} s{r['seed']}: {passed}/{len(r['checks'])}\n"
        line = ""
        for item in failed:  # wrap the failures to the thumbnail's width
            piece = (", " if line else "x ") + item
            if draw.textlength(line + piece) > thumb and line:
                text += line + "\n"
                line = "  " + item
            else:
                line += piece
        text += line
        draw.multiline_text((x, y + thumb + 2), text, fill="black" if not failed else "darkred")
    path = out_dir / "sheet.png"
    sheet.save(path)
    return path


def _margin(xs: list[float], ys: list[float]) -> float | None:
    """Two standard errors of the difference of two means: a change smaller than this
    is noise. Any prompt change re-rolls every image, so the samples are unpaired;
    measured: dropping one word ("daytime") moved a panel's 8-seed score by 7 points."""
    if len(xs) < 2 or len(ys) < 2:
        return None

    def var(v: list[float]) -> float:
        m = sum(v) / len(v)
        return sum((x - m) ** 2 for x in v) / (len(v) - 1)
    return 2 * (var(xs) / len(xs) + var(ys) / len(ys)) ** 0.5


def _pass_margin(a: list[bool], b: list[bool]) -> float | None:
    """``_margin`` for a pass rate, smoothed so 3/3 against 2/3 isn't "certain"."""
    if not a or not b:
        return None
    pa, pb = (sum(a) + 1) / (len(a) + 2), (sum(b) + 1) / (len(b) + 2)
    return 2 * (pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b)) ** 0.5


def compare(a: dict, b: dict) -> list[tuple[str, float | None, float | None, float | None]]:
    """(row, score in a, score in b, noise margin) for the overall, kind, case and check
    scores. A change within the margin (two standard errors) is noise."""
    def images(report, case=None):
        return [r["score"] for r in report["results"] if case in (None, r["case"])]

    def outcomes(report, label, kind=False):
        return [c["ok"] for r in report["results"] for c in r["checks"]
                if (c["kind"] == label if kind else f"{r['case']}: {c['label']}" == label)]

    rows = [("overall", a["summary"]["score"], b["summary"]["score"],
             _margin(images(a), images(b)))]
    for key in dict.fromkeys([*a["summary"]["by_kind"], *b["summary"]["by_kind"]]):
        rows.append((key, a["summary"]["by_kind"].get(key), b["summary"]["by_kind"].get(key),
                     _pass_margin(outcomes(a, key, True), outcomes(b, key, True))))
    for key in dict.fromkeys([*a["summary"]["by_case"], *b["summary"]["by_case"]]):
        rows.append((key, a["summary"]["by_case"].get(key), b["summary"]["by_case"].get(key),
                     _margin(images(a, key), images(b, key))))
    for key in dict.fromkeys([*a["summary"]["by_check"], *b["summary"]["by_check"]]):
        rows.append((key, a["summary"]["by_check"].get(key),
                     b["summary"]["by_check"].get(key),
                     _pass_margin(outcomes(a, key), outcomes(b, key))))
    return rows
