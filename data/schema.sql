-- Synthetic data contract v1 (2026-10-25 UTC).
-- Portable SQL subset that validates data/fixtures.json in SQLite. NOT the production schema:
-- production tables are created by Alembic migrations (docs/roadmap/05-data-model.md), which also add
-- UUID keys, versions, timestamps, row-level security, exclusion constraints and grants.
-- IDs here are stable fixture keys; the seed command maps them to UUIDs with platform.ids.fixture_id().
PRAGMA foreign_keys = ON;

CREATE TABLE businesses (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, timezone TEXT NOT NULL,
  currency TEXT NOT NULL CHECK (length(currency) = 3), locale TEXT NOT NULL,
  is_mock INTEGER NOT NULL CHECK (is_mock = 1)
);
CREATE TABLE locations (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id), name TEXT NOT NULL,
  timezone TEXT NOT NULL, is_default INTEGER NOT NULL CHECK (is_default IN (0,1)),
  PRIMARY KEY (business_id, id)
);
CREATE TABLE opening_hours (
  id TEXT NOT NULL, business_id TEXT NOT NULL, location_id TEXT NOT NULL,
  weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6), opens TEXT NOT NULL, closes TEXT NOT NULL CHECK (closes > opens),
  PRIMARY KEY (business_id, id), FOREIGN KEY (business_id, location_id) REFERENCES locations(business_id, id)
);

-- Global users (identity). Emails must be @example.com (checked by the validator).
CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, is_mock INTEGER NOT NULL CHECK (is_mock = 1));
CREATE TABLE memberships (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id), user_id TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL CHECK (status IN ('invited','active','suspended','removed')), title TEXT,
  PRIMARY KEY (business_id, id), UNIQUE (business_id, user_id)
);
CREATE TABLE role_grants (
  id TEXT NOT NULL, business_id TEXT NOT NULL, membership_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner','admin','operator','inventory_manager','marketing_manager','auditor')),
  PRIMARY KEY (business_id, id), UNIQUE (business_id, membership_id, role),
  FOREIGN KEY (business_id, membership_id) REFERENCES memberships(business_id, id)
);

-- CRM
CREATE TABLE customers (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id),
  kind TEXT NOT NULL CHECK (kind IN ('person','organization')), display_name TEXT NOT NULL, timezone TEXT,
  source TEXT NOT NULL CHECK (source IN ('staff','public_chat','import','assistant')),
  status TEXT NOT NULL CHECK (status IN ('active','archived','merged','erased')),
  PRIMARY KEY (business_id, id)
);
CREATE TABLE contact_points (
  id TEXT NOT NULL, business_id TEXT NOT NULL, customer_id TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('email','phone')), value TEXT NOT NULL, is_primary INTEGER NOT NULL CHECK (is_primary IN (0,1)),
  PRIMARY KEY (business_id, id), UNIQUE (business_id, type, value),
  FOREIGN KEY (business_id, customer_id) REFERENCES customers(business_id, id)
);
CREATE TABLE consent_records (
  id TEXT NOT NULL, business_id TEXT NOT NULL, customer_id TEXT NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('email','sms')), purpose TEXT NOT NULL CHECK (purpose IN ('marketing','service_updates')),
  status TEXT NOT NULL CHECK (status IN ('granted','denied','withdrawn')),
  source TEXT NOT NULL CHECK (source IN ('form','staff','import','unsubscribe_link')), evidence TEXT NOT NULL, recorded_at TEXT NOT NULL,
  PRIMARY KEY (business_id, id), FOREIGN KEY (business_id, customer_id) REFERENCES customers(business_id, id)
);
CREATE TABLE tags (id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id), name TEXT NOT NULL, PRIMARY KEY (business_id, id), UNIQUE (business_id, name));
CREATE TABLE customer_tags (
  business_id TEXT NOT NULL, customer_id TEXT NOT NULL, tag_id TEXT NOT NULL, PRIMARY KEY (business_id, customer_id, tag_id),
  FOREIGN KEY (business_id, customer_id) REFERENCES customers(business_id, id), FOREIGN KEY (business_id, tag_id) REFERENCES tags(business_id, id)
);

-- Catalog
CREATE TABLE catalog_items (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id),
  kind TEXT NOT NULL CHECK (kind IN ('service','product')), sku TEXT, name TEXT NOT NULL, unit TEXT NOT NULL,
  price_minor INTEGER, currency TEXT NOT NULL, duration_minutes INTEGER, buffer_minutes INTEGER NOT NULL DEFAULT 0,
  resource_kind TEXT CHECK (resource_kind IN ('staff','room','equipment')),
  is_stock_tracked INTEGER NOT NULL CHECK (is_stock_tracked IN (0,1)), is_bookable_online INTEGER NOT NULL CHECK (is_bookable_online IN (0,1)),
  status TEXT NOT NULL CHECK (status IN ('active','archived')),
  CHECK ((kind = 'service' AND duration_minutes BETWEEN 5 AND 1440 AND resource_kind IS NOT NULL AND is_stock_tracked = 0)
      OR (kind = 'product' AND duration_minutes IS NULL)),
  PRIMARY KEY (business_id, id), UNIQUE (business_id, sku)
);
CREATE TABLE service_parts (
  id TEXT NOT NULL, business_id TEXT NOT NULL, service_id TEXT NOT NULL, item_id TEXT NOT NULL, quantity NUMERIC NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (business_id, id), UNIQUE (business_id, service_id, item_id),
  FOREIGN KEY (business_id, service_id) REFERENCES catalog_items(business_id, id), FOREIGN KEY (business_id, item_id) REFERENCES catalog_items(business_id, id)
);

