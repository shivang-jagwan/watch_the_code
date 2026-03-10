-- Migration 035: Teacher Time Windows
-- Stores optional per-day (or all-days) availability windows for teachers.
-- When a teacher has windows defined, the solver restricts that teacher's
-- assignments to slots falling within those windows.

CREATE TABLE IF NOT EXISTS teacher_time_windows (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    teacher_id  UUID         NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    -- NULL day_of_week means the window applies to every active day.
    day_of_week INTEGER      NULL,
    start_slot_index INTEGER NOT NULL,
    end_slot_index   INTEGER NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_teacher_windows_day'
  ) THEN
    ALTER TABLE teacher_time_windows
      ADD CONSTRAINT ck_teacher_windows_day
        CHECK (day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 5));
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_teacher_windows_start'
  ) THEN
    ALTER TABLE teacher_time_windows
      ADD CONSTRAINT ck_teacher_windows_start
        CHECK (start_slot_index >= 0);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_teacher_windows_end'
  ) THEN
    ALTER TABLE teacher_time_windows
      ADD CONSTRAINT ck_teacher_windows_end
        CHECK (end_slot_index >= 0);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_teacher_windows_order'
  ) THEN
    ALTER TABLE teacher_time_windows
      ADD CONSTRAINT ck_teacher_windows_order
        CHECK (end_slot_index >= start_slot_index);
  END IF;
END $$;

-- At most one window per (tenant, teacher, day_of_week).
-- day_of_week=NULL (all-days) uses a partial unique index instead of a
-- constraint because NULL != NULL in standard UNIQUE constraints.
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_teacher_windows_tenant_teacher_day'
  ) THEN
    ALTER TABLE teacher_time_windows
      ADD CONSTRAINT uq_teacher_windows_tenant_teacher_day
        UNIQUE (tenant_id, teacher_id, day_of_week);
  END IF;
END $$;

-- Partial unique index for the all-days case (day_of_week IS NULL).
CREATE UNIQUE INDEX IF NOT EXISTS uq_teacher_windows_tenant_teacher_null_day
    ON teacher_time_windows (tenant_id, teacher_id)
    WHERE day_of_week IS NULL;

CREATE INDEX IF NOT EXISTS ix_teacher_time_windows_teacher
    ON teacher_time_windows (teacher_id);

CREATE INDEX IF NOT EXISTS ix_teacher_time_windows_tenant
    ON teacher_time_windows (tenant_id);
