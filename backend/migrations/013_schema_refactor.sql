-- =============================================================================
-- Migration 013: Schema Refactor
-- =============================================================================
-- Covers:
--   1. Remove redundant teacher tables (teacher_subjects, teacher_subject_years)
--      → keep only teacher_subject_sections
--   2. Remove legacy combined-class tables (combined_subject_groups,
--      combined_subject_sections) after migrating data to combined_groups /
--      combined_group_sections
--   3. Add elective_blocks.max_parallel_sections
--   4. Add sections.max_daily_slots
--   5. Add teacher_time_windows.is_strict
--   6. Add timetable_entries unique constraints
--   7. Add timetable_runs solver-metadata columns
--   8. Remove section_breaks table (unused at solve-time)
--   9. Add subjects.credits
-- =============================================================================
-- Run this script inside a transaction so it is fully reversible on error.
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 0. Safety: ensure we have something to migrate
-- ─────────────────────────────────────────────────────────────────────────────

-- Verify teacher_subject_sections exists before we drop the others.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'teacher_subject_sections'
    ) THEN
        RAISE EXCEPTION 'teacher_subject_sections does not exist – aborting migration';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Remove redundant teacher tables
-- ─────────────────────────────────────────────────────────────────────────────
-- teacher_subjects and teacher_subject_years are superseded by
-- teacher_subject_sections which already captures all three dimensions.

-- Backfill teacher_subject_sections from teacher_subject_years (in case any
-- rows exist only in the year table and not yet in the section table).
-- We JOIN academic_years → sections to find the matching section.
INSERT INTO teacher_subject_sections (id, tenant_id, teacher_id, subject_id, section_id, is_active)
SELECT
    gen_random_uuid(),
    tsy.tenant_id,
    tsy.teacher_id,
    tsy.subject_id,
    s.id AS section_id,
    TRUE
FROM teacher_subject_years tsy
JOIN sections s ON s.academic_year_id = tsy.academic_year_id
                AND s.tenant_id       = tsy.tenant_id
WHERE NOT EXISTS (
    SELECT 1 FROM teacher_subject_sections tss
    WHERE tss.tenant_id   = tsy.tenant_id
      AND tss.teacher_id  = tsy.teacher_id
      AND tss.subject_id  = tsy.subject_id
      AND tss.section_id  = s.id
);

-- Drop legacy tables (IF EXISTS for idempotency).
DROP TABLE IF EXISTS teacher_subject_years CASCADE;
DROP TABLE IF EXISTS teacher_subjects      CASCADE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Remove legacy combined-class tables
-- ─────────────────────────────────────────────────────────────────────────────
-- Migrate combined_subject_groups → combined_groups (if table exists and has
-- rows not already present in combined_groups).

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'combined_subject_groups'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'combined_groups'
    ) THEN
        -- Insert missing combined_groups rows from legacy table.
        INSERT INTO combined_groups (id, tenant_id, academic_year_id, subject_id, teacher_id, label, created_at)
        SELECT
            csg.id,
            csg.tenant_id,
            csg.academic_year_id,
            csg.subject_id,
            NULL AS teacher_id,
            NULL AS label,
            csg.created_at
        FROM combined_subject_groups csg
        WHERE NOT EXISTS (
            SELECT 1 FROM combined_groups cg WHERE cg.id = csg.id
        );

        -- Insert matching combined_group_sections rows from combined_subject_sections.
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'combined_subject_sections'
        ) THEN
            INSERT INTO combined_group_sections (id, tenant_id, combined_group_id, subject_id, section_id, created_at)
            SELECT
                css.id,
                css.tenant_id,
                css.combined_group_id,
                (SELECT subject_id FROM combined_groups WHERE id = css.combined_group_id),
                css.section_id,
                css.created_at
            FROM combined_subject_sections css
            WHERE NOT EXISTS (
                SELECT 1
                FROM combined_group_sections cgs
                WHERE cgs.tenant_id          = css.tenant_id
                  AND cgs.combined_group_id  = css.combined_group_id
                  AND cgs.section_id         = css.section_id
            );

            DROP TABLE combined_subject_sections CASCADE;
        END IF;

        DROP TABLE combined_subject_groups CASCADE;
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Add elective_blocks.max_parallel_sections
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE elective_blocks
    ADD COLUMN IF NOT EXISTS max_parallel_sections INTEGER DEFAULT NULL;

-- Optional check: max_parallel_sections must be ≥ 1 when set.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'elective_blocks'
          AND constraint_name = 'ck_elective_blocks_max_parallel'
    ) THEN
        ALTER TABLE elective_blocks
            ADD CONSTRAINT ck_elective_blocks_max_parallel
            CHECK (max_parallel_sections IS NULL OR max_parallel_sections >= 1);
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Add sections.max_daily_slots
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE sections
    ADD COLUMN IF NOT EXISTS max_daily_slots INTEGER DEFAULT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'sections'
          AND constraint_name = 'ck_sections_max_daily_slots'
    ) THEN
        ALTER TABLE sections
            ADD CONSTRAINT ck_sections_max_daily_slots
            CHECK (max_daily_slots IS NULL OR max_daily_slots >= 0);
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Add teacher_time_windows.is_strict
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE teacher_time_windows
    ADD COLUMN IF NOT EXISTS is_strict BOOLEAN NOT NULL DEFAULT FALSE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Add timetable_entries unique constraints
-- ─────────────────────────────────────────────────────────────────────────────
-- These prevent the solver from writing duplicate room/teacher/section
-- assignments for the same slot within a run.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'timetable_entries'
          AND constraint_name = 'uq_entries_run_room_slot'
    ) THEN
        ALTER TABLE timetable_entries
            ADD CONSTRAINT uq_entries_run_room_slot
            UNIQUE (run_id, room_id, slot_id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'timetable_entries'
          AND constraint_name = 'uq_entries_run_teacher_slot'
    ) THEN
        ALTER TABLE timetable_entries
            ADD CONSTRAINT uq_entries_run_teacher_slot
            UNIQUE (run_id, teacher_id, slot_id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'timetable_entries'
          AND constraint_name = 'uq_entries_run_section_slot'
    ) THEN
        ALTER TABLE timetable_entries
            ADD CONSTRAINT uq_entries_run_section_slot
            UNIQUE (run_id, section_id, slot_id);
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Add timetable_runs solver-metadata columns
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE timetable_runs
    ADD COLUMN IF NOT EXISTS solve_time_seconds DOUBLE PRECISION DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS total_variables    INTEGER         DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS total_constraints  INTEGER         DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS objective_value    DOUBLE PRECISION DEFAULT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Remove section_breaks table
-- ─────────────────────────────────────────────────────────────────────────────
-- section_breaks was populated at solve-time but the solver already implements
-- gap minimisation via the objective function.  The table is no longer read
-- or written by any active code path (data_loader.py only queries it using
-- the run_id, which is a transient value and makes the data valueless after
-- a re-solve).
DROP TABLE IF EXISTS section_breaks CASCADE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. Add subjects.credits
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE subjects
    ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'subjects'
          AND constraint_name = 'ck_subjects_credits'
    ) THEN
        ALTER TABLE subjects
            ADD CONSTRAINT ck_subjects_credits CHECK (credits >= 0);
    END IF;
END $$;

COMMIT;