-- Scheduling
CREATE TABLE resources (
  id TEXT NOT NULL, business_id TEXT NOT NULL, location_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('staff','room','equipment')), name TEXT NOT NULL, user_id TEXT REFERENCES users(id),
  status TEXT NOT NULL CHECK (status IN ('active','archived')),
  PRIMARY KEY (business_id, id), FOREIGN KEY (business_id, location_id) REFERENCES locations(business_id, id)
);
CREATE TABLE availability_rules (
  id TEXT NOT NULL, business_id TEXT NOT NULL, resource_id TEXT NOT NULL,
  weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6), start_time TEXT NOT NULL, end_time TEXT NOT NULL CHECK (end_time > start_time),
  PRIMARY KEY (business_id, id), FOREIGN KEY (business_id, resource_id) REFERENCES resources(business_id, id)
);
CREATE TABLE appointments (
  id TEXT NOT NULL, business_id TEXT NOT NULL, customer_id TEXT NOT NULL, service_id TEXT NOT NULL, resource_id TEXT NOT NULL, location_id TEXT NOT NULL,
  starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, blocked_until TEXT NOT NULL, timezone TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('held','confirmed','cancelled','completed','no_show')),
  source TEXT NOT NULL CHECK (source IN ('staff','assistant','public_request')),
  CHECK (ends_at > starts_at AND blocked_until >= ends_at),
  PRIMARY KEY (business_id, id),
  FOREIGN KEY (business_id, customer_id) REFERENCES customers(business_id, id),
  FOREIGN KEY (business_id, service_id) REFERENCES catalog_items(business_id, id),
  FOREIGN KEY (business_id, resource_id) REFERENCES resources(business_id, id),
  FOREIGN KEY (business_id, location_id) REFERENCES locations(business_id, id)
);

-- Inventory (levels are derived from movements and reservations by the validator and the app)
CREATE TABLE stock_locations (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id), name TEXT NOT NULL, is_default INTEGER NOT NULL CHECK (is_default IN (0,1)),
  PRIMARY KEY (business_id, id)
);
CREATE TABLE stock_movements (
  id TEXT NOT NULL, business_id TEXT NOT NULL, item_id TEXT NOT NULL, stock_location_id TEXT NOT NULL,
  quantity NUMERIC NOT NULL CHECK (quantity <> 0),
  reason TEXT NOT NULL CHECK (reason IN ('receipt','consumption','sale','adjustment_gain','adjustment_loss','count_correction','transfer_in','transfer_out','return')),
  note TEXT, occurred_at TEXT NOT NULL,
  PRIMARY KEY (business_id, id),
  FOREIGN KEY (business_id, item_id) REFERENCES catalog_items(business_id, id),
  FOREIGN KEY (business_id, stock_location_id) REFERENCES stock_locations(business_id, id)
);
CREATE TABLE stock_reservations (
  id TEXT NOT NULL, business_id TEXT NOT NULL, item_id TEXT NOT NULL, stock_location_id TEXT NOT NULL,
  quantity NUMERIC NOT NULL CHECK (quantity > 0), status TEXT NOT NULL CHECK (status IN ('active','released','consumed')),
  ref_type TEXT NOT NULL CHECK (ref_type IN ('appointment','sales_order')), ref_id TEXT NOT NULL,
  PRIMARY KEY (business_id, id),
  FOREIGN KEY (business_id, item_id) REFERENCES catalog_items(business_id, id),
  FOREIGN KEY (business_id, stock_location_id) REFERENCES stock_locations(business_id, id)
);
CREATE TABLE reorder_rules (
  id TEXT NOT NULL, business_id TEXT NOT NULL, item_id TEXT NOT NULL, stock_location_id TEXT NOT NULL,
  reorder_point NUMERIC NOT NULL CHECK (reorder_point >= 0), target_level NUMERIC NOT NULL CHECK (target_level > reorder_point),
  lead_time_days INTEGER NOT NULL CHECK (lead_time_days >= 0),
  PRIMARY KEY (business_id, id), UNIQUE (business_id, item_id, stock_location_id),
  FOREIGN KEY (business_id, item_id) REFERENCES catalog_items(business_id, id),
  FOREIGN KEY (business_id, stock_location_id) REFERENCES stock_locations(business_id, id)
);

