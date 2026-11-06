"""PDF bytes are transient; only normalized, unpublished text and bounded provenance reach storage."""

import json
import re
import subprocess
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.errors import Invalid

from . import service
from .models import Article
from .schemas import ArticleCreate

WALL_SECONDS = 25.0
EXTRACT_COMMAND = [sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py"))]
log = structlog.get_logger(__name__)


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def filename(value: str) -> str:
    value = value.replace("\\", "/").split("/")[-1]
    value = re.sub(r"[^\w .()-]", "_", value, flags=re.ASCII).strip(" .")[:116]
    return (value or "document") if value.lower().endswith(".pdf") else (value or "document") + ".pdf"


def extract(data: bytes) -> list[str]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed interpreter/worker; untrusted bytes go only to stdin
            EXTRACT_COMMAND,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=WALL_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills AND reaps the process before raising.
        raise Invalid("knowledge.pdf_timeout") from None
    if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024:
        raise Invalid("knowledge.pdf_resource_limit")
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError
        if value.get("address_space_limit_skipped") is True:
            if sys.platform != "darwin":
                raise Invalid("knowledge.pdf_resource_limit")
            log.warning(
                "knowledge_pdf_address_space_limit_skipped", platform="darwin", development_only=True, limit="RLIMIT_AS"
            )
        if "error" in value:
            allowed = {
                "knowledge.pdf_encrypted",
                "knowledge.pdf_too_many_pages",
                "knowledge.pdf_too_much_text",
                "knowledge.pdf_too_large",
                "knowledge.invalid_pdf",
                "knowledge.pdf_resource_limit",
            }
            raise Invalid(value["error"] if value["error"] in allowed else "knowledge.invalid_pdf")
        pages = value["pages"]
        if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise Invalid("knowledge.invalid_pdf") from None
    if len(pages) > 100:
        raise Invalid("knowledge.pdf_too_many_pages")
    if sum(map(len, pages)) > 300000:
        raise Invalid("knowledge.pdf_too_much_text")
    return [normalize(p) for p in pages]


@dataclass(frozen=True)
class Proposal:
    title: str
    body: str
    start: int
    end: int


def proposals(pages: list[str], name: str) -> list[Proposal]:
    if sum(map(len, pages)) > 300000:
        raise Invalid("knowledge.pdf_too_much_text")
    out: list[Proposal] = []
    # Page groups are reliable even for PDFs without structural heading information.
    for start in range(0, len(pages), 5):
        body = normalize("\n\n".join(pages[start : start + 5]))
        end = min(start + 5, len(pages))
        while body:
            cut = min(19000, len(body))
            if cut < len(body):
                boundary = body.rfind(" ", 15000, cut)
                cut = boundary if boundary > 0 else cut
            section, body = body[:cut].strip(), body[cut:].strip()
            if section:
                title = section.split("\n")[0].lstrip("# ")[:160] or Path(name).stem
                out.append(Proposal(f"{title} (p. {start + 1}-{end})"[:200], section, start + 1, end))
    if not out:
        raise Invalid("knowledge.pdf_no_text")
    return out


def create_drafts(db: Session, ctx: Context, pages: list[str], name: str) -> list[Article]:
    ctx.require("org.settings.write")
    name = filename(name)
    out = []
    for proposal in proposals(pages, name):
        article = service.create(
            db, ctx, ArticleCreate(title=proposal.title, slug=f"pdf-{uuid.uuid4().hex}", body_markdown=proposal.body)
        )
        article.source, article.source_filename = "pdf_import", name
        article.source_page_start, article.source_page_end = proposal.start, proposal.end
        out.append(article)
    db.flush()
    return out
