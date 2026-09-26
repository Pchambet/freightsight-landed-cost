"""Minimal PDF writer: one page of text, no dependency.

A real forwarder invoice is a page of tables; what matters for extraction is the text layer, and a
hand-built PDF gives exactly that with no dependency and no binary blob in the repository. It serves
the invoice fixtures of the tests, and the sample data, which hands the product a document to read
rather than lines it pretends to have read.
"""

from __future__ import annotations


def make_pdf(lines: list[str]) -> bytes:
    operations = ["BT", "/F1 11 Tf", "1 0 0 1 40 800 Tm", "14 TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        operations += [f"({escaped}) Tj", "T*"]
    operations.append("ET")
    stream = "\n".join(operations).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
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
