"""Small, dependency-free comic export helpers (ZIP/CBZ and raster PDF)."""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def safe_page_stem(label: str, index: int) -> str:
    """Return a stable, naturally sortable filename for a page."""
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", str(label or "Page"))
    label = label.strip(".-_") or "Page"
    return f"{index:03d}-{label}"


def parse_page_range(text: str, count: int) -> list[int]:
    """0-based indexes for a page range such as "3-7, 9" (1-based, in project order);
    blank or "all" is every page. Raises ValueError, worded for the artist."""
    text = (text or "").strip().lower()
    if text in ("", "all"):
        return list(range(count))
    chosen: set[int] = set()
    for part in text.split(","):
        bounds = [b.strip() for b in part.split("-")]
        if not part.strip() or len(bounds) > 2 or not all(b.isdigit() for b in bounds):
            raise ValueError(f"Can't read the page range {text!r}: write it like 1-4, 7")
        first, last = int(bounds[0]), int(bounds[-1])
        if first < 1 or last < first or last > count:
            raise ValueError(f"Pages {part.strip()} are outside this project's "
                             f"{count} page{'s' if count != 1 else ''}")
        chosen.update(range(first - 1, last))
    return sorted(chosen)


def is_cover_page(page: dict) -> bool:
    label = str(page.get("label", "")).strip().lower()
    return bool(page.get("cover")) or "cover" in label


def comicinfo_xml(title: str, chapter: str, reading_order: str,
                  page_count: int) -> bytes:
    root = ElementTree.Element("ComicInfo")
    for name, value in (("Series", title), ("Title", chapter),
                        ("Number", chapter),
                        ("Manga", "YesAndRightToLeft" if reading_order == "rtl"
                         else "No"), ("PageCount", str(page_count))):
        if value:
            ElementTree.SubElement(root, name).text = value
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def write_cbz(destination: Path, page_files: list[tuple[str, Path]], title: str,
              chapter: str, reading_order: str, include_metadata: bool = True) -> None:
    """Write a standard ZIP-based comic archive with ComicInfo metadata."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, source in page_files:
            archive.write(source, name)
        if include_metadata:
            archive.writestr("ComicInfo.xml", comicinfo_xml(
                title, chapter, reading_order, len(page_files)))


def _png_image_data(path: Path):
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Not a PNG image: {path.name}")
    offset = 8
    width = height = bit_depth = color_type = None
    idat = []
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + size]
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
        offset += 12 + size
    # GIMP's flattened RGB or grayscale exports can pass through using PDF's
    # PNG predictor without decoding and recompressing pixels.
    if bit_depth != 8 or color_type not in (0, 2):
        raise ValueError("PDF export needs flattened 8-bit RGB or grayscale PNG pages")
    colors = 1 if color_type == 0 else 3
    return width, height, b"".join(idat), colors


def _jpeg_dimensions(data: bytes):
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Not a JPEG image")
    offset = 2
    while offset + 4 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        length = struct.unpack(">H", data[offset:offset + 2])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
            return width, height, data[offset + 7]
        offset += length
    raise ValueError("Could not read JPEG dimensions")


def _pdf_image(path: Path):
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height, payload, colors = _png_image_data(path)
        color_space = "/DeviceGray" if colors == 1 else "/DeviceRGB"
        dictionary = (f"/ColorSpace {color_space} /Filter /FlateDecode "
                      f"/DecodeParms << /Predictor 15 /Colors {colors} "
                      f"/BitsPerComponent 8 /Columns {width} >>")
    elif data.startswith(b"\xff\xd8"):
        width, height, colors = _jpeg_dimensions(data)
        if colors not in (1, 3):
            raise ValueError("PDF export supports grayscale or RGB JPEG pages")
        payload = data
        color_space = "/DeviceGray" if colors == 1 else "/DeviceRGB"
        dictionary = f"/ColorSpace {color_space} /Filter /DCTDecode"
    else:
        raise ValueError(f"Unsupported PDF page image: {path.name}")
    return width, height, payload, dictionary, color_space


def write_pdf(destination: Path, pages: list[tuple[Path, float]], title: str = "") -> None:
    """Write a compact PDF from page JPEGs or GIMP RGB PNG exports.

    Each page is independently sized. ``resolution`` is the source page's PPI.
    PNG data is passed through with the PDF PNG predictor; JPEG is embedded as-is.
    """
    objects: list[bytes] = []

    def add(value: bytes) -> int:
        objects.append(value)
        return len(objects)

    catalog_id = add(b"")
    pages_id = add(b"")
    page_ids = []
    for index, (path, resolution) in enumerate(pages):
        width, height, payload, image_filter, color_space = _pdf_image(path)
        ppi = resolution if resolution > 0 else 72.0
        page_width = width * 72.0 / ppi
        page_height = height * 72.0 / ppi
        image_id = add(
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/BitsPerComponent 8 {image_filter} "
            f"/Length {len(payload)} >>\nstream\n".encode("ascii")
            + payload + b"\nendstream")
        content = (f"q\n{page_width:.4f} 0 0 {page_height:.4f} 0 0 cm\n"
                   f"/Im{index} Do\nQ\n").encode("ascii")
        content_id = add(f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
                         + content + b"endstream")
        page_id = add(
            f"<< /Type /Page /Parent {pages_id} 0 R "
            f"/MediaBox [0 0 {page_width:.4f} {page_height:.4f}] "
            f"/Resources << /XObject << /Im{index} {image_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("ascii"))
        page_ids.append(page_id)
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{value} 0 R' for value in page_ids)}] "
        f"/Count {len(page_ids)} >>".encode())
    title_hex = (b"\xfe\xff" + title.encode("utf-16-be")).hex().upper()
    info_id = add(f"<< /Producer (Imanganation) /Title <{title_hex}> >>".encode("ascii"))

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R "
        f"/Info {info_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    destination.write_bytes(output)
