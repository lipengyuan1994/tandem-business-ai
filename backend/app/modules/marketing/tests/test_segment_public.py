"""The campaign writer's public segment metadata never expands an audience."""

import uuid

import pytest

from app.modules.marketing import public as marketing
from app.modules.marketing import service
from app.modules.marketing.schemas import SegmentDefinition, SegmentIn
from app.modules.org.testing import make_business
from app.platform.db import transaction
from app.platform.errors import Forbidden, NotFound
from app.platform.testing.auth import ctx_for


def test_get_segment_returns_only_name_and_filter_description(registry, monkeypatch):
    business = make_business()
    ctx = ctx_for(business, {"marketing.campaign.write"})
    with transaction(ctx) as db:
        row = service.create_segment(
            db,
            ctx,
            SegmentIn(
                name="Road riders",
                definition=SegmentDefinition.model_validate(
                    {
                        "all": [
                            {"field": "tag", "op": "has", "value": "road-bike"},
                            {"field": "last_interaction_at", "op": "older_than_days", "value": 180},
                        ]
                    }
                ),
            ),
        )
        sid = row.id

    def forbidden(*args, **kwargs):
        raise AssertionError("Reading segment metadata must not compute an audience")

    monkeypatch.setattr(service, "compute_audience", forbidden)
    for permission in ("marketing.campaign.read", "marketing.campaign.write"):
        reader = ctx_for(business, {permission})
        with transaction(reader) as db:
            segment = marketing.get_segment(db, reader, sid)
            assert segment.model_dump() == {
                "name": "Road riders",
                "filter_description": (
                    'All conditions must match: tag has "road-bike"; last_interaction_at older_than_days 180'
                ),
            }

    other = ctx_for(make_business(slug="other-segment-shop"), {"marketing.campaign.read"})
    with transaction(other) as db, pytest.raises(NotFound):
        marketing.get_segment(db, other, sid)
    with transaction(ctx) as db, pytest.raises(NotFound):
        marketing.get_segment(db, ctx, uuid.uuid4())
    denied = ctx_for(business)
    with transaction(denied) as db, pytest.raises(Forbidden):
        marketing.get_segment(db, denied, sid)
