"""Isolated PDF parser. stdin/stdout only; limits are installed before importing pypdf."""

import io
import json
import logging
import resource
import sys


def apply_limits() -> None:
    """Linux must enforce AS; the owner-approved native macOS development exception skips AS only."""
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if sys.platform != "darwin":
            resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    except (OSError, ValueError):
        raise ValueError("knowledge.pdf_resource_limit") from None


def read_pdf() -> list[str]:
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("knowledge.pdf_too_large")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("knowledge.pdf_encrypted")
    if len(reader.pages) > 100:
        raise ValueError("knowledge.pdf_too_many_pages")
    pages: list[str] = []
    size = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        size += len(text)
        if size > 300000:
            raise ValueError("knowledge.pdf_too_much_text")
        pages.append(text)
    return pages


def extract_result() -> dict[str, object]:
    result: dict[str, object] = {}
    try:
        apply_limits()
        # The API parent emits one structured warning; parser stderr never carries document text into logs.
        result["address_space_limit_skipped"] = sys.platform == "darwin"
        result["pages"] = read_pdf()
    except Exception as exc:
        allowed = {
            "knowledge.pdf_encrypted",
            "knowledge.pdf_too_many_pages",
            "knowledge.pdf_too_much_text",
            "knowledge.pdf_too_large",
            "knowledge.pdf_resource_limit",
        }
        result["error"] = str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else "knowledge.invalid_pdf"
    return result


if __name__ == "__main__":
    print(json.dumps(extract_result(), ensure_ascii=False))
