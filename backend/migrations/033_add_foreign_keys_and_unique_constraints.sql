-- ==========================================================================
-- Migration 033: Add foreign keys and unique constraints for data integrity
-- Idempotent: safe to run multiple times.
-- ==========================================================================

-- Helper: all FK additions use DO blocks that check pg_constraint first.

-- ===================== tenant_id → tenants.id (CASCADE) ==================
-- Every tenant-scoped table needs this FK.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_academic_years_tenant') THEN
    ALTER TABLE academic_years ADD CONSTRAINT fk_academic_years_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_programs_tenant') THEN
    ALTER TABLE programs ADD CONSTRAINT fk_programs_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_rooms_tenant') THEN
    ALTER TABLE rooms ADD CONSTRAINT fk_rooms_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_teachers_tenant') THEN
    ALTER TABLE teachers ADD CONSTRAINT fk_teachers_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_subjects_tenant') THEN
    ALTER TABLE subjects ADD CONSTRAINT fk_subjects_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sections_tenant') THEN
    ALTER TABLE sections ADD CONSTRAINT fk_sections_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_time_slots_tenant') THEN
    ALTER TABLE time_slots ADD CONSTRAINT fk_time_slots_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_timetable_runs_tenant') THEN
    ALTER TABLE timetable_runs ADD CONSTRAINT fk_timetable_runs_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_timetable_entries_tenant') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_timetable_entries_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_timetable_conflicts_tenant') THEN
    ALTER TABLE timetable_conflicts ADD CONSTRAINT fk_timetable_conflicts_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_subjects_tenant') THEN
    ALTER TABLE section_subjects ADD CONSTRAINT fk_section_subjects_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_breaks_tenant') THEN
    ALTER TABLE section_breaks ADD CONSTRAINT fk_section_breaks_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_time_windows_tenant') THEN
    ALTER TABLE section_time_windows ADD CONSTRAINT fk_section_time_windows_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_elective_blocks_tenant') THEN
    ALTER TABLE section_elective_blocks ADD CONSTRAINT fk_section_elective_blocks_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_teacher_subjects_tenant') THEN
    ALTER TABLE teacher_subjects ADD CONSTRAINT fk_teacher_subjects_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_teacher_subject_sections_tenant') THEN
    ALTER TABLE teacher_subject_sections ADD CONSTRAINT fk_teacher_subject_sections_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_teacher_subject_years_tenant') THEN
    ALTER TABLE teacher_subject_years ADD CONSTRAINT fk_teacher_subject_years_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_track_subjects_tenant') THEN
    ALTER TABLE track_subjects ADD CONSTRAINT fk_track_subjects_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_combined_groups_tenant') THEN
    ALTER TABLE combined_groups ADD CONSTRAINT fk_combined_groups_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_combined_group_sections_tenant') THEN
    ALTER TABLE combined_group_sections ADD CONSTRAINT fk_combined_group_sections_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_combined_subject_groups_tenant') THEN
    ALTER TABLE combined_subject_groups ADD CONSTRAINT fk_combined_subject_groups_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_combined_subject_sections_tenant') THEN
    ALTER TABLE combined_subject_sections ADD CONSTRAINT fk_combined_subject_sections_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_elective_blocks_tenant') THEN
    ALTER TABLE elective_blocks ADD CONSTRAINT fk_elective_blocks_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_elective_block_subjects_tenant') THEN
    ALTER TABLE elective_block_subjects ADD CONSTRAINT fk_elective_block_subjects_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_timetable_entries_tenant') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_timetable_entries_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_tenant') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_tenant
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
  END IF;
END $$;


-- ===================== Entity cross-references (CASCADE) =================

-- sections → programs, academic_years
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sections_program') THEN
    ALTER TABLE sections ADD CONSTRAINT fk_sections_program
      FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sections_academic_year') THEN
    ALTER TABLE sections ADD CONSTRAINT fk_sections_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE;
  END IF;
