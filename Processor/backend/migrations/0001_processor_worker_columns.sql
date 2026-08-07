-- ============================================================================
-- Processor backend — schema additions on top of User/supabase/schema.sql
--
-- This does NOT modify or replace anything in User/supabase/schema.sql —
-- it only ALTERs the existing shared `exams` table to add columns/values
-- the Processor worker needs. Apply with the Supabase SQL editor against
-- the SAME project as User/supabase/schema.sql, after that schema exists.
--
-- See DESIGN_REPORT.md §4.2 "Trạng thái failed" and §4.3 "Khóa tiến trình"
-- for the reasoning. Both changes need a green light from whoever owns the
-- User-side UI before this runs against a shared/prod project — see the
-- open item in DESIGN_REPORT.md §7.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Concurrency control (§3.1): claim columns instead of FOR UPDATE SKIP
--    LOCKED, since the worker talks to Postgres exclusively through
--    PostgREST (supabase-py + service_role key), which has no session/
--    transaction concept to hold a row lock across a "claim" step and the
--    later "release" step. A conditional UPDATE achieves the same
--    exclusivity: `UPDATE exams SET claimed_by = ... WHERE id = :id AND
--    claimed_by IS NULL` — Postgres serializes concurrent UPDATEs to the
--    same row, so only one racer's WHERE clause still matches after the
--    first commits.
-- ----------------------------------------------------------------------------
alter table exams add column if not exists claimed_by text;
alter table exams add column if not exists claimed_at timestamptz;

create index if not exists idx_exams_status_claimed_by
  on exams(status, claimed_by);

-- ----------------------------------------------------------------------------
-- 2. `failed` status (§3.2): distinguishes "worker gave up on this exam,
--    needs a human" from "actively being processed". Keeping status stuck
--    at 'processing' with only `error_message` set (the other option the
--    requirements doc floats) would make it indistinguishable in the User
--    UI from a healthy in-progress exam and would make it look, to the
--    worker's own claim query, like a candidate that just needs a retry —
--    it would be picked up again in an infinite fail loop unless the claim
--    query also excludes "has error_message", which is a fragile
--    signal to overload for this. A dedicated status is unambiguous for
--    both.
-- ----------------------------------------------------------------------------
alter table exams drop constraint if exists exams_status_check;
alter table exams add constraint exams_status_check
  check (status in ('pending', 'processing', 'finished', 'failed'));

-- ----------------------------------------------------------------------------
-- 3. Manual retry (services/enqueue_service.py exposes POST
--    /processor/exams/{id}/retry): moves a 'failed' exam back to
--    'processing', clearing the claim and error so the worker picks it up
--    on its next poll. No schema change needed beyond the two above — noted
--    here for completeness since it's the "retry thủ công" the requirements
--    doc mentions in §3.2 without specifying a mechanism.
-- ============================================================================
