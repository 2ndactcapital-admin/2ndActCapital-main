
-- assistant_action_catalog
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  action_key text NOT NULL
  module text NOT NULL
  description text NULL
  access_type text NOT NULL
  required_permission text NULL
  default_autonomy text NOT NULL DEFAULT 'confirm'::text
  reversible boolean NOT NULL DEFAULT false
  render_target text NOT NULL DEFAULT 'auto'::text
  is_active boolean NOT NULL DEFAULT true
  registered_at timestamp with time zone NOT NULL DEFAULT now()

-- assistant_activities
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  user_id uuid NOT NULL
  action_key text NOT NULL
  title text NOT NULL
  status text NOT NULL DEFAULT 'awaiting_review'::text
  rationale text NULL
  payload jsonb NULL
  result jsonb NULL
  reversible boolean NOT NULL DEFAULT false
  undo_token jsonb NULL
  undone_at timestamp with time zone NULL
  related_type text NULL
  related_id uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- assistant_autonomy_prefs
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  user_id uuid NOT NULL
  action_key text NOT NULL
  autonomy text NOT NULL
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- assistant_conversations
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  user_id uuid NOT NULL
  title text NULL
  messages jsonb NOT NULL DEFAULT '[]'::jsonb
  context_ref jsonb NULL
  status text NOT NULL DEFAULT 'active'::text
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- audit_log
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  user_id uuid NULL
  action text NOT NULL
  resource_type text NULL
  resource_id uuid NULL
  payload jsonb NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- compliance_override_requests
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  deal_id uuid NOT NULL
  user_id uuid NOT NULL
  entity_id uuid NULL
  request_notes text NULL
  status text NOT NULL DEFAULT 'pending'::text
  reviewed_by uuid NULL
  review_notes text NULL
  reviewed_at timestamp with time zone NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- compliance_records
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  kyc_status USER-DEFINED NOT NULL DEFAULT 'not_started'::kyc_status
  kyc_verified_date date NULL
  kyc_verified_by uuid NULL
  ofac_screen_status USER-DEFINED NOT NULL DEFAULT 'not_screened'::ofac_status
  ofac_screen_date timestamp with time zone NULL
  aml_risk_rating USER-DEFINED NOT NULL DEFAULT 'low'::aml_risk_rating
  accreditation_status USER-DEFINED NOT NULL DEFAULT 'not_verified'::accreditation_status
  accreditation_basis text NULL
  accreditation_verified_date date NULL
  next_reverification_due date NULL
  pep_status boolean NOT NULL DEFAULT false
  pep_details text NULL
  notes text NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- config
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  config_key text NOT NULL
  config_value text NOT NULL
  value_type text NOT NULL DEFAULT 'string'::text
  category text NOT NULL DEFAULT 'general'::text
  display_order integer NOT NULL DEFAULT 0
  is_active boolean NOT NULL DEFAULT true
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- deal_ai_summaries
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  deal_id uuid NOT NULL
  summary_text text NOT NULL
  key_strengths ARRAY NULL
  key_risks ARRAY NULL
  market_context text NULL
  input_sources jsonb NULL
  model_used text NULL
  generated_at timestamp with time zone NOT NULL DEFAULT now()
  generated_by uuid NULL
  is_current boolean NOT NULL DEFAULT true
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- deal_documents
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  deal_id uuid NOT NULL
  file_name text NOT NULL
  file_type text NOT NULL
  file_size_bytes bigint NULL
  r2_key text NOT NULL
  r2_bucket text NOT NULL
  document_type text NOT NULL DEFAULT 'general'::text
  processing_status USER-DEFINED NOT NULL DEFAULT 'pending'::deal_document_status
  extracted_data jsonb NULL
  extraction_model text NULL
  extraction_date timestamp with time zone NULL
  uploaded_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()
  status text NOT NULL DEFAULT 'pending'::text
  reviewed_by uuid NULL
  review_notes text NULL
  reviewed_at timestamp with time zone NULL
  visible_to_members boolean NOT NULL DEFAULT false

-- deal_interest
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  deal_id uuid NOT NULL
  user_id uuid NOT NULL
  entity_id uuid NULL
  status text NOT NULL DEFAULT 'indicated'::text
  amount_interest numeric NULL
  notes text NULL
  compliance_override boolean NOT NULL DEFAULT false
  override_by uuid NULL
  override_notes text NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()
  investment_stage text NULL DEFAULT 'interest_indicated'::text

