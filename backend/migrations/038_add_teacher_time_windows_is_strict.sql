-- Migration 038: Schema drift recovery for missing columns
-- Ensures columns referenced by current ORM models exist in older databases.

ALTER TABLE IF EXISTS teacher_time_windows
    ADD COLUMN IF NOT EXISTS is_strict BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE IF EXISTS subjects
        ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 0;

ALTER TABLE IF EXISTS sections
    ADD COLUMN IF NOT EXISTS max_daily_slots INTEGER DEFAULT NULL;

ALTER TABLE IF EXISTS timetable_runs
    ADD COLUMN IF NOT EXISTS solve_time_seconds DOUBLE PRECISION DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS total_variables INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS total_constraints INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS objective_value DOUBLE PRECISION DEFAULT NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_name = 'subjects'
            AND constraint_name = 'ck_subjects_credits'
    ) THEN
        ALTER TABLE subjects
            ADD CONSTRAINT ck_subjects_credits CHECK (credits >= 0);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_name = 'sections'
            AND constraint_name = 'ck_sections_max_daily_slots'
    ) THEN
        ALTER TABLE sections
            ADD CONSTRAINT ck_sections_max_daily_slots
            CHECK (max_daily_slots IS NULL OR max_daily_slots >= 0);
    END IF;
END $$;
