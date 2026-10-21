BEGIN;

CREATE TABLE IF NOT EXISTS core.media_risk_category (
    id bigserial PRIMARY KEY,
    vv_media_id bigint NOT NULL,
    media_record_id bigint NOT NULL REFERENCES core.media_record(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    version_no int NOT NULL,
    risk_details jsonb NOT NULL DEFAULT '{}'::jsonb,
    risk_details_hash text NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT NOW(),
    valid_to timestamptz,
    is_current boolean NOT NULL DEFAULT TRUE,
    created_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS delivery.media_daily_delta_actions (
    id bigserial PRIMARY KEY,
    effective_date date NOT NULL,
    action text NOT NULL CHECK (action IN ('ADD', 'UPDATE', 'DELETE')),
    vv_media_id bigint NOT NULL,
    media_record_id bigint NOT NULL REFERENCES core.media_record(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_media_risk_category_current
    ON core.media_risk_category(vv_media_id) WHERE is_current = TRUE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_member_risk_category_current
    ON core.member_risk_category(vv_member_id) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_delta_date_id
    ON delivery.media_daily_delta_actions(effective_date, id);
CREATE INDEX IF NOT EXISTS idx_watchlist_delta_date_id
    ON delivery.watchlist_daily_delta_actions(effective_date, id);

CREATE TABLE IF NOT EXISTS core.risk_label_review (
    id bigserial PRIMARY KEY,
    media_risk_category_id bigint REFERENCES core.media_risk_category(id),
    member_risk_category_id bigint REFERENCES core.member_risk_category(id),
    risk_category_id text NOT NULL,
    verdict text NOT NULL CHECK (verdict IN ('present', 'absent', 'uncertain')),
    reviewed_by text NOT NULL CHECK (BTRIM(reviewed_by) <> ''),
    reviewed_at timestamptz NOT NULL DEFAULT NOW(),
    taxonomy_version text NOT NULL,
    evidence text,
    comment text,
    CHECK (num_nonnulls(media_risk_category_id, member_risk_category_id) = 1)
);

CREATE INDEX IF NOT EXISTS idx_risk_label_review_media
    ON core.risk_label_review(media_risk_category_id, risk_category_id, id);
CREATE INDEX IF NOT EXISTS idx_risk_label_review_member
    ON core.risk_label_review(member_risk_category_id, risk_category_id, id);
CREATE INDEX IF NOT EXISTS idx_risk_label_review_filter
    ON core.risk_label_review(risk_category_id, verdict, id);

COMMIT;
