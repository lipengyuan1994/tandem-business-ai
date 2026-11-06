# crm (M03)

Customer identity, contact points, append-only consent, notes, tags and customer timeline.
Other modules use `public.py`; raw delivery addresses stay out of model context.

- Tables: customers, contact_points, consent_records, customer_notes, tags, customer_tags, timeline_entries.
- Public: customer summaries/candidates, contact delivery lookup, current consent, timeline, customer creation/
  updates, booking/customer locks, erasure links and restriction reads. No automatic merge.
- Executors: crm.create_customer and crm.update_customer; versioned staff review and current permissions apply.
- P10-B2: `preferred_contact_channel=email|web_chat|none`, default email. New and existing customers still need
  the appropriate explicit consent; a preference is not consent. CustomerFields preserves an omitted preference
  when serializing an immutable proposal, so unrelated CRM updates cannot opt a customer back into email.
- `customer_care_contact(db,ctx,customer_id) -> CareContact|None` is an internal delivery DTO, serialized with
  customer erasure and consent/preference/contact changes. It includes first name, language, status, preference
  and a raw active email for delivery only. This address must never be passed to the AI writer.
- `proactive_service_consent` accepts a current staff/import service-update grant only. Form grants cover a
  request and cannot authorize proactive Care; `current_consent` and request replies retain their semantics.
- Published events include crm.customer_created and crm.consent_changed. Existing lifecycle exports all customer
  tables and owns redaction/restriction. Customer Care message bodies remain in existing lifecycle-owned rows.