-- deal_scores
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  deal_id uuid NOT NULL
  dimension text NOT NULL
  score numeric NULL
  weight numeric NOT NULL DEFAULT 0.1667
  notes text NULL
  scored_by uuid NULL
  scored_by_ai boolean NOT NULL DEFAULT false
  ai_model text NULL
  ai_confidence numeric NULL
  override_of uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- deal_votes
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  deal_id uuid NOT NULL
  user_id uuid NOT NULL
  vote smallint NOT NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- deals
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  name text NOT NULL
  slug text NULL
  description text NULL
  deal_status USER-DEFINED NOT NULL DEFAULT 'draft'::deal_status
  asset_super_class text NULL
  asset_class text NULL
  asset_sub_category text NULL
  sponsor_entity_id uuid NULL
  sponsor_name_override text NULL
  target_raise numeric NULL
  minimum_investment numeric NULL
  expected_return_pct numeric NULL
  term_months integer NULL
  deal_date date NULL
  close_date date NULL
  location text NULL
  highlights ARRAY NULL
  tags ARRAY NULL
  is_featured boolean NOT NULL DEFAULT false
  submitted_by uuid NULL
  reviewed_by uuid NULL
  published_at timestamp with time zone NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()
  deal_stage text NULL DEFAULT 'sourced'::text

-- entities
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_type USER-DEFINED NOT NULL
  display_name text NOT NULL
  legal_name text NULL
  tax_id text NULL
  date_of_birth date NULL
  country_of_formation text NULL
  notes text NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()
  sub_type text NULL
  status text NOT NULL DEFAULT 'prospect'::text
  lead_source text NULL
  relationship_manager_id uuid NULL
  tags ARRAY NULL DEFAULT '{}'::text[]
  linkedin_url text NULL
  primary_email text NULL
  primary_phone text NULL
  profile_mode text NOT NULL DEFAULT 'foundation'::text

