BEGIN;

-- =========================================================
-- Combined Classes v2 (Multiple groups per subject)
--
-- New model:
-- - combined_groups: a group per (academic_year, subject) with an explicit teacher_id
-- - combined_group_sections: sections that participate in a group
--
-- Key rule enforced at DB level:
-- - A section cannot be in multiple combined groups for the same subject
--   (UNIQUE(subject_id, section_id))
-- =========================================================

CREATE TABLE IF NOT EXISTS combined_groups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NULL,
  academic_year_id uuid NOT NULL REFERENCES academic_years(id) ON DELETE RESTRICT,
  subject_id uuid NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
  teacher_id uuid NULL REFERENCES teachers(id) ON DELETE RESTRICT,
  label text NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_combined_groups_tenant_id ON combined_groups(tenant_id);
CREATE INDEX IF NOT EXISTS ix_combined_groups_year ON combined_groups(academic_year_id);
CREATE INDEX IF NOT EXISTS ix_combined_groups_subject ON combined_groups(subject_id);
CREATE INDEX IF NOT EXISTS ix_combined_groups_teacher ON combined_groups(teacher_id);

CREATE TABLE IF NOT EXISTS combined_group_sections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NULL,
  combined_group_id uuid NOT NULL REFERENCES combined_groups(id) ON DELETE CASCADE,
  subject_id uuid NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
  section_id uuid NOT NULL REFERENCES sections(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_combined_group_sections_group_section UNIQUE (combined_group_id, section_id),
  CONSTRAINT uq_combined_group_sections_subject_section UNIQUE (subject_id, section_id)
);

CREATE INDEX IF NOT EXISTS ix_combined_group_sections_tenant_id ON combined_group_sections(tenant_id);
CREATE INDEX IF NOT EXISTS ix_combined_group_sections_group ON combined_group_sections(combined_group_id);
CREATE INDEX IF NOT EXISTS ix_combined_group_sections_subject ON combined_group_sections(subject_id);
CREATE INDEX IF NOT EXISTS ix_combined_group_sections_section ON combined_group_sections(section_id);

-- =========================================================
-- Backfill from legacy strict combined tables, if present.
--
-- Note: We preserve legacy group IDs by inserting with id = combined_subject_groups.id
-- so existing timetable_entries.combined_class_id keeps working.
-- =========================================================

DO $$
BEGIN
  IF to_regclass('public.combined_subject_groups') IS NOT NULL THEN
    INSERT INTO combined_groups(id, tenant_id, academic_year_id, subject_id, teacher_id, label, created_at)
    SELECT
      g.id,
      g.tenant_id,
      g.academic_year_id,
      g.subject_id,
      (
        SELECT CASE
          WHEN COUNT(DISTINCT tss.teacher_id) = 1 THEN MIN(tss.teacher_id::text)::uuid
          ELSE NULL
        END
        FROM combined_subject_sections css
        JOIN teacher_subject_sections tss
          ON tss.section_id = css.section_id
         AND tss.subject_id = g.subject_id
         AND tss.is_active IS TRUE
        WHERE css.combined_group_id = g.id
      ) AS teacher_id,
      NULL AS label,
      g.created_at
    FROM combined_subject_groups g
    ON CONFLICT (id) DO NOTHING;
  END IF;
END $$;

DO $$
BEGIN
  IF to_regclass('public.combined_subject_sections') IS NOT NULL THEN
    INSERT INTO combined_group_sections(tenant_id, combined_group_id, subject_id, section_id, created_at)
    SELECT
      COALESCE(css.tenant_id, g.tenant_id) AS tenant_id,
      css.combined_group_id,
      g.subject_id,
      css.section_id,
      css.created_at
    FROM combined_subject_sections css
    JOIN combined_subject_groups g ON g.id = css.combined_group_id
    ON CONFLICT ON CONSTRAINT uq_combined_group_sections_group_section DO NOTHING;
  END IF;
END $$;

-- If tenancy is enabled, backfill tenant_id and enforce NOT NULL.
DO $$
DECLARE
  default_tenant_id uuid;
BEGIN
  IF to_regclass('public.tenants') IS NULL THEN
    RETURN;
  END IF;

  INSERT INTO tenants (slug, name)
  SELECT 'default', 'Default College'
  WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE slug = 'default');

  SELECT id INTO default_tenant_id FROM tenants WHERE slug = 'default' LIMIT 1;
  IF default_tenant_id IS NULL THEN
    RETURN;
  END IF;

  UPDATE combined_groups SET tenant_id = default_tenant_id WHERE tenant_id IS NULL;
  UPDATE combined_group_sections SET tenant_id = default_tenant_id WHERE tenant_id IS NULL;

  ALTER TABLE combined_groups ALTER COLUMN tenant_id SET NOT NULL;
  ALTER TABLE combined_group_sections ALTER COLUMN tenant_id SET NOT NULL;
END $$;

COMMIT;
