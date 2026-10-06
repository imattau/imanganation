"""Face pass: matching faces to characters, masks (no GPU, no models)."""

from __future__ import annotations

from manganation.render import facepass as fp


def test_identity_tags_keep_only_what_tells_people_apart():
    probs = {"white hair": 0.9, "twintails": 0.8, "grin": 0.9, "red eyes": 0.5,
             "school uniform": 0.9, "brown hair": 0.1}
    assert fp.identity_tags(probs) == {"white hair": 0.9, "twintails": 0.8, "red eyes": 0.5}


def test_faces_are_matched_to_the_most_alike_reference():
    yuki = {"white hair": 0.9, "twintails": 0.8, "pink hair": 0.4}
    akira = {"brown hair": 0.9, "streaked hair": 0.7, "blue hair": 0.5}
    faces = [{"brown hair": 0.8, "blue hair": 0.6}, {"white hair": 0.7, "twintails": 0.6}]
    assert fp.match_faces(faces, {"Yuki": yuki, "Akira": akira}) == {0: "Akira", 1: "Yuki"}
    # an extra face (a background extra) is left alone
    faces.append({"black hair": 0.9})
    assert sorted(fp.match_faces(faces, {"Yuki": yuki, "Akira": akira}).values()) == [
        "Akira", "Yuki"]


def test_face_mask_is_a_grown_oval_on_the_face():
    mask = fp.face_mask((200, 200), (50, 50, 150, 150))
    assert mask.getpixel((100, 100)) == 255          # the face
    assert mask.getpixel((40, 100)) == 255           # grown 15% past the box
    assert mask.getpixel((5, 5)) == 0 and mask.getpixel((52, 52)) == 0  # oval corners