-- entity_addresses
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  address_type USER-DEFINED NOT NULL DEFAULT 'primary_residence'::address_type
  street1 text NOT NULL
  street2 text NULL
  city text NOT NULL
  state text NULL
  postal_code text NULL
  country text NOT NULL DEFAULT 'US'::text
  is_verified boolean NOT NULL DEFAULT false
  is_primary boolean NOT NULL DEFAULT false
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_attributes
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  attribute_key text NOT NULL
  attribute_value text NULL
  value_type text NOT NULL DEFAULT 'string'::text
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_briefs
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  brief_text text NOT NULL
  key_themes ARRAY NULL
  risk_profile text NULL
  decision_style text NULL
  relationship_notes text NULL
  suitability_notes text NULL
  input_sources jsonb NULL
  model_used text NULL
  is_current boolean NOT NULL DEFAULT true
  generated_by uuid NULL
  generated_at timestamp with time zone NOT NULL DEFAULT now()
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_employment
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  employee_id uuid NOT NULL
  employer_id uuid NOT NULL
  title text NULL
  start_date date NULL
  end_date date NULL
  is_current boolean NOT NULL DEFAULT false
  notes text NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_notes
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  note_text text NOT NULL
  note_type text NOT NULL DEFAULT 'meeting'::text
  meeting_date date NULL
  extracted_fields jsonb NULL
  extraction_model text NULL
  extraction_status text NOT NULL DEFAULT 'pending'::text
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_ownership
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  parent_id uuid NOT NULL
  child_id uuid NOT NULL
  ownership_pct numeric NOT NULL
  ownership_type text NOT NULL DEFAULT 'equity'::text
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_relationships
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  from_entity_id uuid NOT NULL
  to_entity_id uuid NOT NULL
  relationship_type text NOT NULL
  notes text NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_social_profiles
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  platform USER-DEFINED NOT NULL
  url text NOT NULL
  is_primary boolean NOT NULL DEFAULT false
  linkedin_import_stub boolean NOT NULL DEFAULT false
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- entity_tax_ids
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  tax_id_type USER-DEFINED NOT NULL DEFAULT 'ssn'::tax_id_type
  tax_id_country text NOT NULL DEFAULT 'US'::text
  tax_id_encrypted text NOT NULL
  tax_id_last4 text NOT NULL
  is_primary boolean NOT NULL DEFAULT true
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- investment_profile_answers
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  question_id uuid NOT NULL
  answer_value text NULL
  answer_json jsonb NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- investment_profile_extractions
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  question_id uuid NOT NULL
  answer_id uuid NOT NULL
  extracted_fields jsonb NOT NULL
  extraction_model text NULL
  confidence numeric NULL
  advisor_reviewed boolean NOT NULL DEFAULT false
  advisor_accepted boolean NULL
  reviewed_by uuid NULL
  reviewed_at timestamp with time zone NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- investment_profile_questions
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  question_key text NOT NULL
  question_text text NOT NULL
  question_type text NOT NULL DEFAULT 'text'::text
  options jsonb NULL
  category text NOT NULL DEFAULT 'general'::text
  is_required boolean NOT NULL DEFAULT false
  display_order integer NOT NULL DEFAULT 0
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- investment_stage_history
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  member_investment_id uuid NOT NULL
  from_stage text NULL
  to_stage text NOT NULL
  changed_by uuid NULL
  notes text NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- member_investments
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  deal_id uuid NOT NULL
  user_id uuid NOT NULL
  entity_id uuid NULL
  investment_stage text NOT NULL DEFAULT 'interest_indicated'::text
  amount_committed numeric NULL
  amount_funded numeric NULL
  subdoc_sent_at timestamp with time zone NULL
  subdoc_executed_at timestamp with time zone NULL
  funded_at timestamp with time zone NULL
  stage_updated_at timestamp with time zone NOT NULL DEFAULT now()
  stage_updated_by uuid NULL
  notes text NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- member_target_allocations
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  user_id uuid NULL
  entity_id uuid NOT NULL
  taxonomy_key text NOT NULL
  taxonomy_level text NOT NULL
  target_pct numeric NOT NULL
  notes text NULL
  set_by uuid NULL
  valid_from timestamp with time zone NOT NULL DEFAULT now()
  valid_to timestamp with time zone NULL
  system_from timestamp with time zone NOT NULL DEFAULT now()
  system_to timestamp with time zone NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- notification_delivery_log
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  notification_id uuid NOT NULL
  recipient_id uuid NOT NULL
  channel text NOT NULL
  status text NOT NULL DEFAULT 'pending'::text
  attempted_at timestamp with time zone NULL
  delivered_at timestamp with time zone NULL
  failed_at timestamp with time zone NULL
  failure_reason text NULL
  external_id text NULL
  metadata jsonb NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- notification_recipients
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  notification_id uuid NOT NULL
  user_id uuid NOT NULL
  status text NOT NULL DEFAULT 'pending'::text
  read_at timestamp with time zone NULL
  acted_at timestamp with time zone NULL
  dismissed_at timestamp with time zone NULL
  action_taken text NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- notifications
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  event_type text NOT NULL
  title text NOT NULL
  body text NOT NULL
  payload jsonb NULL
  resource_type text NULL
  resource_id uuid NULL
  priority text NOT NULL DEFAULT 'normal'::text
  created_by uuid NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- organizations
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  name text NOT NULL
  slug text NOT NULL
  created_at timestamp with time zone NOT NULL DEFAULT now()

-- permissions
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  name text NOT NULL
  resource text NOT NULL
  action text NOT NULL

-- profile_conversations
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  entity_id uuid NOT NULL
  current_question_index integer NOT NULL DEFAULT 0
  status text NOT NULL DEFAULT 'in_progress'::text
  messages jsonb NOT NULL DEFAULT '[]'::jsonb
  started_at timestamp with time zone NOT NULL DEFAULT now()
  completed_at timestamp with time zone NULL
  created_by uuid NULL

-- role_permissions
  role_id uuid NOT NULL
  permission_id uuid NOT NULL

-- roles
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  name text NOT NULL
  description text NULL

-- user_notification_preferences
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  user_id uuid NOT NULL
  event_type text NOT NULL
  channel text NOT NULL
  is_enabled boolean NOT NULL DEFAULT true
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()

-- user_roles
  user_id uuid NOT NULL
  role_id uuid NOT NULL

-- users
  id uuid NOT NULL DEFAULT uuid_generate_v4()
  org_id uuid NOT NULL
  email text NOT NULL
  full_name text NULL
  avatar_url text NULL
  auth0_sub text NULL
  role text NOT NULL DEFAULT 'member'::text
  created_at timestamp with time zone NOT NULL DEFAULT now()
  updated_at timestamp with time zone NOT NULL DEFAULT now()
  assistant_panel_posture text NULL
