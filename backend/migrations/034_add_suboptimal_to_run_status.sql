-- Migration 034: Add SUBOPTIMAL to run_status enum
-- The solver uses SUBOPTIMAL when a feasible solution is found but optimality
-- is not proven (e.g. due to time limit with require_optimal=true).

ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'SUBOPTIMAL' AFTER 'FEASIBLE';