END $$;

-- subjects → programs, academic_years
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_subjects_program') THEN
    ALTER TABLE subjects ADD CONSTRAINT fk_subjects_program
      FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_subjects_academic_year') THEN
    ALTER TABLE subjects ADD CONSTRAINT fk_subjects_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE;
  END IF;
END $$;

-- section_subjects → sections, subjects
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_subjects_section') THEN
    ALTER TABLE section_subjects ADD CONSTRAINT fk_section_subjects_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_subjects_subject') THEN
    ALTER TABLE section_subjects ADD CONSTRAINT fk_section_subjects_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

-- teacher_subjects → teachers, subjects
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_teacher_subjects_teacher') THEN
    ALTER TABLE teacher_subjects ADD CONSTRAINT fk_teacher_subjects_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_teacher_subjects_subject') THEN
    ALTER TABLE teacher_subjects ADD CONSTRAINT fk_teacher_subjects_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

-- teacher_subject_sections → teachers, subjects, sections
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tss_teacher') THEN
    ALTER TABLE teacher_subject_sections ADD CONSTRAINT fk_tss_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tss_subject') THEN
    ALTER TABLE teacher_subject_sections ADD CONSTRAINT fk_tss_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tss_section') THEN
    ALTER TABLE teacher_subject_sections ADD CONSTRAINT fk_tss_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

-- teacher_subject_years → teachers, subjects, academic_years
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tsy_teacher') THEN
    ALTER TABLE teacher_subject_years ADD CONSTRAINT fk_tsy_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tsy_subject') THEN
    ALTER TABLE teacher_subject_years ADD CONSTRAINT fk_tsy_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tsy_academic_year') THEN
    ALTER TABLE teacher_subject_years ADD CONSTRAINT fk_tsy_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE;
  END IF;
END $$;

-- track_subjects → programs, academic_years, subjects
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_track_subjects_program') THEN
    ALTER TABLE track_subjects ADD CONSTRAINT fk_track_subjects_program
      FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_track_subjects_academic_year') THEN
    ALTER TABLE track_subjects ADD CONSTRAINT fk_track_subjects_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_track_subjects_subject') THEN
    ALTER TABLE track_subjects ADD CONSTRAINT fk_track_subjects_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

-- section_time_windows → sections
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_time_windows_section') THEN
    ALTER TABLE section_time_windows ADD CONSTRAINT fk_section_time_windows_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

