-- =====================================================================
-- Vigilance Core -- standard schema, phase 1
-- =====================================================================
-- Source of truth: provided by the DBA. This file is the canonical schema
-- the application code (repositories/, services/) is written against, and the
-- one loaded into the throwaway Postgres used by the `db` integration tests
-- (locally and in CI). When the DBA ships a new schema version, replace this
-- file (or add db/schema/NNN_*.sql alongside it) so code and tests stay in sync.
--
-- Loads cleanly into an empty database, e.g.:
--   psql "$TEST_DATABASE_URL" -f db/schema/vigilance_core_standard_v2_phase1.sql
-- =====================================================================

-- =====================================================================
-- SCHEMAS
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS "citext";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE SCHEMA common;
CREATE SCHEMA raw;
CREATE SCHEMA core;
CREATE SCHEMA delivery;

-- =====================================================================
-- FUNCTION
-- =====================================================================


CREATE OR REPLACE FUNCTION gen_random_uuid_v7() 
RETURNS uuid AS $$
DECLARE
    unix_time_ms bytea;
    uuid_bytes bytea;
BEGIN
    -- 1. Get current epoch milliseconds and convert to 6-byte bytea
    unix_time_ms := substring(decode(lpad(to_hex(floor(extract(epoch from clock_timestamp()) * 1000)::bigint), 16, '0'), 'hex') from 3 for 6);
    
    -- 2. Generate 10 random bytes
    uuid_bytes := unix_time_ms || gen_random_bytes(10);
    
    -- 3. Set UUIDv7 version (0111xxx or '7') at byte 7
    uuid_bytes := set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);
    
    -- 4. Set variant (10xxxxxx) at byte 9
    uuid_bytes := set_byte(uuid_bytes, 8, (get_byte(uuid_bytes, 8) & 63) | 128);
    
    RETURN encode(uuid_bytes, 'hex')::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- =====================================================================
-- PROCEDURE
-- =====================================================================

CREATE OR REPLACE PROCEDURE delivery.generate_watchlist_daily_delta_actions(p_effective_date DATE)
LANGUAGE plpgsql
AS $$
BEGIN
    -- STEP 14 & 18 (Step 8): Standard PL/pgSQL procedures execute within a single transaction automatically.
    -- If any error occurs, PostgreSQL will automatically roll back the entire transaction block.

    -- STEP 13 & 18 (Step 7): Idempotency - Delete any existing records for the requested Effective Date.
    DELETE FROM delivery.watchlist_daily_delta 
    WHERE effective_date = p_effective_date;

    -- STEP 7, 8, 9, 10, & 12: Extract and map the delta records using CTEs.
    INSERT INTO delivery.watchlist_daily_delta (effective_date, action, vv_member_id, watchlist_member_id)
    WITH daily_records AS (
        -- STEP 2 & 7: Retrieve all candidate records created ONLY during the requested business date.
        SELECT 
            id,
            vv_member_id,
            change_type,
            version_no,
            -- STEP 8: Group by vv_member_id and determine chronological order using version_no
            ROW_NUMBER() OVER (PARTITION BY vv_member_id ORDER BY version_no ASC) AS rn_first,
            ROW_NUMBER() OVER (PARTITION BY vv_member_id ORDER BY version_no DESC) AS rn_last
        FROM core.watchlist_member
        WHERE DATE(created_at) = p_effective_date
    ),
    member_transitions AS (
        -- STEP 4: For each logical member, isolate the First Event and Last Event of the day.
        SELECT 
            first_rec.vv_member_id,
            first_rec.change_type AS first_event,
            last_rec.change_type AS last_event,
            -- STEP 9 & 11: The last_rec.id represents the latest version created during the requested date.
            last_rec.id AS latest_watchlist_member_id
        FROM (SELECT * FROM daily_records WHERE rn_first = 1) first_rec
        JOIN (SELECT * FROM daily_records WHERE rn_last = 1) last_rec
          ON first_rec.vv_member_id = last_rec.vv_member_id
    ),
    mapped_actions AS (
        -- STEP 5 & 10: Apply the exact synchronization decision matrix.
        SELECT 
            vv_member_id,
            latest_watchlist_member_id,
            CASE 
                WHEN first_event = 'NEW' AND last_event = 'NEW' THEN 'ADD'
                WHEN first_event = 'NEW' AND last_event = 'UPDATED' THEN 'ADD'
                WHEN first_event = 'NEW' AND last_event = 'DELETED' THEN 'IGNORE'
                WHEN first_event = 'UPDATED' AND last_event = 'UPDATED' THEN 'UPDATE'
                WHEN first_event = 'UPDATED' AND last_event = 'DELETED' THEN 'DELETE'
                WHEN first_event = 'DELETED' AND last_event = 'NEW' THEN 'UPDATE'
                ELSE 'IGNORE' 
            END AS action_type
        FROM member_transitions
    )
    -- STEP 6, 7 & 19: Insert only valid actions. If no records matched the date, 0 rows are inserted.
    SELECT 
        p_effective_date,
        action_type,
        vv_member_id,
        latest_watchlist_member_id
    FROM mapped_actions
    WHERE action_type != 'IGNORE';

    -- STEP 18 (Step 9): Completion is handled when the procedure reaches the end successfully.
