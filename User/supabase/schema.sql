-- ============================================================================
-- DocxOCR — Supabase schema (Postgres)
-- Source of truth: User/architecture_and_requirements.md §8-9
--
-- Apply with the Supabase SQL editor, or:
--   supabase db push   (if you keep this under supabase/migrations/)
--
-- Threat model this RLS design assumes (documented explicitly in the
-- requirements doc, §8): single-tenant desktop app, no end-user login, the
-- `anon` key is embedded in the shipped Electron app. If multi-user support
-- is added later, add a `user_id` column to `exams` and tighten every policy
-- below to `auth.uid() = user_id` (see the TODO markers).
-- ============================================================================

create extension if not exists pgcrypto; -- gen_random_uuid()

-- ----------------------------------------------------------------------------
-- Tables
-- ----------------------------------------------------------------------------
create table if not exists exams (
    id              uuid primary key default gen_random_uuid(),
    title           text not null,
    status          text not null default 'pending'
                        constraint exams_status_check check (status in ('pending','processing','finished')),
    error_message   text,
    uploaded_at     timestamptz not null default now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    updated_at      timestamptz not null default now()
);

create table if not exists pages (
    id              uuid primary key default gen_random_uuid(),
    exam_id         uuid not null references exams(id) on delete cascade,
    page_order      smallint not null,
    file_path       text not null,   -- object key in the `exam-pages` Storage bucket
    ocr_text        jsonb            -- null until the Processor finishes this page
);

create index if not exists idx_pages_exam_id on pages(exam_id);
create index if not exists idx_exams_status on exams(status);
create unique index if not exists idx_pages_exam_order on pages(exam_id, page_order);

-- Keep updated_at fresh without relying on client clocks.
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_exams_updated_at on exams;
create trigger trg_exams_updated_at
  before update on exams
  for each row execute function set_updated_at();

-- ----------------------------------------------------------------------------
-- Row Level Security
-- ----------------------------------------------------------------------------
alter table exams enable row level security;
alter table pages enable row level security;

-- exams: the frontend only ever reads and creates rows. Status/
-- finished_at transitions are written by the Processor using the
-- `service_role` key, which bypasses RLS entirely — no UPDATE policy for
-- `anon` is granted on purpose.
create policy exams_select_anon on exams
  for select to anon
  using (true); -- TODO(multi-user): using (user_id = auth.uid())

create policy exams_insert_anon on exams
  for insert to anon
  with check (true); -- TODO(multi-user): with check (user_id = auth.uid())

-- Deleting is allowed any time *except* while the Processor owns the row
-- (status = 'processing') — enforced here, not just in the UI, since this
-- is the last line of defense against a race between the frontend's own
-- pre-delete status check and the Processor flipping status concurrently.
-- `pages` cascades via FK; the matching Storage objects don't (Storage
-- isn't part of Postgres), so the frontend deletes those explicitly first
-- (see stores/documents.ts::deleteExam) — see storage.sql for that policy.
create policy exams_delete_anon on exams
  for delete to anon
  using (status <> 'processing'); -- TODO(multi-user): and user_id = auth.uid()

-- pages: readable always; writable (update/delete) by the frontend only
-- while the parent exam is still 'pending' (§6 "Pending: cho phép xóa trang
-- và sắp xếp lại thứ tự"). Insert is allowed for the initial submit.
create policy pages_select_anon on pages
  for select to anon
  using (true);

create policy pages_insert_anon on pages
  for insert to anon
  with check (
    exists (select 1 from exams e where e.id = exam_id and e.status = 'pending')
  );

create policy pages_update_anon on pages
  for update to anon
  using (
    exists (select 1 from exams e where e.id = exam_id and e.status = 'pending')
  )
  with check (
    exists (select 1 from exams e where e.id = exam_id and e.status = 'pending')
  );

create policy pages_delete_anon on pages
  for delete to anon
  using (
    exists (select 1 from exams e where e.id = exam_id and e.status = 'pending')
  );

-- ----------------------------------------------------------------------------
-- Realtime
-- ----------------------------------------------------------------------------
-- Enable via Dashboard → Database → Replication → supabase_realtime, or:
--   alter publication supabase_realtime add table exams;
--   alter publication supabase_realtime add table pages;
-- The frontend subscribes to `exams` changes to drive the sidebar counts
-- without polling (architecture_and_requirements.md §9.1).
