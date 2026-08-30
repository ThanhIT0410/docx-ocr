-- ============================================================================
-- DocxOCR — Supabase Storage bucket + policies
-- Bucket name must match NUXT_PUBLIC_SUPABASE_STORAGE_BUCKET (default:
-- `exam-pages`). Object key convention used by the frontend:
--   {exam_id}/{page_id}.png
-- ============================================================================

insert into storage.buckets (id, name, public)
values ('exam-pages', 'exam-pages', false)
on conflict (id) do nothing;

-- anon uploads a page image at submit time, and needs to read it back
-- (signed URLs) to render the original-image pane in Pending/Processing/
-- Finished. Delete is needed so removing a page in the Pending editor can
-- also clean up its stored object (see stores/documents.ts).
create policy exam_pages_insert_anon on storage.objects
  for insert to anon
  with check (bucket_id = 'exam-pages');

create policy exam_pages_select_anon on storage.objects
  for select to anon
  using (bucket_id = 'exam-pages');

create policy exam_pages_delete_anon on storage.objects
  for delete to anon
  using (bucket_id = 'exam-pages');

-- Deleting an `exams` row cascade-deletes `pages` at the DB level, but
-- Storage objects live outside Postgres so nothing does that automatically
-- — `stores/documents.ts::deleteExam` deletes the matching Storage objects
-- itself (querying `pages.file_path` for the exam) *before* deleting the
-- exam row, using the generic `exam_pages_delete_anon` policy above.
