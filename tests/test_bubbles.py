"""Speech-bubble templates: geometry, text boxes, SVG and previews (no GIMP)."""

from __future__ import annotations

import math

from gimp.imanganation import bubbles as B


def test_library_is_large_unique_and_covers_every_category_and_kind():
    library = B.templates()
    assert len(library) >= 100
    assert len({t.id for t in library}) == len(library)
    assert {t.category for t in library} == {c for c, _ in B.CATEGORIES}
    assert set(B.KIND_CATEGORY.values()) <= {t.category for t in library}
    assert B.template_by_id(library[5].id) == library[5]


def test_body_fills_its_box_and_the_tail_reaches_the_tip():
    for t in B.templates():
        polygons = B.outline(t, 300, 180, tail_tip=(90, 300))
        if t.shape == "none":
            assert polygons == []
            continue
        body = polygons[0]
        xs, ys = [p[0] for p in body], [p[1] for p in body]
        assert min(xs) >= -0.5 and max(xs) <= 300.5, t.id
        if t.tail in ("pointed", "curved"):
            assert (90, 300) in body, t.id  # the tail is part of the one outline
            assert max(ys) == 300
        else:
            assert max(ys) <= 180.5, t.id
        if t.tail == "dots":
            assert len(polygons) == 4  # body + three shrinking dots
            radii = [max(math.hypot(x - sum(q[0] for q in d) / len(d),
                                    y - sum(q[1] for q in d) / len(d)) for x, y in d)
                     for d in polygons[1:]]
            assert radii == sorted(radii, reverse=True)


def test_a_tip_inside_the_body_draws_no_tail():
    t = B.template_by_id("speech-oval-wide-pointed-solid-white")
    assert len(B.outline(t, 300, 180, tail_tip=(150, 90))[0]) == len(B.outline(
        B.template_by_id("speech-oval-wide-none-solid-white"), 300, 180)[0])


def test_text_box_is_centred_inside_and_size_for_text_inverts_it():
    for t in B.templates():
        x, y, w, h = B.text_box(t, 400, 200)
        assert 0 < w <= 400 and 0 < h <= 200
        assert abs((x + w / 2) - 200) < 1e-6 and abs((y + h / 2) - 100) < 1e-6
        bw, bh = B.size_for_text(t, 180, 60, padding=10)
        _, _, tw, th = B.text_box(t, bw, bh)
        assert abs(tw - 200) < 1e-6 and abs(th - 80) < 1e-6


def test_svg_places_the_path_at_image_coordinates():
    t = B.template_by_id("narration-box-wide-none-solid-white")
    svg = B.svg_document(B.outline(t, 100, 50), 2000, 3000, offset=(400, 600))
    assert 'width="2000" height="3000"' in svg and 'viewBox="0 0 2000 3000"' in svg
    assert "M 500.00 625.00" in svg  # the box's right-middle point, offset


def test_styles_and_previews():
    whisper = B.template_by_id("whisper-oval-wide-pointed-dashed-white")
    assert B.dash_pattern(whisper) and not B.dash_pattern(B.templates()[0])
    heavy = B.template_by_id("speech-oval-wide-pointed-heavy-white")
    solid = B.template_by_id("speech-oval-wide-pointed-solid-white")
    assert B.stroke_width(heavy, 300, 200) > B.stroke_width(solid, 300, 200)
    inner = next(t for t in B.templates() if t.fill == "black")
    assert inner.text_color == (1.0, 1.0, 1.0)
    png = B.preview_png(solid)
    assert png.startswith(b"\x89PNG") and len(png) > 200