END;
$$;

-- =====================================================================
-- LEVEL 1: Independent Tables (No Dependencies)
-- =====================================================================

CREATE TABLE raw.attachment (
  id bigserial PRIMARY KEY,
  storage_path text,
  file_name text,
  file_type text,
  mime_type text,
  file_size bigint,
  file_hash text UNIQUE NOT NULL,
  source_url text,
  downloaded_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.attachment.id IS 'Unique identifier of the extracted attachment.';
COMMENT ON COLUMN raw.attachment.storage_path IS 'Internal storage location of the attachment.';
COMMENT ON COLUMN raw.attachment.file_name IS 'Original attachment file name.';
COMMENT ON COLUMN raw.attachment.file_type IS 'File format (e.g., PDF, JPG, PNG, DOCX, XLSX, CSV, HTML).';
COMMENT ON COLUMN raw.attachment.mime_type IS 'MIME type of the attachment.';
COMMENT ON COLUMN raw.attachment.file_size IS 'File size in bytes.';
COMMENT ON COLUMN raw.attachment.file_hash IS 'Unique hash value used to detect duplicate attachments.';
COMMENT ON COLUMN raw.attachment.source_url IS 'Original URL from which the attachment was downloaded, if available.';
COMMENT ON COLUMN raw.attachment.downloaded_at IS 'Timestamp when the attachment was downloaded.';


CREATE TABLE common.lkup_entity_type (
  id bigserial PRIMARY KEY,
  name citext UNIQUE NOT NULL,
  description text
);
COMMENT ON COLUMN common.lkup_entity_type.id IS 'Unique identifier of the entity type.';
COMMENT ON COLUMN common.lkup_entity_type.name IS 'Entity type name. Case-insensitive and globally unique. Expected values include Individual, Organization, Vessel, Aircraft, High Risk Country, and Dual Use Goods.';
COMMENT ON COLUMN common.lkup_entity_type.description IS 'Detailed description of the entity type.';


-- =====================================================================
-- LEVEL 2: First-Tier Dependencies
-- =====================================================================

CREATE TABLE common.lkup_source (
  id bigserial PRIMARY KEY,
  name citext UNIQUE NOT NULL,
  country citext,
  authority text,
  base_url text,
  logo_attachment_id bigint REFERENCES raw.attachment (id) DEFERRABLE INITIALLY IMMEDIATE,
  created_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN common.lkup_source.id IS 'Unique identifier of the source.';
COMMENT ON COLUMN common.lkup_source.name IS 'Official source name. Case-insensitive and globally unique (e.g., OFAC SDN, UN Sanctions List, SEC Advisory).';
COMMENT ON COLUMN common.lkup_source.country IS 'Country or jurisdiction that owns or publishes the source.';
COMMENT ON COLUMN common.lkup_source.authority IS 'Publishing authority or organization responsible for maintaining the source.';
COMMENT ON COLUMN common.lkup_source.base_url IS 'Official website or root URL of the source.';
COMMENT ON COLUMN common.lkup_source.logo_attachment_id IS 'Reference to the source logo file stored in object storage.';
COMMENT ON COLUMN common.lkup_source.created_at IS 'Timestamp when the source record was created.';


-- =====================================================================
-- LEVEL 3: Second-Tier Dependencies
-- =====================================================================

CREATE TABLE common.lkup_source_list_type (
  id bigserial PRIMARY KEY,
  source_id bigint REFERENCES common.lkup_source (id) DEFERRABLE INITIALLY IMMEDIATE,
  name citext NOT NULL,
  code citext UNIQUE NOT NULL,
  description text
);
COMMENT ON COLUMN common.lkup_source_list_type.id IS 'Unique identifier of the source list type.';
COMMENT ON COLUMN common.lkup_source_list_type.source_id IS 'Reference to the source that owns this list type.';
COMMENT ON COLUMN common.lkup_source_list_type.name IS 'Display name of the source list type (e.g., Sanctions, PEP, Advisory, Wanted List).';
COMMENT ON COLUMN common.lkup_source_list_type.code IS 'Unique short code representing the source list type (e.g., SANCTION, PEP, FRAUD, ADVISORY).';
COMMENT ON COLUMN common.lkup_source_list_type.description IS 'Detailed description of the source list type and its intended purpose.';


-- =====================================================================
-- LEVEL 4: Third-Tier Dependencies
-- =====================================================================

CREATE TABLE raw.watchlist_file (
  id bigserial PRIMARY KEY,
  source_id bigint NOT NULL REFERENCES common.lkup_source (id) DEFERRABLE INITIALLY IMMEDIATE,
  list_type_id bigint REFERENCES common.lkup_source_list_type (id) DEFERRABLE INITIALLY IMMEDIATE,
  list_url text,
  storage_path text,
  file_name text,
  file_type text,
  mime_type text,
  file_size bigint,
  file_hash text UNIQUE NOT NULL,
  file_version text,
  downloaded_at timestamptz DEFAULT (now()),
  parsed_at timestamptz,
  status text DEFAULT 'DOWNLOADED',
  download_method text
);
COMMENT ON COLUMN raw.watchlist_file.id IS 'Unique identifier of the downloaded source file.';
COMMENT ON COLUMN raw.watchlist_file.source_id IS 'Reference to the source from which the file was downloaded.';
COMMENT ON COLUMN raw.watchlist_file.list_type_id IS 'Reference to the source list type associated with the file.';
COMMENT ON COLUMN raw.watchlist_file.list_url IS 'Exact URL from which the file was downloaded.';
COMMENT ON COLUMN raw.watchlist_file.storage_path IS 'Internal storage location of the downloaded file.';
COMMENT ON COLUMN raw.watchlist_file.file_name IS 'Original downloaded file name.';
COMMENT ON COLUMN raw.watchlist_file.file_type IS 'File format (e.g., JSON, JSONL, XML, CSV, XLSX, HTML, PDF).';
COMMENT ON COLUMN raw.watchlist_file.mime_type IS 'MIME type of the downloaded file.';
COMMENT ON COLUMN raw.watchlist_file.file_size IS 'File size in bytes.';
COMMENT ON COLUMN raw.watchlist_file.file_hash IS 'Unique hash value used to detect file-level changes and prevent duplicate downloads.';
COMMENT ON COLUMN raw.watchlist_file.file_version IS 'Source file version identifier or release version provided by the publisher.';
COMMENT ON COLUMN raw.watchlist_file.downloaded_at IS 'Timestamp when the file was downloaded.';
COMMENT ON COLUMN raw.watchlist_file.parsed_at IS 'Timestamp when parsing completed successfully.';
COMMENT ON COLUMN raw.watchlist_file.status IS 'Current processing status (e.g., DOWNLOADED, PARSED, FAILED, ARCHIVED).';


-- =====================================================================
-- LEVEL 5: Core Processing & Remaining Raw Tables
-- =====================================================================

CREATE TABLE raw.watchlist_file_log (
  id bigserial PRIMARY KEY,
  file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  event_time timestamptz DEFAULT (now()),
  step text NOT NULL,
  status text NOT NULL,
  message text,
  error_code text,
  error_details text,
  duration_ms bigint
);
COMMENT ON COLUMN raw.watchlist_file_log.id IS 'Unique processing log entry.';
COMMENT ON COLUMN raw.watchlist_file_log.file_id IS 'Reference to the processed file.';
COMMENT ON COLUMN raw.watchlist_file_log.event_time IS 'Timestamp when the processing event occurred.';
COMMENT ON COLUMN raw.watchlist_file_log.step IS 'Pipeline step (e.g., DOWNLOAD, VALIDATION, PARSING, RAW_INSERT, STAGING, CORE_INSERT).';
COMMENT ON COLUMN raw.watchlist_file_log.status IS 'Processing result (e.g., STARTED, SUCCESS, FAILED, SKIPPED).';
COMMENT ON COLUMN raw.watchlist_file_log.message IS 'Processing message or summary.';
COMMENT ON COLUMN raw.watchlist_file_log.error_code IS 'Application or parser error code, if any.';
COMMENT ON COLUMN raw.watchlist_file_log.error_details IS 'Detailed error or exception message.';
COMMENT ON COLUMN raw.watchlist_file_log.duration_ms IS 'Execution time of the processing step in milliseconds.';


CREATE TABLE raw.unparsed_watchlist_payload (
  id bigserial PRIMARY KEY,
  watchlist_file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  external_id text NOT NULL,
  raw_json jsonb NOT NULL,
  inserted_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.unparsed_watchlist_payload.id IS 'Unique identifier of the raw source record.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.watchlist_file_id IS 'Reference to the source file.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.external_id IS 'Original unique identifier assigned by the source.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.raw_json IS 'Original source record in its native format without transformation.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.inserted_at IS 'Timestamp when the raw record was stored.';


CREATE TABLE raw.list_attachment (
  id bigserial PRIMARY KEY,
  raw_file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  attachment_id bigint NOT NULL REFERENCES raw.attachment (id) DEFERRABLE INITIALLY IMMEDIATE,
  created_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.list_attachment.id IS 'Unique identifier of the list attachment.';
COMMENT ON COLUMN raw.list_attachment.raw_file_id IS 'Reference to the source file that contains this attachment.';
COMMENT ON COLUMN raw.list_attachment.attachment_id IS 'Reference to the extracted attachment.';
COMMENT ON COLUMN raw.list_attachment.created_at IS 'Timestamp when the attachment mapping was created.';


CREATE TABLE raw.member_attachment (
  id bigserial PRIMARY KEY,
  external_id text NOT NULL,
  attachment_id bigint NOT NULL REFERENCES raw.attachment (id) DEFERRABLE INITIALLY IMMEDIATE,
  attachment_type text,
  created_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.member_attachment.external_id IS 'Original source entity identifier.';
COMMENT ON COLUMN raw.member_attachment.attachment_type IS 'PHOTO, PASSPORT, DOCUMENT, ADVISORY, COURT_ORDER, WANTED_POSTER, EVIDENCE, OTHER.';
COMMENT ON COLUMN raw.member_attachment.created_at IS 'Timestamp when the attachment mapping was created.';


CREATE TABLE core.watchlist_member (
  id bigserial PRIMARY KEY,
  raw_file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  raw_member_id bigint REFERENCES raw.unparsed_watchlist_payload (id) DEFERRABLE INITIALLY IMMEDIATE,
  vv_member_id uuid NOT NULL DEFAULT (gen_random_uuid_v7()),
  source_id bigint NOT NULL REFERENCES common.lkup_source (id) DEFERRABLE INITIALLY IMMEDIATE,
  list_type_id bigint NOT NULL REFERENCES common.lkup_source_list_type (id) DEFERRABLE INITIALLY IMMEDIATE,
  external_id text,
  entity_type_id bigint NOT NULL REFERENCES common.lkup_entity_type (id) DEFERRABLE INITIALLY IMMEDIATE,
  version_no int NOT NULL DEFAULT 1,
  is_current boolean NOT NULL DEFAULT true,
  record_hash text NOT NULL,
  valid_from timestamptz NOT NULL DEFAULT (now()),
  valid_to timestamptz,
  change_type text DEFAULT 'NEW',
  full_payload jsonb NOT NULL,
  created_at timestamptz DEFAULT (now())
);
CREATE UNIQUE INDEX uq_watchlist_member_vv_version ON core.watchlist_member (vv_member_id, version_no);
CREATE UNIQUE INDEX uq_watchlist_member_source_external_version ON core.watchlist_member (list_type_id, external_id, version_no);
COMMENT ON COLUMN core.watchlist_member.id IS 'Unique internal identifier of the watchlist member version.';
COMMENT ON COLUMN core.watchlist_member.raw_file_id IS 'Reference to the source file from which this version originated.';
COMMENT ON COLUMN core.watchlist_member.raw_member_id IS 'Reference to the raw source record used to create this version.';
COMMENT ON COLUMN core.watchlist_member.vv_member_id IS 'Unique internal identifier shared by all versions of the same logical watchlist member.';
COMMENT ON COLUMN core.watchlist_member.source_id IS 'Reference to the source that published the member.';
COMMENT ON COLUMN core.watchlist_member.list_type_id IS 'Reference to the source list type.';
COMMENT ON COLUMN core.watchlist_member.external_id IS 'Stable identifier assigned by the source system.';
COMMENT ON COLUMN core.watchlist_member.entity_type_id IS 'Reference to the member entity type.';
COMMENT ON COLUMN core.watchlist_member.version_no IS 'Sequential version number. Starts at 1 and increments for each update.';
COMMENT ON COLUMN core.watchlist_member.is_current IS 'Indicates whether this is the current active version of the member.';
COMMENT ON COLUMN core.watchlist_member.record_hash IS 'Hash of the normalized record used for change detection.';
COMMENT ON COLUMN core.watchlist_member.valid_from IS 'Timestamp when this version became active.';
COMMENT ON COLUMN core.watchlist_member.valid_to IS 'Timestamp when this version was superseded or deleted. NULL indicates the current version.';
COMMENT ON COLUMN core.watchlist_member.change_type IS 'Member synchronization status (e.g., NEW, UPDATED, DELETED).';
COMMENT ON COLUMN core.watchlist_member.full_payload IS 'Complete standardized member record stored in the VV canonical schema.';
COMMENT ON COLUMN core.watchlist_member.created_at IS 'Timestamp when this version was created.';

-- =====================================================================
-- LEVEL 6: Core Member Details (Dependent on Watchlist Member)
-- =====================================================================

CREATE TABLE core.member_name (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  name_type citext,
  name citext,
  first_name citext,
  middle_name citext,
  last_name citext,
  normalized_name citext,
  phonetic_key citext,
  search_tokens citext,
  language text,
  is_primary boolean NOT NULL DEFAULT false
);
CREATE INDEX ON core.member_name (normalized_name);
COMMENT ON COLUMN core.member_name.id IS 'Unique identifier of the entity name record.';
COMMENT ON COLUMN core.member_name.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_name.name_type IS 'Type of name (e.g., Primary, AKA, Legal Name, Native Name).';
COMMENT ON COLUMN core.member_name.name IS 'Complete entity name.';
COMMENT ON COLUMN core.member_name.first_name IS 'Given name. Mainly applicable to individuals.';
COMMENT ON COLUMN core.member_name.middle_name IS 'Middle name.';
COMMENT ON COLUMN core.member_name.last_name IS 'Surname or family name.';
COMMENT ON COLUMN core.member_name.normalized_name IS 'Normalized name generated by VVIF for exact, fuzzy, phonetic, and transliteration matching.';
COMMENT ON COLUMN core.member_name.language IS 'Language of the name.';
COMMENT ON COLUMN core.member_name.is_primary IS 'Indicates whether this is the primary display name.';


CREATE TABLE core.member_alias (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  alias_type citext,
  alias citext NOT NULL,
  normalized_alias citext,
  phonetic_key citext,
  search_tokens citext
);
CREATE INDEX ON core.member_alias (normalized_alias);
COMMENT ON COLUMN core.member_alias.id IS 'Unique identifier of the alias record.';
COMMENT ON COLUMN core.member_alias.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_alias.alias_type IS 'Type of alias (e.g., AKA, Former Name, Weak Alias, Strong Alias, Native Name).';
COMMENT ON COLUMN core.member_alias.alias IS 'Alias value.';
COMMENT ON COLUMN core.member_alias.normalized_alias IS 'Normalized alias generated by VVIF for exact, fuzzy, phonetic, and transliteration matching.';


CREATE TABLE core.member_identifier (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  identifier_type citext NOT NULL,
  identifier_value citext NOT NULL,
  normalized_identifier citext,
  issuing_country citext
);
CREATE INDEX ON core.member_identifier (identifier_value);
CREATE INDEX ON core.member_identifier (normalized_identifier);
COMMENT ON COLUMN core.member_identifier.id IS 'Unique identifier of the identifier record.';
COMMENT ON COLUMN core.member_identifier.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_identifier.identifier_type IS 'Type of identifier (e.g., Passport, National ID, IMO Number, Registration Number, Tax ID).';
COMMENT ON COLUMN core.member_identifier.identifier_value IS 'Identifier value.';
COMMENT ON COLUMN core.member_identifier.normalized_identifier IS 'Normalized identifier generated by VVIF for standardized matching.';
COMMENT ON COLUMN core.member_identifier.issuing_country IS 'Country that issued the identifier.';


CREATE TABLE core.member_date (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  date_type citext NOT NULL,
  year int,
  month int,
  day int,
  is_approximate boolean NOT NULL DEFAULT false,
  note text
);
COMMENT ON COLUMN core.member_date.id IS 'Unique identifier of the entity date record.';
COMMENT ON COLUMN core.member_date.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_date.date_type IS 'Type of date (e.g., Birth, Incorporation, Registration, Manufacture, Built, Expiry).';
COMMENT ON COLUMN core.member_date.year IS 'Year component of the date.';
COMMENT ON COLUMN core.member_date.month IS 'Month component of the date.';
COMMENT ON COLUMN core.member_date.day IS 'Day component of the date.';
COMMENT ON COLUMN core.member_date.is_approximate IS 'Indicates whether the date is approximate.';
COMMENT ON COLUMN core.member_date.note IS 'Additional notes regarding the date.';


CREATE TABLE core.member_country (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  country_type citext NOT NULL,
  country_code citext,
  country_name citext NOT NULL
);
COMMENT ON COLUMN core.member_country.id IS 'Unique identifier of the country record.';
COMMENT ON COLUMN core.member_country.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_country.country_type IS 'Type of country (e.g., Nationality, Citizenship, Registration, Flag, Residence).';
COMMENT ON COLUMN core.member_country.country_code IS 'ISO 3166-1 alpha-2 or alpha-3 country code, if available.';
COMMENT ON COLUMN core.member_country.country_name IS 'Country name.';


CREATE TABLE core.member_address (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  country_name citext,
  state_province citext,
  city citext,
  postal_code citext,
  full_address citext
);
COMMENT ON COLUMN core.member_address.id IS 'Unique identifier of the address record.';
COMMENT ON COLUMN core.member_address.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_address.country_name IS 'Country name.';
COMMENT ON COLUMN core.member_address.state_province IS 'State, province, or region.';
COMMENT ON COLUMN core.member_address.city IS 'City or locality.';
COMMENT ON COLUMN core.member_address.postal_code IS 'Postal or ZIP code.';
COMMENT ON COLUMN core.member_address.full_address IS 'Complete formatted address.';


CREATE TABLE core.member_relationship (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  relationship_type citext NOT NULL,
  related_watchlist_member_id bigint REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  related_normalized_name citext
);
COMMENT ON COLUMN core.member_relationship.id IS 'Unique identifier of the relationship record.';
COMMENT ON COLUMN core.member_relationship.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_relationship.relationship_type IS 'Type of relationship (e.g., Owner, Director, Parent, Subsidiary, Associate, Relative).';
COMMENT ON COLUMN core.member_relationship.related_watchlist_member_id IS 'Reference to the current watchlist member record representing the related entity.';
COMMENT ON COLUMN core.member_relationship.related_normalized_name IS 'Related entity name as provided by the source.';


CREATE TABLE core.member_program (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  program_type citext,
  authority citext,
  program_name citext NOT NULL
);
COMMENT ON COLUMN core.member_program.id IS 'Unique identifier of the program record.';
COMMENT ON COLUMN core.member_program.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_program.program_type IS 'Type of program (e.g., Sanctions, Regulatory, Watchlist).';
COMMENT ON COLUMN core.member_program.authority IS 'Authority responsible for the program.';
COMMENT ON COLUMN core.member_program.program_name IS 'Program name.';


CREATE TABLE core.member_contact (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  contact_type citext NOT NULL,
  contact_value citext NOT NULL
);
COMMENT ON COLUMN core.member_contact.id IS 'Unique identifier of the contact record.';
COMMENT ON COLUMN core.member_contact.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_contact.contact_type IS 'Type of contact (e.g., Email, Phone, Website, Social Media, Fax).';
COMMENT ON COLUMN core.member_contact.contact_value IS 'Contact value.';


CREATE TABLE core.member_risk_category (
  id bigserial PRIMARY KEY,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  version_no int NOT NULL,
  category citext NOT NULL,
  sub_category citext,
  indicator citext,
  valid_from timestamptz NOT NULL DEFAULT (now()),
  valid_to timestamptz,
  is_current boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT (now())
);
COMMENT ON COLUMN core.member_risk_category.id IS 'Unique identifier of the risk category version.';
COMMENT ON COLUMN core.member_risk_category.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_risk_category.version_no IS 'Version number inherited from the watchlist member.';
COMMENT ON COLUMN core.member_risk_category.category IS 'Risk category (e.g., Sanction, PEP, Adverse Media, Fraud, Regulatory, Law Enforcement).';
COMMENT ON COLUMN core.member_risk_category.sub_category IS 'Risk sub-category.';
COMMENT ON COLUMN core.member_risk_category.indicator IS 'Risk indicator or classification assigned to the member.';
COMMENT ON COLUMN core.member_risk_category.valid_from IS 'Date and time when this risk category became effective.';
COMMENT ON COLUMN core.member_risk_category.valid_to IS 'Date and time when this risk category expired. NULL indicates the current version.';
COMMENT ON COLUMN core.member_risk_category.is_current IS 'TRUE indicates the current active risk category. FALSE indicates a historical version.';
COMMENT ON COLUMN core.member_risk_category.created_at IS 'Timestamp when this version was created.';


CREATE TABLE delivery.watchlist_daily_delta (
  id bigserial PRIMARY KEY,
  effective_date date NOT NULL,
  action text NOT NULL,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE
);
COMMENT ON COLUMN delivery.watchlist_daily_delta.id IS 'Unique identifier of the delta record.';
COMMENT ON COLUMN delivery.watchlist_daily_delta.effective_date IS 'Effective date of the delta record.';
COMMENT ON COLUMN delivery.watchlist_daily_delta.action IS 'Delta operation type (ADD, UPDATE, DELETE).';
COMMENT ON COLUMN delivery.watchlist_daily_delta.watchlist_member_id IS 'Reference to the current active watchlist member.';


CREATE TABLE delivery.risk_category_daily_delta (
  id bigserial PRIMARY KEY,
  effective_date date NOT NULL,
  action text NOT NULL,
  vv_member_id uuid NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE
);
COMMENT ON COLUMN delivery.risk_category_daily_delta.id IS 'Unique identifier of the delta record.';
COMMENT ON COLUMN delivery.risk_category_daily_delta.effective_date IS 'Effective date of the delta record.';
COMMENT ON COLUMN delivery.risk_category_daily_delta.action IS 'Delta operation type (ADD, UPDATE, DELETE).';
COMMENT ON COLUMN delivery.risk_category_daily_delta.watchlist_member_id IS 'Reference to the current active watchlist member.';


CREATE TABLE core.spoke_run_log (
    run_date DATE PRIMARY KEY,
    status VARCHAR(50),
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- =====================================================================
-- LEVEL 7: Dependent Logs & System Operations
-- =====================================================================
