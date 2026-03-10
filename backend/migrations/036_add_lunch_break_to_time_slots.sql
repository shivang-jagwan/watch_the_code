-- Migration 036: Add is_lunch_break flag to time_slots
-- Marks a specific time slot as a lunch / break period.
-- The solver will refuse to schedule any class in slots where is_lunch_break = TRUE.

ALTER TABLE time_slots
    ADD COLUMN IF NOT EXISTS is_lunch_break BOOLEAN NOT NULL DEFAULT FALSE;
