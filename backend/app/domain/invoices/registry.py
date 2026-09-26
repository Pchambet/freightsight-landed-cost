"""Which extractor this deployment has, and the order they are tried in."""

from __future__ import annotations

from app.core.settings import Settings, get_settings
from app.domain.invoices.ports import DocumentExtractor


def get_extractors(settings: Settings | None = None) -> list[DocumentExtractor]:
    """The model first when there is a key, the patterns always — a reading beats no reading."""
    settings = settings or get_settings()
    from app.adapters.extraction.regex_extractor import RegexExtractor

    extractors: list[DocumentExtractor] = []
    if settings.extraction_api_key:
        from app.adapters.extraction.llm_extractor import LlmExtractor

        extractors.append(
            LlmExtractor(
                settings.extraction_api_key,
                settings.extraction_model,
                api_url=settings.extraction_api_url,
            )
        )
    extractors.append(RegexExtractor())
    return extractors