-- section_breaks → timetable_runs, sections, time_slots
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_breaks_run') THEN
    ALTER TABLE section_breaks ADD CONSTRAINT fk_section_breaks_run
      FOREIGN KEY (run_id) REFERENCES timetable_runs(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_breaks_section') THEN
    ALTER TABLE section_breaks ADD CONSTRAINT fk_section_breaks_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_section_breaks_slot') THEN
    ALTER TABLE section_breaks ADD CONSTRAINT fk_section_breaks_slot
      FOREIGN KEY (slot_id) REFERENCES time_slots(id) ON DELETE CASCADE;
  END IF;
END $$;

-- combined_groups → academic_years, subjects, teachers
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_combined_groups_academic_year') THEN
    ALTER TABLE combined_groups ADD CONSTRAINT fk_combined_groups_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_combined_groups_subject') THEN
    ALTER TABLE combined_groups ADD CONSTRAINT fk_combined_groups_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_combined_groups_teacher') THEN
    ALTER TABLE combined_groups ADD CONSTRAINT fk_combined_groups_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE SET NULL;
  END IF;
END $$;

-- combined_group_sections → combined_groups, subjects, sections
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_cgs_combined_group') THEN
    ALTER TABLE combined_group_sections ADD CONSTRAINT fk_cgs_combined_group
      FOREIGN KEY (combined_group_id) REFERENCES combined_groups(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_cgs_subject') THEN
    ALTER TABLE combined_group_sections ADD CONSTRAINT fk_cgs_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_cgs_section') THEN
    ALTER TABLE combined_group_sections ADD CONSTRAINT fk_cgs_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

-- combined_subject_groups → academic_years, subjects
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_csgroups_academic_year') THEN
    ALTER TABLE combined_subject_groups ADD CONSTRAINT fk_csgroups_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_csgroups_subject') THEN
    ALTER TABLE combined_subject_groups ADD CONSTRAINT fk_csgroups_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

-- combined_subject_sections → combined_subject_groups, sections
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_css_combined_group') THEN
    ALTER TABLE combined_subject_sections ADD CONSTRAINT fk_css_combined_group
      FOREIGN KEY (combined_group_id) REFERENCES combined_subject_groups(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_css_section') THEN
    ALTER TABLE combined_subject_sections ADD CONSTRAINT fk_css_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

-- elective_blocks → programs, academic_years
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_elective_blocks_program') THEN
    ALTER TABLE elective_blocks ADD CONSTRAINT fk_elective_blocks_program
      FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_elective_blocks_academic_year') THEN
    ALTER TABLE elective_blocks ADD CONSTRAINT fk_elective_blocks_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE;
  END IF;
END $$;

-- elective_block_subjects → elective_blocks, subjects, teachers
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ebs_block') THEN
    ALTER TABLE elective_block_subjects ADD CONSTRAINT fk_ebs_block
      FOREIGN KEY (block_id) REFERENCES elective_blocks(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ebs_subject') THEN
    ALTER TABLE elective_block_subjects ADD CONSTRAINT fk_ebs_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ebs_teacher') THEN
    ALTER TABLE elective_block_subjects ADD CONSTRAINT fk_ebs_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

-- section_elective_blocks → sections, elective_blocks
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_seb_section') THEN
    ALTER TABLE section_elective_blocks ADD CONSTRAINT fk_seb_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_seb_block') THEN
    ALTER TABLE section_elective_blocks ADD CONSTRAINT fk_seb_block
      FOREIGN KEY (block_id) REFERENCES elective_blocks(id) ON DELETE CASCADE;
  END IF;
END $$;

-- fixed_timetable_entries → sections, subjects, teachers, rooms, time_slots
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_section') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_subject') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_teacher') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_room') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_room
      FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_fixed_entries_slot') THEN
    ALTER TABLE fixed_timetable_entries ADD CONSTRAINT fk_fixed_entries_slot
      FOREIGN KEY (slot_id) REFERENCES time_slots(id) ON DELETE CASCADE;
  END IF;
END $$;

-- special_allotments → sections, subjects, teachers, rooms, time_slots
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_section') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_subject') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_teacher') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_room') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_room
      FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_special_allotments_slot') THEN
    ALTER TABLE special_allotments ADD CONSTRAINT fk_special_allotments_slot
      FOREIGN KEY (slot_id) REFERENCES time_slots(id) ON DELETE CASCADE;
  END IF;
END $$;

-- timetable_runs → academic_years (nullable)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_timetable_runs_academic_year') THEN
    ALTER TABLE timetable_runs ADD CONSTRAINT fk_timetable_runs_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE SET NULL;
  END IF;
END $$;

