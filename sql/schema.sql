-- FactTrack canonical schema for East-Texas landwork automation
-- Target: PostgreSQL 16+
-- All entities are deduplicated by natural keys where stable identifiers exist; surrogate IDs otherwise.

BEGIN;

CREATE SCHEMA IF NOT EXISTS facttrack;
SET search_path = facttrack, public;

-- ── Reference / lookups ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS county (
    fips        CHAR(5) PRIMARY KEY,
    name        TEXT NOT NULL,
    state       CHAR(2) NOT NULL DEFAULT 'TX',
    opr_platform TEXT,
    opr_base_url TEXT,
    notes       TEXT
);

INSERT INTO county (fips, name, state, opr_platform, opr_base_url) VALUES
    ('48001', 'Anderson', 'TX', 'tyler_tech_idox', NULL),
    ('48225', 'Houston',  'TX', 'tyler_tech_idox', NULL)
ON CONFLICT (fips) DO NOTHING;

CREATE TABLE IF NOT EXISTS price_deck (
    id          SERIAL PRIMARY KEY,
    label       TEXT NOT NULL,
    oil_per_bbl NUMERIC(7,2) NOT NULL,
    gas_per_mcf NUMERIC(7,2) NOT NULL,
    valid_from  DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to    DATE
);

INSERT INTO price_deck (label, oil_per_bbl, gas_per_mcf) VALUES
    ('default_2026q2', 70.00, 3.50)
ON CONFLICT DO NOTHING;

-- ── Geographic / abstract identifiers ────────────────────────────────────

CREATE TABLE IF NOT EXISTS tract (
    id              BIGSERIAL PRIMARY KEY,
    county_fips     CHAR(5) NOT NULL REFERENCES county(fips),
    abstract_no     TEXT,
    survey_name     TEXT,
    block_no        TEXT,
    section_no      TEXT,
    label           TEXT NOT NULL,
    gross_acres     NUMERIC(10,3),
    centroid_lat    DOUBLE PRECISION,
    centroid_lon    DOUBLE PRECISION,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (county_fips, abstract_no, survey_name, block_no, section_no)
);

CREATE INDEX IF NOT EXISTS idx_tract_county ON tract(county_fips);

