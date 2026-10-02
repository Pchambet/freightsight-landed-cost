#!/usr/bin/env python3
"""Regenerate the demo invoice fixture (Transdemo, MSCU4821990).

The figures and wording live in one place, `app.domain.sample_data.demo_invoice_lines()`, so this
script and `backend/tests/test_demo_story.py` can never quote different numbers for the same invoice
(the failure the 2026-09-17 audit found: the demo walkthrough said 13,91 €, the screen said 11,80 €).

The invoice date defaults to 3 days before the day this script runs, which is what keeps the fixture
plausible next to a container whose ETD/ETA/discharge dates are always computed relative to "today"
(see sample_data.py). Regenerate before any demo held long after this file's last commit — there is no
hard rule for "too old", but "the invoice predates the ship leaving port" (the bug this fixture used to
have) is the thing to avoid; a few months of drift is still fine, half a year is not.

Usage:
    backend/.venv/bin/python docs/demo/fixtures/generate_invoice.py

Requires Google Chrome (headless print-to-pdf, no other dependency) and a backend/.venv with this
repo's dependencies installed, only to import the four figures above — nothing here touches a database.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))

from app.domain.sample_data import (
    DEMO_INVOICE_CUSTOMS_BROKERAGE,
    DEMO_INVOICE_CUSTOMS_DUTY,
    DEMO_INVOICE_DRAYAGE,
    DEMO_INVOICE_NUMBER,
    DEMO_INVOICE_OCEAN_FREIGHT,
    DEMO_INVOICE_SUBTOTAL,
    DEMO_INVOICE_THC,
    DEMO_INVOICE_TOTAL,
    DEMO_INVOICE_VAT,
    DEMO_INVOICE_VENDOR,
    demo_invoice_lines,
)

HERE = Path(__file__).parent
HTML_PATH = HERE / "facture-transdemo-demo.html"
PDF_PATH = HERE / "facture-transdemo-demo.pdf"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
]


def _find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise SystemExit("Google Chrome not found — install it, or edit CHROME_CANDIDATES in this script")


def render_html(invoice_date: date) -> str:
    """One line of text per row, on purpose: this is what the regex extractor
    (backend/app/adapters/extraction/regex_extractor.py) reads back, and what
    backend/tests/test_demo_story.py builds its own test PDF from via demo_invoice_lines() — a
    multi-cell HTML table risks Chrome emitting the description and the amount as separate text runs
    on separate lines, which the extractor would then miss entirely.
    """
    lines = demo_invoice_lines(invoice_date)
    # The first line is the vendor name (rendered as the <h1> below); a blank line then starts the
    # charge table, with the rest of the header in between.
    blank_at = lines.index("")
    header, table = lines[1:blank_at], lines[blank_at + 1 :]
    header_html = "\n".join(f"<p>{line}</p>" for line in header if line)
    table_html = "\n".join(f"<div>{line if line else '&nbsp;'}</div>" for line in table)
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{DEMO_INVOICE_NUMBER}</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; color: #111; margin: 48px; }}
  h1 {{ font-size: 20px; margin: 0 0 24px; }}
  p {{ margin: 2px 0; font-size: 13px; }}
  .table {{ font-family: "Courier New", monospace; font-size: 12.5px; white-space: pre;
            margin-top: 24px; line-height: 1.5; }}
</style>
</head>
<body>
  <h1>{DEMO_INVOICE_VENDOR}</h1>
  {header_html}
  <div class="table">
{table_html}
  </div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=datetime.now(UTC).date() - timedelta(days=3),
        help="invoice date, ISO format (default: 3 days before today)",
    )
    args = parser.parse_args()

    html = render_html(args.date)
    HTML_PATH.write_text(html, encoding="utf-8")

    chrome = _find_chrome()
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={PDF_PATH}",
            HTML_PATH.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    print(f"wrote {HTML_PATH}")
    print(f"wrote {PDF_PATH}")
    print(f"invoice date: {args.date.isoformat()}")
    print(
        f"HT {DEMO_INVOICE_SUBTOTAL} + TVA {DEMO_INVOICE_VAT} = TTC {DEMO_INVOICE_TOTAL} "
        f"(freight {DEMO_INVOICE_OCEAN_FREIGHT}, THC {DEMO_INVOICE_THC}, "
        f"brokerage {DEMO_INVOICE_CUSTOMS_BROKERAGE}, haulage {DEMO_INVOICE_DRAYAGE}, "
        f"duty {DEMO_INVOICE_CUSTOMS_DUTY})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