-- timetable_entries → timetable_runs (CASCADE), entities (RESTRICT)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_run') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_run
      FOREIGN KEY (run_id) REFERENCES timetable_runs(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_academic_year') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_academic_year
      FOREIGN KEY (academic_year_id) REFERENCES academic_years(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_section') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_section
      FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_subject') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_subject
      FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_teacher') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_teacher
      FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_room') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_room
      FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE RESTRICT;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_slot') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_slot
      FOREIGN KEY (slot_id) REFERENCES time_slots(id) ON DELETE RESTRICT;
  END IF;
END $$;

-- NOTE: combined_class_id is NOT a pure FK to combined_groups.  The solver also
-- stores synthetic UUIDs (room_conflict_group_id, elective_group_id) in this
-- column, so a FK constraint would reject those inserts.  No FK added here.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_entries_elective_block') THEN
    ALTER TABLE timetable_entries ADD CONSTRAINT fk_entries_elective_block
      FOREIGN KEY (elective_block_id) REFERENCES elective_blocks(id) ON DELETE SET NULL;
  END IF;
END $$;

-- timetable_conflicts → timetable_runs
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_conflicts_run') THEN
    ALTER TABLE timetable_conflicts ADD CONSTRAINT fk_conflicts_run
      FOREIGN KEY (run_id) REFERENCES timetable_runs(id) ON DELETE CASCADE;
  END IF;
END $$;


-- ===================== Unique constraints on junction tables =============

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_section_subjects_tenant_section_subject') THEN
    ALTER TABLE section_subjects ADD CONSTRAINT uq_section_subjects_tenant_section_subject
      UNIQUE (tenant_id, section_id, subject_id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_teacher_subjects_tenant_teacher_subject') THEN
    ALTER TABLE teacher_subjects ADD CONSTRAINT uq_teacher_subjects_tenant_teacher_subject
      UNIQUE (tenant_id, teacher_id, subject_id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_tss_tenant_teacher_subject_section') THEN
    ALTER TABLE teacher_subject_sections ADD CONSTRAINT uq_tss_tenant_teacher_subject_section
      UNIQUE (tenant_id, teacher_id, subject_id, section_id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_tsy_tenant_teacher_subject_year') THEN
    ALTER TABLE teacher_subject_years ADD CONSTRAINT uq_tsy_tenant_teacher_subject_year
      UNIQUE (tenant_id, teacher_id, subject_id, academic_year_id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_cgs_tenant_group_section') THEN
    ALTER TABLE combined_group_sections ADD CONSTRAINT uq_cgs_tenant_group_section
      UNIQUE (tenant_id, combined_group_id, section_id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_css_tenant_group_section') THEN
    ALTER TABLE combined_subject_sections ADD CONSTRAINT uq_css_tenant_group_section
      UNIQUE (tenant_id, combined_group_id, section_id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_ebs_tenant_block_subject') THEN
    ALTER TABLE elective_block_subjects ADD CONSTRAINT uq_ebs_tenant_block_subject
      UNIQUE (tenant_id, block_id, subject_id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_seb_tenant_section_block') THEN
    ALTER TABLE section_elective_blocks ADD CONSTRAINT uq_seb_tenant_section_block
      UNIQUE (tenant_id, section_id, block_id);
  END IF;
END $$;


-- ===================== Performance indexes ===============================
-- Common query patterns: filter by run_id, by section_id+slot_id, by teacher_id+slot_id

CREATE INDEX IF NOT EXISTS ix_timetable_entries_run_id ON timetable_entries(run_id);
CREATE INDEX IF NOT EXISTS ix_timetable_entries_section_slot ON timetable_entries(section_id, slot_id);
CREATE INDEX IF NOT EXISTS ix_timetable_entries_teacher_slot ON timetable_entries(teacher_id, slot_id);
CREATE INDEX IF NOT EXISTS ix_timetable_entries_room_slot ON timetable_entries(room_id, slot_id);
CREATE INDEX IF NOT EXISTS ix_timetable_conflicts_run_id ON timetable_conflicts(run_id);
CREATE INDEX IF NOT EXISTS ix_section_breaks_run_id ON section_breaks(run_id);
CREATE INDEX IF NOT EXISTS ix_section_subjects_section ON section_subjects(section_id);
CREATE INDEX IF NOT EXISTS ix_teacher_subject_sections_section ON teacher_subject_sections(section_id);
CREATE INDEX IF NOT EXISTS ix_elective_block_subjects_block ON elective_block_subjects(block_id);
CREATE INDEX IF NOT EXISTS ix_combined_group_sections_group ON combined_group_sections(combined_group_id);