-- ── Operators / RRC entities ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS operator (
    rrc_p5_number   INT PRIMARY KEY,
    name            TEXT NOT NULL,
    address         TEXT,
    status          TEXT,
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_operator_name ON operator(LOWER(name));

-- ── Wells (RRC) ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS well (
    api_no              CHAR(14) PRIMARY KEY,
    rrc_district        CHAR(2),
    county_fips         CHAR(5) REFERENCES county(fips),
    operator_p5         INT REFERENCES operator(rrc_p5_number),
    lease_name          TEXT,
    well_no             TEXT,
    field_name          TEXT,
    surface_lat         DOUBLE PRECISION,
    surface_lon         DOUBLE PRECISION,
    spud_date           DATE,
    completion_date     DATE,
    status              TEXT,
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_well_county ON well(county_fips);
CREATE INDEX IF NOT EXISTS idx_well_operator ON well(operator_p5);

-- Operator history (P-4 changes)
CREATE TABLE IF NOT EXISTS well_operator_history (
    id              BIGSERIAL PRIMARY KEY,
    api_no          CHAR(14) NOT NULL REFERENCES well(api_no),
    operator_p5     INT NOT NULL REFERENCES operator(rrc_p5_number),
    effective_date  DATE NOT NULL,
    end_date        DATE,
    source          TEXT NOT NULL DEFAULT 'rrc_p4',
    UNIQUE (api_no, operator_p5, effective_date)
);

-- Production reports (P-1, monthly)
CREATE TABLE IF NOT EXISTS well_production_monthly (
    api_no          CHAR(14) NOT NULL REFERENCES well(api_no),
    period          DATE NOT NULL,
    oil_bbl         NUMERIC(12,2) NOT NULL DEFAULT 0,
    gas_mcf         NUMERIC(14,2) NOT NULL DEFAULT 0,
    water_bbl       NUMERIC(12,2) NOT NULL DEFAULT 0,
    days_on         INT,
    source          TEXT NOT NULL DEFAULT 'rrc_pr',
    PRIMARY KEY (api_no, period)
);

CREATE INDEX IF NOT EXISTS idx_prod_period ON well_production_monthly(period);

-- ── Leases & title chain ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lease (
    id                  BIGSERIAL PRIMARY KEY,
    tract_id            BIGINT REFERENCES tract(id),
    county_fips         CHAR(5) NOT NULL REFERENCES county(fips),
    opr_volume          TEXT,
    opr_page            TEXT,
    opr_instrument_no   TEXT,
    recording_date      DATE,
    lessor_text         TEXT,
    lessee_text         TEXT,
    effective_date      DATE,
    primary_term_years  NUMERIC(4,2),
    primary_term_end    DATE,
    royalty_fraction    NUMERIC(9,7),
    has_pugh_clause     BOOLEAN,
    has_retained_acreage BOOLEAN,
    has_continuous_dev  BOOLEAN,
    depth_limit_ft      NUMERIC(8,1),
    raw_clause_text     TEXT,
    parsed_metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence_score    NUMERIC(3,2),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (county_fips, opr_instrument_no)
);

CREATE INDEX IF NOT EXISTS idx_lease_tract ON lease(tract_id);
CREATE INDEX IF NOT EXISTS idx_lease_county ON lease(county_fips);
CREATE INDEX IF NOT EXISTS idx_lease_primary_term_end ON lease(primary_term_end);

CREATE TABLE IF NOT EXISTS lease_party (
    id              BIGSERIAL PRIMARY KEY,
    lease_id        BIGINT NOT NULL REFERENCES lease(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('lessor', 'lessee', 'witness')),
    name            TEXT NOT NULL,
    fraction_signed NUMERIC(9,7),
    is_deceased     BOOLEAN,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_lease_party_lease ON lease_party(lease_id);

-- Assignments, releases, ratifications
CREATE TABLE IF NOT EXISTS chain_event (
    id                  BIGSERIAL PRIMARY KEY,
    county_fips         CHAR(5) NOT NULL REFERENCES county(fips),
    opr_instrument_no   TEXT,
    recording_date      DATE,
    event_type          TEXT NOT NULL CHECK (event_type IN (
                            'assignment', 'release', 'ratification', 'extension',
                            'top_lease', 'aoh', 'probate', 'rop', 'orri_creation',
                            'orri_release', 'pooled_unit'
                        )),
    grantor_text        TEXT,
    grantee_text        TEXT,
    references_lease_id BIGINT REFERENCES lease(id),
    raw_text            TEXT,
    parsed_metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence_score    NUMERIC(3,2),
    UNIQUE (county_fips, opr_instrument_no, event_type)
);

CREATE INDEX IF NOT EXISTS idx_chain_event_lease ON chain_event(references_lease_id);
CREATE INDEX IF NOT EXISTS idx_chain_event_county_date ON chain_event(county_fips, recording_date);

-- Override royalty interests
CREATE TABLE IF NOT EXISTS orri (
    id                  BIGSERIAL PRIMARY KEY,
    creating_event_id   BIGINT REFERENCES chain_event(id),
    release_event_id    BIGINT REFERENCES chain_event(id),
    beneficiary_text    TEXT NOT NULL,
    fraction            NUMERIC(9,7),
    description         TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ── Curative findings (the product output) ──────────────────────────────

CREATE TABLE IF NOT EXISTS curative_item (
    id                  BIGSERIAL PRIMARY KEY,
    project_id          TEXT NOT NULL,
    tract_id            BIGINT REFERENCES tract(id),
    lease_id            BIGINT REFERENCES lease(id),
    rule_id             TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    confidence_score    NUMERIC(3,2) NOT NULL,
    dollar_impact_low   NUMERIC(12,2),
    dollar_impact_high  NUMERIC(12,2),
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    suggested_action    TEXT NOT NULL,
    assignee_level      TEXT CHECK (assignee_level IN ('junior_landman', 'senior_landman', 'attorney_referral', 'operator_action')),
    status              TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'awaiting_doc', 'closed', 'wontfix')),
    deadline            DATE,
    related_events      JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_curative_project ON curative_item(project_id);
CREATE INDEX IF NOT EXISTS idx_curative_severity ON curative_item(severity, status);

-- ── Projects (a customer-facing grouping of tracts) ─────────────────────

CREATE TABLE IF NOT EXISTS project (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    customer_label  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes           TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS project_tract (
    project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    tract_id        BIGINT NOT NULL REFERENCES tract(id),
    PRIMARY KEY (project_id, tract_id)
);

-- ── Ingestion audit ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ingestion_run (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    rows_in         INT,
    rows_upserted   INT,
    error           TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

COMMIT;
