# marketing (M12) — module card

Spec: [M12 marketing](../../../../docs/roadmap/modules/M12-marketing.md).

`public.get_segment(db, ctx, segment_id) -> SegmentBrief{name, filter_description}` returns only the segment name and
its configured filter description, scoped to the business, with campaign read or write permission. It never computes
an audience or returns customer data. U5 uses this alongside `get_campaign` and `add_revision` to create draft content.
