"""The model-based extractor, used when a key exists.

What it is allowed to do is narrow on purpose: it reads text out of the PDF, it is handed the
organization's own container and PO numbers, and it answers a fixed JSON shape that pydantic
validates. Anything it invents beyond that list is dropped by `domain/invoices/checks.py`, and
nothing it says ever becomes a cost without a person accepting the line.

Amounts are requested as strings, and stay strings all the way to Decimal. A model that writes 1234.5
must not be able to make that 1234.50 by accident on the way through a float.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.http import timeout as http_timeout
from app.domain.invoices.ports import (
    ExtractionContext,
    ExtractionFailed,
    ExtractionInput,
    ExtractorNotConfigured,
    FreightInvoice,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_TEXT_CHARS = 40_000

SYSTEM = """You read freight forwarder invoices and return structured data. Rules:
- Amounts are strings with a decimal point and two decimals: "1234.56". Never a number, never a
  thousands separator, never a currency symbol.
- Use only container numbers and purchase order numbers from the lists provided. If a reference on
  the invoice is not in the list, leave the field empty; do not guess or correct it.
- One line per charge. Do not include totals, subtotals or VAT as charges.
- Keep the sign. A refund, a discount and every figure on a credit note ("AVOIR", "CREDIT NOTE")
  keep their minus: "-2450.00". Never return the absolute value of a negative amount, and never
  turn a document that refunds money into one that asks for it.
- subtotal is the pre-tax total the invoice itself prints ("Total HT", "Subtotal", "Net"), copied as
  written. Leave it null when the invoice does not print one; never compute it, and never subtract
  the VAT from the total to obtain it.
- cost_type must be one of the given values, or null when you are not sure.
- Set confidence honestly: 0.9 when the line is unambiguous, 0.3 when you are guessing.
Return only the JSON object, with no commentary."""

SCHEMA_HINT = """{
  "vendor": str|null, "invoice_number": str|null, "invoice_date": "YYYY-MM-DD"|null,
  "currency": "EUR"|..., "total": str|null, "subtotal": str|null, "vat": str|null,
  "lines": [{"description": str, "amount": str, "currency": str|null,
             "cost_type": str|null, "container_number": str|null, "po_number": str|null,
             "confidence": float}],
  "container_numbers": [str], "bl_numbers": [str], "po_numbers": [str], "confidence": float
}"""


#: A model reading an invoice takes its time; reaching the API does not. Before the two were
#: separated, an endpoint that had gone dark cost two minutes of a worker per invoice.
TIMEOUT_SECONDS = 120.0


class LlmExtractor:
    name = "llm"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        api_url: str = API_URL,
        client: httpx.Client | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.api_url = api_url
        self.max_tokens = max_tokens
        self._client = client

    def extract(self, document: ExtractionInput, context: ExtractionContext) -> FreightInvoice:
        if not self.api_key:
            raise ExtractorNotConfigured(
                "EXTRACTION_API_KEY is not set: this deployment reads invoices with the pattern "
                "extractor only"
            )
        from app.adapters.extraction.regex_extractor import text_of

        text = text_of(document)[:MAX_TEXT_CHARS]
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": self._prompt(text, context)}],
        }
        client = self._client or httpx.Client(timeout=http_timeout(TIMEOUT_SECONDS))
        try:
            response = client.post(
                self.api_url,
                json=payload,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ExtractionFailed(f"The extraction service is unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ExtractionFailed(
                f"The extraction service answered {response.status_code}", code="EXTRACTOR_ERROR"
            )
        return self._parse(response.json())

    def _prompt(self, text: str, context: ExtractionContext) -> str:
        containers = ", ".join(sorted(context.containers)) or "(none)"
        purchase_orders = ", ".join(sorted(context.purchase_orders)) or "(none)"
        return (
            f"Base currency: {context.base_currency}\n"
            f"Containers of this company: {containers}\n"
            f"Purchase orders of this company: {purchase_orders}\n\n"
            f"Return this JSON shape:\n{SCHEMA_HINT}\n\n"
            f"Invoice text:\n---\n{text}\n---"
        )

    def _parse(self, body: dict[str, Any]) -> FreightInvoice:
        blocks = body.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip()
        if text.startswith("```"):  # models like their fences
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if text.lower().startswith("json") else text
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ExtractionFailed("The extraction service did not return JSON", code="EXTRACTOR_ERROR")
        try:
            data = json.loads(text[start : end + 1])
        except ValueError as exc:
            raise ExtractionFailed(f"The extraction service returned broken JSON: {exc}") from exc
        try:
            return FreightInvoice.model_validate(data)
        except ValidationError as exc:
            # A model that answers off-shape is a failed reading, not a half-trusted one.
            raise ExtractionFailed(
                f"The extraction service returned an unusable shape: {exc.error_count()} problem(s)",
                code="EXTRACTOR_SHAPE",
            ) from exc
