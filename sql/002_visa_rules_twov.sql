-- Migration 002: Extend visa tables with TWOV rules and structured JSON
-- Run AFTER 001_init.sql
-- psql -U sentinel -d travel_sentinel -f sql/002_visa_rules_twov.sql

-- =====================================================
-- Extend visa_requirements with TWOV rules JSON
-- =====================================================

-- Add structured rules_data column (replaces flat fields over time)
ALTER TABLE visa_requirements
    ADD COLUMN IF NOT EXISTS rules_data      JSONB,
    ADD COLUMN IF NOT EXISTS twov_allowed    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS twov_required_visas TEXT[] DEFAULT '{}';

-- Index for TWOV queries (find all countries where TWOV is possible)
CREATE INDEX IF NOT EXISTS idx_visa_twov ON visa_requirements (twov_allowed)
    WHERE twov_allowed = TRUE;

-- GIN index for fast JSONB queries on rules_data
CREATE INDEX IF NOT EXISTS idx_visa_rules_gin ON visa_requirements USING GIN (rules_data);

-- =====================================================
-- Extend transit_requirements with TWOV rules
-- =====================================================

ALTER TABLE transit_requirements
    ADD COLUMN IF NOT EXISTS twov_allowed        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS twov_required_visas TEXT[] DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS twov_max_hours      SMALLINT,
    ADD COLUMN IF NOT EXISTS twov_notes          TEXT;

-- =====================================================
-- Add transit_countries to deals (array of ISO codes)
-- Previously transit info was not stored
-- =====================================================

ALTER TABLE deals
    ADD COLUMN IF NOT EXISTS transit_countries TEXT[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_deals_transit
    ON deals USING GIN (transit_countries)
    WHERE transit_countries != '{}';

-- =====================================================
-- Seed known TWOV rules for Schengen and UK
-- (Based on official immigration rules as of 2026)
-- =====================================================

-- Schengen: TWOV with valid US/UK/CA/JP visa
INSERT INTO transit_requirements
    (transit_country, passport_type, visa_required, max_transit_hrs,
     twov_allowed, twov_required_visas, twov_max_hours, twov_notes,
     verified_at, expires_at)
SELECT country_code, 'BY', TRUE, NULL,
    TRUE, ARRAY['US', 'GB', 'CA', 'JP'],
    24,
    'TWOV (Airside Transit Without Visa) - valid for airside only, no terminal change. '
    'Requires valid US, UK, Canadian, or Japanese visa in passport.',
    NOW(), NOW() + INTERVAL '30 days'
FROM (VALUES
    ('DE'), ('FR'), ('NL'), ('AT'), ('IT'), ('ES'), ('PT'), ('BE'),
    ('PL'), ('CZ'), ('HU'), ('SE'), ('DK'), ('FI'), ('NO'), ('CH')
) AS schengen(country_code)
ON CONFLICT (transit_country, passport_type) DO UPDATE SET
    twov_allowed = TRUE,
    twov_required_visas = ARRAY['US', 'GB', 'CA', 'JP'],
    twov_max_hours = 24,
    twov_notes = EXCLUDED.twov_notes,
    verified_at = NOW(),
    expires_at = NOW() + INTERVAL '30 days';

-- UK: DATV waiver with valid US/Schengen/IE/CA/AU/NZ/JP visa
INSERT INTO transit_requirements
    (transit_country, passport_type, visa_required, max_transit_hrs,
     twov_allowed, twov_required_visas, twov_max_hours, twov_notes,
     verified_at, expires_at)
VALUES (
    'GB', 'BY', TRUE, NULL,
    TRUE, ARRAY['US', 'IE', 'CA', 'AU', 'NZ', 'JP', 'SCHENGEN'],
    24,
    'UK DATV (Direct Airside Transit Visa) required. '
    'Waived if holding valid US, Irish, Canadian, Australian, NZ, or Japanese visa, '
    'or any valid Schengen visa. Airside only. Max 24h.',
    NOW(), NOW() + INTERVAL '30 days'
)
ON CONFLICT (transit_country, passport_type) DO UPDATE SET
    twov_allowed = TRUE,
    twov_required_visas = ARRAY['US', 'IE', 'CA', 'AU', 'NZ', 'JP', 'SCHENGEN'],
    twov_max_hours = 24,
    twov_notes = EXCLUDED.twov_notes,
    verified_at = NOW(),
    expires_at = NOW() + INTERVAL '30 days';
