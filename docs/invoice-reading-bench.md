# Reading forwarder invoices: what is measured, and what is not

*[Version française](invoice-reading-bench.fr.md)*

The invoice inbox relies on a rule-based reader (`backend/app/adapters/extraction/regex_extractor.py`)
that must work with no API key, no network and without sending the document anywhere. Until 17 September 2026 its quality was not measured at all:
the repository held a single sample PDF. This page describes the bench that now measures it, the
numbers, and their limits.

## The bench

`backend/tests/bench_invoices_*.py` generates 55 PDFs built like real tables (each cell placed where it
belongs, amounts right-aligned, several pages), not lines of text:

- **9 layout families** seen on French import invoices. Five come in 4 variants (known labels, labels
  no list knows, quantities > 1, freight in dollars converted on the line): four columns (description,
  quantity, unit price, amount); VAT code after the amount (Sage, EBP); taxable and non-taxable side by
  side; disbursements separate from services; a table running over two pages with a carried-forward
  subtotal. The other four come as one invoice each: currency converted on the line, carrier statement
  in English, credit note, several containers on one invoice.
- **Every invoice twice**: drawn row by row, then column by column, which is what tools that build a
  table from text boxes do. The text then comes out of the PDF with every label first and every amount
  after. That makes (5 × 4 + 4) × 2 = 48 family invoices.
- **7 traps**, written separately and *before* checking whether the reader handled them: VAT rate
  printed to the right of the amount, a label wrapped over two lines, non-breaking spaces and the €
  sign, amounts with cents above the table (customs value, weight, exchange rate), a VAT line inside
  the table, import VAT as a disbursement, totals without the word "total" ("Montant HT",
  "Net à payer").

Scored per invoice: lines read with the right amount, invented lines, the cost type when the label
names one, the header (number, date, currency, net total, total, containers) and above all the
**silent error**: a wrong reading that the arithmetic check does not flag. A reading may be incomplete;
it may not be incomplete *without saying so*.

Run the bench: `cd backend && PYTHONPATH=tests .venv/bin/python -m bench_invoices_scoring` (prints a
table per layout family, labels in French). `tests/test_invoice_bench.py` turns it into a ratchet: what
is read today must still be read tomorrow.

## The numbers

| | Before (16 Sep) | After (17 Sep) |
|---|---|---|
| Lines read, 9 families, 48 invoices | 82 / 278 (29 %) | 278 / 278 (100 %) |
| … of which tables drawn column by column | 0 % | 100 % |
| … of which "VAT code after the amount" | 0 % | 100 % |
| Lines read, 7 traps, first blind measurement | — | 19 / 24 (79 %), 7 invented, **2 silent errors** |
| Lines read, 7 traps, after the fixes | — | 24 / 24, 0 invented, 0 silent |
| Invoices read with no error at all | 11 / 48 | 55 / 55 |
| Silent errors, 9 families, 48 invoices | 0 | 0 |

Re-run on 2 October 2026: 55 / 55 invoices, 302 / 302 lines, 0 invented, 239 / 239 cost types, 0 silent
errors.

What changed in the reader:

1. **Text is read by its geometry** (pypdf's `extraction_mode="layout"`): a table row stays a row
   whatever order the file draws its cells in, and the gap between two columns stays visible. That is
   also what tells "2   285,00" (two handling operations at 285 €) from "2 285,00".
2. **Every row of the table is a charge**, whether its label is known or not. Before, a row such as
   "port security fee", "call tax", "VGM weighing" or "commercial discount" never reached the screen:
   the arithmetic check said the invoice did not add up, and the reviewer had to find what was missing
   alone. It now arrives with no cost type and confidence 0.50 ("to review"), and a person names it.
3. The table is located by its header row (or, without a header, by its first row carrying an amount
   with cents) and stops at the first total row. Subtotals, carried-forward amounts, customs value,
   exchange rate, weight and the invoice's VAT line are not charges.
4. The amount is the last column that carries one, except when the header names the "Montant" column
   and the row has a cell in every column: a VAT rate "20,00" printed to the right is then no longer
   taken for the amount.
5. VAT code, currency or symbol after the amount; labels on two lines; container as a section title;
   credit-note numbers; import VAT as a disbursement (`IMPORT_VAT`); non-breaking, thin and narrow
   spaces; "65,000,00" (two cells run together) refused rather than read as 65 000.
6. **No readable total means an unverified reading** (`@unverified_total`, capped confidence): "it adds
   up" cannot be said of a reading that had nothing to compare against. This was the cause of the two
   silent errors on the traps.

## What these numbers do not say

- **These are not customer invoices.** They are the structures I know of, and I wrote both the corpus
  and the reader: 100 % means "these families are covered", not "100 % of invoices will be read". The 7
  traps showed it: written blind, they scored 79 % with two silent errors. The next blind set will
  score below 100 % again.
- **The real number will come from real invoices.** The bench can score them: drop `name.pdf` and
  `name.json` (number, date, currency, totals, expected lines) into
  `backend/tests/fixtures/invoices_real/`, a git-ignored folder, so nothing leaves the machine. Ten
  invoices from three different forwarders are worth more than this whole corpus.
- **A scanned PDF is not read at all** (`NO_TEXT_LAYER`): it has no text layer and the reader has no OCR.
  The screen says so and offers manual entry. Next step: local OCR (Tesseract) before the rules, measured
  on images of the same 55 PDFs.
- **Known pypdf limit**: layout text keeps the order of columns, not their alignment (the same column
  can end ten characters apart from one row to the next), and cells drawn right to left within a row
  come out glued together. The first case is handled by counting columns; the second is refused
  (unreadable amount, missing line, arithmetic check fails).
- The model-based reader (`llm_extractor.py`, enabled only when a key is set) is not measured here: the
  bench must not depend on any external service. The same corpus can score it on demand.
