-- ============================================================================
-- Processor backend — reverts 0001_processor_worker_columns.sql's claim
-- columns.
--
-- `claimed_by`/`claimed_at` existed for the old DB-based claim mechanism
-- (SELECT candidate + conditional UPDATE, see 0001's comments) — the
-- worker has since been redesigned around an in-process asyncio queue
-- (app/services/queue_service.py) with a single background OCR task per
-- process (app/services/ocr_pipeline.py), so there's no longer more than
-- one consumer that could race on claiming a row; nothing reads or writes
-- these columns anymore. Confirmed zero references on the User side
-- (User/frontend, User/backend) before writing this — unlike `progress`
-- (part of the original shared schema, User/supabase/schema.sql, and
-- displayed directly in User/frontend/app/components/docs/
-- ProcessingPanel.vue), these two were Processor-only additions safe to
-- drop without touching anything User owns.
--
-- Apply with the Supabase SQL editor, same project as
-- 0001_processor_worker_columns.sql was applied to.
-- ============================================================================

drop index if exists idx_exams_status_claimed_by;

alter table exams drop column if exists claimed_by;
alter table exams drop column if exists claimed_at;
