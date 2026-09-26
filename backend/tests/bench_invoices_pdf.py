"""A PDF writer that places text where a real invoice places it: in columns, right-aligned amounts,
several pages — and, when asked, in the order some generators really emit it, a column at a time.

The extractor's enemy is not the wording of an invoice, it is its geometry: what `pypdf` hands back
for a table depends on where each cell sits and in which order the file draws them. A corpus of
lines of text would measure the easy half of the problem.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Helvetica advance widths, in thousandths of the font size — enough of them to right-align figures.
_WIDTHS = {" ": 278, ",": 278, ".": 278, ":": 278, "-": 333, "(": 333, ")": 333, "/": 278, "'": 191, "%": 889}
_DIGIT, _UPPER, _LOWER = 556, 680, 520


def text_width(text: str, size: float) -> float:
    total = 0
    for char in text:
        if char in _WIDTHS:
            total += _WIDTHS[char]
        elif char.isdigit():
            total += _DIGIT
        elif char.isupper():
            total += _UPPER
        else:
            total += _LOWER
    return total * size / 1000


@dataclass(frozen=True)
class Cell:
    x: float
    y: float
    text: str
    size: float = 9
    align: str = "left"  # left | right: for "right", x is where the text ends
    column: int = 0  # which column of the table the cell belongs to, for column-major emission

    @property
    def left(self) -> float:
        return self.x - text_width(self.text, self.size) if self.align == "right" else self.x


def _escape(text: str) -> bytes:
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("cp1252", errors="replace")


def make_pdf(pages: list[list[Cell]], *, column_major: bool = False) -> bytes:
    """One page per list of cells. `column_major` draws each table column whole before the next, as
    report generators that build a table out of text frames do."""
    objects: list[bytes] = [b"", b""]  # catalog and page tree, filled in below
    page_ids: list[int] = []
    font_id = 3
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for cells in pages:
        # Row by row, left to right — or a whole column before the next one.
        ordered = sorted(cells, key=lambda c: (c.column, -c.y, c.x) if column_major else (-c.y, c.left))
        operations = [b"BT"]
        for cell in ordered:
            operations.append(f"/F1 {cell.size:g} Tf".encode())
            operations.append(f"1 0 0 1 {cell.left:.2f} {cell.y:.2f} Tm".encode())
            operations.append(b"(" + _escape(cell.text) + b") Tj")
        operations.append(b"ET")
        stream = b"\n".join(operations)
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        content_id = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            + f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        page_ids.append(len(objects))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
