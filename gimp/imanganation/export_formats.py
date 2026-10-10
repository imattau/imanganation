"""Small, dependency-free comic export helpers (ZIP/CBZ and raster PDF)."""

from __future__ import annotations

import re
import struct
import zlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree


VERTICAL_PRESETS = {
    "custom": {"label": "Custom", "width": 800, "slice_height": 1280,
               "max_file_mb": 0, "max_episode_mb": 0},
    "webtoon": {"label": "WEBTOON CANVAS", "width": 800, "slice_height": 1280,
                "max_file_mb": 2, "max_episode_mb": 20},
    "tapas": {"label": "Tapas", "width": 940, "slice_height": 5000,
              "max_file_mb": 10, "max_episode_mb": 0},
}


def _png_rgb(path: Path):
    """Decode an 8-bit, non-interlaced RGB/RGBA/grayscale PNG to RGB rows."""
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Vertical export needs PNG source pages: {path.name}")
    offset, width, height, depth, color, interlace = 8, 0, 0, 0, 0, 0
    compressed = []
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + size]
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            compressed.append(chunk)
        elif kind == b"IEND":
            break
        offset += size + 12
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if not width or not height or depth != 8 or channels is None or interlace:
        raise ValueError(f"Unsupported PNG encoding in {path.name}")
    raw = zlib.decompress(b"".join(compressed))
    stride = width * channels
    previous = bytearray(stride)
    rows = []
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        scan = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        for i in range(stride):
            left = scan[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                scan[i] = (scan[i] + left) & 255
            elif filter_type == 2:
                scan[i] = (scan[i] + up) & 255
            elif filter_type == 3:
                scan[i] = (scan[i] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                scan[i] = (scan[i] + (left if pa <= pb and pa <= pc else
                                      up if pb <= pc else upper_left)) & 255
            elif filter_type != 0:
                raise ValueError(f"Unsupported PNG filter in {path.name}")
        if color == 2:
            rows.append(bytes(scan))
        else:
            rgb = bytearray(width * 3)
            for x in range(width):
                if color == 0:
                    value = scan[x]
                    rgb[x * 3:x * 3 + 3] = bytes((value, value, value))
                elif color == 4:
                    value, alpha = scan[x * 2:x * 2 + 2]
                    value = (value * alpha + 255 * (255 - alpha)) // 255
                    rgb[x * 3:x * 3 + 3] = bytes((value, value, value))
                else:
                    red, green, blue, alpha = scan[x * 4:x * 4 + 4]
                    rgb[x * 3:x * 3 + 3] = bytes((
                        (red * alpha + 255 * (255 - alpha)) // 255,
                        (green * alpha + 255 * (255 - alpha)) // 255,
                        (blue * alpha + 255 * (255 - alpha)) // 255))
            rows.append(bytes(rgb))
        previous = scan
    return width, height, rows


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        header = source.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"Vertical export needs PNG source pages: {path.name}")
    return struct.unpack(">II", header[16:24])


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def _write_rgb_png(path: Path, width: int, height: int, rows) -> None:
    compressor = zlib.compressobj(6)
    payload = bytearray()
    for row in rows:
        payload.extend(compressor.compress(b"\0" + row))
    payload.extend(compressor.flush())
    path.write_bytes(b"\x89PNG\r\n\x1a\n" +
                     _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
                     _png_chunk(b"IDAT", bytes(payload)) + _png_chunk(b"IEND", b""))


def write_vertical_slices(page_files: list[tuple[str, Path]], destination: Path, *,
                          width: int = 800, slice_height: int = 1280,
                          gap: int = 48, prefix: str = "episode") -> list[Path]:
    """Scale page PNGs to a common width, stack with white gaps, and split to PNGs.

    Slices are cut at fixed heights. Every emitted file stays within width and
    slice_height; the final slice may be shorter. The gap is between page images.
    """
    if not 1 <= width <= 10000 or not 1 <= slice_height <= 50000 or not 0 <= gap <= 2000:
        raise ValueError("Width, slice height, or page gap is outside the supported range")
    if not page_files:
        raise ValueError("Choose at least one page for vertical export")
    pages = []
    total_height = 0
    page_ends = []
    for _, path in page_files:
        source_width, source_height = _png_dimensions(path)
        scaled_height = max(1, round(source_height * width / source_width))
        pages.append((path, source_width, source_height, scaled_height))
        total_height += scaled_height
        page_ends.append(total_height)
        if len(page_ends) < len(page_files):
            total_height += gap
    if width * slice_height > 40_000_000:
        raise ValueError("Slice dimensions are too large to export safely")
    destination.mkdir(parents=True, exist_ok=False)
    outputs = []
    slice_ranges = []
    top = 0
    while top < total_height:
        limit = min(total_height, top + slice_height)
        boundaries = [end for end in page_ends if top < end <= limit]
        # Prefer a page boundary when it leaves at least 70% of the slice filled.
        # Long pages still split at the fixed height limit.
        bottom = (boundaries[-1] if boundaries and boundaries[-1] >= top + slice_height * 0.7
                  else limit)
        slice_ranges.append((top, bottom))
        top = bottom
    for slice_index, (top, bottom) in enumerate(slice_ranges, 1):

        def rows_for_slice():
            global_y = 0
            for page_index, (path, source_width, source_height, scaled_height) in enumerate(pages):
                page_bottom = global_y + scaled_height
                start, end = max(top, global_y), min(bottom, page_bottom)
                if start < end:
                    _, _, rows = _png_rgb(path)
                    for y in range(start - global_y, end - global_y):
                        source_y = max(0.0, min(source_height - 1,
                                               (y + 0.5) * source_height / scaled_height - 0.5))
                        y0, y1 = int(source_y), min(source_height - 1, int(source_y) + 1)
                        fy = source_y - y0
                        row0, row1 = rows[y0], rows[y1]
                        if source_width == width:
                            yield row0
                        else:
                            out = bytearray(width * 3)
                            for x in range(width):
                                source_x = max(0.0, min(source_width - 1,
                                                       (x + 0.5) * source_width / width - 0.5))
                                x0, x1 = int(source_x), min(source_width - 1, int(source_x) + 1)
                                fx = source_x - x0
                                for channel in range(3):
                                    a = row0[x0 * 3 + channel] * (1 - fx) + row0[x1 * 3 + channel] * fx
                                    b = row1[x0 * 3 + channel] * (1 - fx) + row1[x1 * 3 + channel] * fx
                                    out[x * 3 + channel] = round(a * (1 - fy) + b * fy)
                            yield bytes(out)
                global_y = page_bottom
                if page_index < len(pages) - 1:
                    gap_start, gap_end = global_y, global_y + gap
                    count = max(0, min(bottom, gap_end) - max(top, gap_start))
                    white = bytes([255]) * (width * 3)
                    for _ in range(count):
                        yield white
                    global_y = gap_end

        out = destination / f"{prefix}-{slice_index:03d}.png"
        _write_rgb_png(out, width, bottom - top, rows_for_slice())
        outputs.append(out)
    return outputs


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