-- Conversations
CREATE TABLE conversations (
  id TEXT NOT NULL, business_id TEXT NOT NULL, customer_id TEXT,
  channel TEXT NOT NULL CHECK (channel IN ('web_chat','phone_log','walk_in','email','voice')),
  status TEXT NOT NULL CHECK (status IN ('open','waiting_on_staff','waiting_on_customer','closed')),
  subject TEXT, visitor_name TEXT, visitor_email TEXT, last_message_at TEXT NOT NULL,
  PRIMARY KEY (business_id, id), FOREIGN KEY (business_id, customer_id) REFERENCES customers(business_id, id)
);
CREATE TABLE messages (
  id TEXT NOT NULL, business_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('inbound','outbound','internal')),
  author_kind TEXT NOT NULL CHECK (author_kind IN ('visitor','customer','staff','assistant','system')), author_user_id TEXT REFERENCES users(id),
  body TEXT NOT NULL CHECK (length(body) <= 8000), created_at TEXT NOT NULL,
  publication_state TEXT NOT NULL DEFAULT 'published' CHECK (publication_state IN ('withheld','published','stopped','cancelled')),
  publish_at TEXT, published_at TEXT, answers_message_id TEXT, cancellation_reason TEXT,
  PRIMARY KEY (business_id, id), FOREIGN KEY (business_id, conversation_id) REFERENCES conversations(business_id, id)
);

-- Approvals (one staff-originated pending proposal; no decisions or executions are pre-seeded)
CREATE TABLE autonomy_rules (
  id TEXT NOT NULL, business_id TEXT NOT NULL, action_type TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('off','draft','ask','act_notify','act')),
  shadow INTEGER NOT NULL DEFAULT 1 CHECK (shadow IN (0,1)),
  daily_cap INTEGER NOT NULL DEFAULT 10 CHECK (daily_cap >= 0),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  granted_by TEXT NOT NULL REFERENCES users(id),
  PRIMARY KEY (business_id,id), UNIQUE (business_id,action_type),
  FOREIGN KEY (business_id) REFERENCES businesses(id)
);

CREATE TABLE action_proposals (
  id TEXT NOT NULL, business_id TEXT NOT NULL, action_type TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending_review','approved','rejected','expired','cancelled','executing','succeeded','failed')),
  origin_kind TEXT NOT NULL CHECK (origin_kind IN ('staff','assistant','rule')), origin_user_id TEXT REFERENCES users(id),
  title TEXT NOT NULL, expires_at TEXT NOT NULL,
  PRIMARY KEY (business_id, id)
);
CREATE TABLE proposal_revisions (
  id TEXT NOT NULL, business_id TEXT NOT NULL, proposal_id TEXT NOT NULL, revision_no INTEGER NOT NULL CHECK (revision_no >= 1),
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)), summary TEXT NOT NULL,
  PRIMARY KEY (business_id, id), UNIQUE (business_id, proposal_id, revision_no),
  FOREIGN KEY (business_id, proposal_id) REFERENCES action_proposals(business_id, id)
);

-- Marketing
CREATE TABLE segments (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id), name TEXT NOT NULL,
  definition_json TEXT NOT NULL CHECK (json_valid(definition_json)), PRIMARY KEY (business_id, id)
);
CREATE TABLE campaigns (
  id TEXT NOT NULL, business_id TEXT NOT NULL, name TEXT NOT NULL, purpose TEXT NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('email')), segment_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','in_review','approved','sending','sent','cancelled')),
  PRIMARY KEY (business_id, id), FOREIGN KEY (business_id, segment_id) REFERENCES segments(business_id, id)
);
CREATE TABLE campaign_revisions (
  id TEXT NOT NULL, business_id TEXT NOT NULL, campaign_id TEXT NOT NULL, revision_no INTEGER NOT NULL,
  subject TEXT NOT NULL, body_markdown TEXT NOT NULL, created_by_kind TEXT NOT NULL CHECK (created_by_kind IN ('staff','assistant')),
  PRIMARY KEY (business_id, id), UNIQUE (business_id, campaign_id, revision_no),
  FOREIGN KEY (business_id, campaign_id) REFERENCES campaigns(business_id, id)
);
CREATE TABLE suppressions (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id), channel TEXT NOT NULL CHECK (channel IN ('email','sms')),
  value_normalized TEXT NOT NULL, reason TEXT NOT NULL CHECK (reason IN ('unsubscribed','bounced','complaint','manual')),
  PRIMARY KEY (business_id, id), UNIQUE (business_id, channel, value_normalized)
);


-- P10-C1 owner-written knowledge. Runtime version rows are created through the seed service.
CREATE TABLE kb_articles (
  id TEXT NOT NULL, business_id TEXT NOT NULL REFERENCES businesses(id),
  title TEXT NOT NULL, slug TEXT NOT NULL, visibility TEXT NOT NULL CHECK (visibility IN ('public','internal')),
  status TEXT NOT NULL CHECK (status IN ('draft','published','archived')),
  body_markdown TEXT NOT NULL CHECK (length(body_markdown) <= 20000),
  tags_json TEXT NOT NULL CHECK (json_valid(tags_json)), is_mock INTEGER NOT NULL CHECK (is_mock=1),
  PRIMARY KEY (business_id,id), UNIQUE (business_id,slug)
);
