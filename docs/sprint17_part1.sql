-- Sprint 17: Reusable Entity Picker + CRM Docs Tab
-- This Part 1 SQL was applied to the live database before Sprint 17 code work began.
-- DO NOT run again — it is retained for reference only.

-- ============================================================
-- entities: stub / picker completeness columns
-- ============================================================
ALTER TABLE entities
  ADD COLUMN IF NOT EXISTS is_incomplete boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS created_via text;

-- ============================================================
-- entity_documents
-- ============================================================
CREATE TABLE IF NOT EXISTS entity_documents (
  id               uuid        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id           uuid        NOT NULL,
  entity_id        uuid        NOT NULL REFERENCES entities(id),
  title            text        NOT NULL,
  doc_category     text        NOT NULL,
  file_name        text        NOT NULL,
  file_type        text,
  file_size_bytes  bigint,
  r2_key           text        NOT NULL,
  r2_bucket        text        NOT NULL DEFAULT '2ndactcapital-docs',
  version          integer     NOT NULL DEFAULT 1,
  supersedes_id    uuid        REFERENCES entity_documents(id),
  status           text        NOT NULL DEFAULT 'active',
  uploaded_by      uuid,
  created_at       timestamp with time zone NOT NULL DEFAULT now(),
  updated_at       timestamp with time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS entity_documents_entity_id_idx
  ON entity_documents (entity_id, status, created_at DESC);

-- ============================================================
-- entity_document_tags
-- ============================================================
CREATE TABLE IF NOT EXISTS entity_document_tags (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  document_id  uuid NOT NULL REFERENCES entity_documents(id) ON DELETE CASCADE,
  tag          text NOT NULL,
  created_at   timestamp with time zone NOT NULL DEFAULT now(),
  UNIQUE (document_id, tag)
);

-- ============================================================
-- Seed: doc_category (12 items)
-- ============================================================
INSERT INTO reference_data (list_key, code, label, display_order) VALUES
  ('doc_category', 'id_passport',         'Passport / ID',           1),
  ('doc_category', 'id_drivers_license',  "Driver's License",        2),
  ('doc_category', 'tax_return',          'Tax Return',              3),
  ('doc_category', 'w9',                  'W-9 Form',                4),
  ('doc_category', 'subscription_docs',   'Subscription Documents',  5),
  ('doc_category', 'operating_agreement', 'Operating Agreement',     6),
  ('doc_category', 'trust_agreement',     'Trust Agreement',         7),
  ('doc_category', 'articles_of_incorp',  'Articles of Incorporation', 8),
  ('doc_category', 'bank_statement',      'Bank Statement',          9),
  ('doc_category', 'accreditation',       'Accreditation Letter',   10),
  ('doc_category', 'kyc_aml',             'KYC / AML Document',     11),
  ('doc_category', 'other',               'Other',                  12)
ON CONFLICT (list_key, code) DO NOTHING;
