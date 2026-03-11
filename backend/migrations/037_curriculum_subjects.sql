-- Migration 037: Introduce curriculum_subjects table
--
-- Separates subject *definition* (code, name, type, credits) from
-- curriculum *requirements* (sessions/week, max_per_day, lab_block_size_slots).
-- A compat backfill copies existing subject scheduling params so the solver
-- continues working without any data changes.
--
-- BACKWARD COMPATIBILITY
--   • subjects.sessions_per_week / max_per_day / lab_block_size_slots remain
--     (still NOT NULL) so existing queries / INSERT statements still work.
--   • The solver reads from curriculum_subjects first and falls back to the
--     subjects columns when no matching row exists.
--   • A compat view v_subject_curriculum is created for reporting.

BEGIN;

-- ──────────────────────────────────────────────────────────────────────────
-- 1. New table
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS curriculum_subjects (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID         NOT NULL REFERENCES tenants(id)        ON DELETE CASCADE,
    program_id          UUID         NOT NULL REFERENCES programs(id)       ON DELETE CASCADE,
    academic_year_id    UUID         NOT NULL REFERENCES academic_years(id) ON DELETE CASCADE,
    -- 'CORE' | 'CYBER' | 'AI_DS' | 'AI_ML' | … future tracks
    track               TEXT         NOT NULL DEFAULT 'CORE',
    subject_id          UUID         NOT NULL REFERENCES subjects(id)       ON DELETE CASCADE,
    sessions_per_week   INTEGER      NOT NULL DEFAULT 0  CHECK (sessions_per_week   >= 0),
    max_per_day         INTEGER      NOT NULL DEFAULT 1  CHECK (max_per_day         >= 0),
    lab_block_size_slots INTEGER     NOT NULL DEFAULT 1  CHECK (lab_block_size_slots >= 1),
    is_elective         BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT uq_curriculum_subjects
        UNIQUE (tenant_id, program_id, academic_year_id, track, subject_id)
);

CREATE INDEX IF NOT EXISTS ix_curriculum_subjects_tenant_id
    ON curriculum_subjects (tenant_id);
CREATE INDEX IF NOT EXISTS ix_curriculum_subjects_program_year
    ON curriculum_subjects (program_id, academic_year_id);

-- ──────────────────────────────────────────────────────────────────────────
-- 2. Backfill: one CORE row per existing subject
--    (subjects already carry program_id + academic_year_id)
-- ──────────────────────────────────────────────────────────────────────────
INSERT INTO curriculum_subjects
    (id, tenant_id, program_id, academic_year_id, track, subject_id,
     sessions_per_week, max_per_day, lab_block_size_slots, is_elective)
SELECT
    gen_random_uuid(),
    s.tenant_id,
    s.program_id,
    s.academic_year_id,
    'CORE',
    s.id,
    COALESCE(s.sessions_per_week, 0),
    COALESCE(s.max_per_day, 1),
    COALESCE(s.lab_block_size_slots, 1),
    FALSE
FROM subjects s
ON CONFLICT (tenant_id, program_id, academic_year_id, track, subject_id)
DO NOTHING;

-- ──────────────────────────────────────────────────────────────────────────
-- 3. Backfill non-CORE tracks from track_subjects
--    (sessions_override wins over the subject default)
-- ──────────────────────────────────────────────────────────────────────────
INSERT INTO curriculum_subjects
    (id, tenant_id, program_id, academic_year_id, track, subject_id,
     sessions_per_week, max_per_day, lab_block_size_slots, is_elective)
SELECT
    gen_random_uuid(),
    ts.tenant_id,
    ts.program_id,
    ts.academic_year_id,
    ts.track::TEXT,
    ts.subject_id,
    COALESCE(ts.sessions_override, s.sessions_per_week, 0),
    COALESCE(s.max_per_day, 1),
    COALESCE(s.lab_block_size_slots, 1),
    ts.is_elective
FROM track_subjects ts
JOIN subjects s ON s.id = ts.subject_id
WHERE ts.track::TEXT <> 'CORE'
ON CONFLICT (tenant_id, program_id, academic_year_id, track, subject_id)
DO NOTHING;

-- ──────────────────────────────────────────────────────────────────────────
-- 4. Compat view: joins subjects + curriculum_subjects for reporting
-- ──────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_subject_curriculum AS
SELECT
    s.id                    AS subject_id,
    s.tenant_id,
    s.program_id,
    s.academic_year_id,
    s.code                  AS subject_code,
    s.name                  AS subject_name,
    s.subject_type,
    s.credits,
    cs.id                   AS curriculum_id,
    cs.track,
    cs.sessions_per_week,
    cs.max_per_day,
    cs.lab_block_size_slots,
    cs.is_elective
FROM subjects s
LEFT JOIN curriculum_subjects cs
    ON cs.subject_id = s.id
    AND cs.tenant_id = s.tenant_id
    AND cs.program_id = s.program_id
    AND cs.academic_year_id = s.academic_year_id;

COMMIT;
