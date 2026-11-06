"""Knowledge uses the real business export and customer-erasure composition."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, text

from app.modules.conversations import public as conversations
from app.modules.knowledge import public as knowledge
from app.platform.config import get_settings
from app.platform.context import system_context
from app.platform.db import transaction

from .test_data import CYCLE, GARDEN, NOW, customer, export_zip
from .test_data import demo as demo


def test_export_includes_knowledge_history_and_redacted_suggestions(demo: Any, owner_engine: Engine) -> None:
    ctx = system_context(CYCLE, frozenset({"conversations.thread.read"}))
    with owner_engine.connect() as observer:
        conv = observer.scalar(
            text("select id from conversations where business_id=:b and visitor_email='ada@example.com'"), {"b": CYCLE}
        )
    with transaction(ctx) as db:
        knowledge.suggest(db, ctx, conv, "Fit question: ada@example.com, +1 303 555 0100.")
    archive = export_zip(demo["owner"])
    manifest = json.loads(archive.read("manifest.json"))
    assert set(manifest["modules"]["knowledge"]) == {"kb_articles", "kb_article_versions", "kb_suggestions"}
    articles = json.loads(archive.read("knowledge/kb_articles.json"))
    versions = json.loads(archive.read("knowledge/kb_article_versions.json"))
    suggestions = json.loads(archive.read("knowledge/kb_suggestions.json"))
    warranty = next(row for row in articles if row["slug"] == "repair-warranty")
    history = [row for row in versions if row["article_id"] == warranty["id"]]
    assert {row["version_no"] for row in history} == {1, 2}
    assert any(row["published_at"] is None for row in history) and any(row["published_at"] for row in history)
    assert all("workmanship" in row["body_markdown"] for row in history)
    assert all(row["business_id"] == str(CYCLE) for row in [*articles, *versions, *suggestions])
    assert {row["article_id"] for row in versions} <= {row["id"] for row in articles}
    assert len(suggestions) == 1 and "[email hidden]" in suggestions[0]["question_text"]
    assert "ada@example.com" not in suggestions[0]["question_text"] and "555" not in suggestions[0]["question_text"]


def test_admin_erasure_redacts_customer_and_visitor_suggestions_only(demo: Any, owner_engine: Engine) -> None:
    owner = demo["owner"]
    ada = customer(owner, "Ada Example")
    ctx = system_context(CYCLE, frozenset({"crm.customer.read", "conversations.thread.read"}))
    with transaction(ctx) as db:
        linked = conversations.start_conversation(
            db,
            ctx,
            channel="web_chat",
            body="A private fit concern.",
            author_kind="visitor",
            customer_id=uuid.UUID(ada["id"]),
        )
    with owner_engine.connect() as observer:
        visitor = observer.scalar(
            text("select id from conversations where business_id=:b and visitor_email='ada@example.com'"), {"b": CYCLE}
        )
        unrelated = observer.scalar(
            text("select id from conversations where business_id=:b and visitor_email='eve@example.com'"), {"b": CYCLE}
        )
        foreign = observer.scalar(text("select id from conversations where business_id=:b limit 1"), {"b": GARDEN})
    rows: list[tuple[uuid.UUID, uuid.UUID, str, str]] = [
        (CYCLE, conversation, status, f"Private fit concern ({status})")
        for conversation in (linked, visitor)
        for status in ("open", "done", "dismissed")
    ]
    rows.extend(
        [(CYCLE, unrelated, "open", "Unrelated question"), (GARDEN, foreign, "open", "Ada Example foreign question")]
    )
    with owner_engine.begin() as db:
        for business, conversation, status, question in rows:
            db.execute(
                text(
                    "insert into kb_suggestions (id, business_id, conversation_id, status, question_text) "
                    "values (gen_random_uuid(),:b,:c,:s,:q)"
                ),
                {"b": business, "c": conversation, "s": status, "q": question},
            )
        before = db.execute(text("select * from kb_article_versions order by id")).all()
    response = owner.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "Ada Example"})
    assert response.status_code == 200, response.text
    assert response.json()["report"]["knowledge"]["kb_suggestions_redacted"] == 6
    with owner_engine.connect() as db:
        erased = db.execute(
            text(
                "select question_text,status,version from kb_suggestions "
                "where business_id=:b and conversation_id in (:linked,:visitor)"
            ),
            {"b": CYCLE, "linked": linked, "visitor": visitor},
        ).all()
        assert len(erased) == 6 and all(row.question_text == "[erased]" and row.version == 2 for row in erased)
        assert {row.status for row in erased} == {"done", "dismissed"}
        untouched = db.execute(
            text(
                "select question_text,version from kb_suggestions "
                "where conversation_id in (:u,:f) order by question_text"
            ),
            {"u": unrelated, "f": foreign},
        ).all()
        assert untouched == [("Ada Example foreign question", 1), ("Unrelated question", 1)]
        assert db.execute(text("select * from kb_article_versions order by id")).all() == before
    again = owner.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "anything"})
    assert again.status_code == 200 and again.json()["id"] == response.json()["id"]
    with owner_engine.connect() as db:
        assert db.scalar(text("select max(version) from kb_suggestions")) == 2


def test_retention_preserves_regulated_hold_after_profile_switch(
    demo: Any,
    owner_engine: Engine,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    ada = customer(demo["owner"], "Ada Example")
    with owner_engine.begin() as db:
        conv = db.scalar(
            text("select id from conversations where business_id=:b and visitor_email='ada@example.com'"), {"b": CYCLE}
        )
        db.execute(
            text(
                "insert into kb_suggestions (id,business_id,conversation_id,question_text,status,updated_at) "
                "values (gen_random_uuid(),:b,:c,'Retained private fit question','done',:old)"
            ),
            {"b": CYCLE, "c": conv, "old": NOW - timedelta(days=731)},
        )
        db.execute(
            text(
                "insert into kb_suggestions (id,business_id,conversation_id,question_text,status,updated_at) "
                "select gen_random_uuid(),business_id,id,'Expired unrelated question','dismissed',:old "
                "from conversations where business_id=:b and visitor_email='eve@example.com'"
            ),
            {"b": CYCLE, "old": NOW - timedelta(days=731)},
        )
    response = demo["owner"].post(
        "/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "Ada Example"}
    )
    assert response.status_code == 200 and response.json()["status"] == "restricted", response.text
    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    ctx = system_context(CYCLE)
    with transaction(ctx) as db:
        demo["registry"].jobs["knowledge.retention"].handler(db, ctx, {})
    with owner_engine.connect() as db:
        assert list(db.scalars(text("select question_text from kb_suggestions"))) == ["Retained private fit question"]
        assert db.scalar(text("select restricted_at is not null from conversations where id=:c"), {"c": conv})
