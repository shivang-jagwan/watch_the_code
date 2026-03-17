-- 039_subject_room_exclusive_and_delete_guards.sql
-- Adds exclusive subject-room ownership and normalizes FK delete actions
-- for timetable/fixed/special entry references.

ALTER TABLE subject_allowed_rooms
  ADD COLUMN IF NOT EXISTS is_exclusive BOOLEAN NOT NULL DEFAULT FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_subject_allowed_rooms_tenant_subject_room
  ON subject_allowed_rooms (tenant_id, subject_id, room_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_subject_allowed_rooms_exclusive_room
  ON subject_allowed_rooms (tenant_id, room_id)
  WHERE is_exclusive IS TRUE;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_entries_section' AND rc.delete_rule <> 'RESTRICT'
  ) THEN
    ALTER TABLE timetable_entries DROP CONSTRAINT fk_entries_section;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_section') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_entries_subject' AND rc.delete_rule <> 'RESTRICT'
  ) THEN
    ALTER TABLE timetable_entries DROP CONSTRAINT fk_entries_subject;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_subject') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_entries_teacher' AND rc.delete_rule <> 'RESTRICT'
  ) THEN
    ALTER TABLE timetable_entries DROP CONSTRAINT fk_entries_teacher;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_teacher') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_entries_room' AND rc.delete_rule <> 'RESTRICT'
  ) THEN
    ALTER TABLE timetable_entries DROP CONSTRAINT fk_entries_room;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_room') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_room
      FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_fixed_entries_section' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE fixed_timetable_entries DROP CONSTRAINT fk_fixed_entries_section;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_section') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_fixed_entries_subject' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE fixed_timetable_entries DROP CONSTRAINT fk_fixed_entries_subject;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_subject') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_fixed_entries_teacher' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE fixed_timetable_entries DROP CONSTRAINT fk_fixed_entries_teacher;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_teacher') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_fixed_entries_room' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE fixed_timetable_entries DROP CONSTRAINT fk_fixed_entries_room;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_room') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_room
      FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_special_allotments_section' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE special_allotments DROP CONSTRAINT fk_special_allotments_section;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_section') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_special_allotments_subject' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE special_allotments DROP CONSTRAINT fk_special_allotments_subject;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_subject') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_special_allotments_teacher' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE special_allotments DROP CONSTRAINT fk_special_allotments_teacher;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_teacher') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.referential_constraints rc
    WHERE rc.constraint_name = 'fk_special_allotments_room' AND rc.delete_rule <> 'CASCADE'
  ) THEN
    ALTER TABLE special_allotments DROP CONSTRAINT fk_special_allotments_room;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_room') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_room
      FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE;
  END IF;
END $$;
