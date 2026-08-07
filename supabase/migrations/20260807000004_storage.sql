-- ─────────────────────────────────────────────────────────────────────────────
-- Receipt image storage
--
-- Receipts are proof-of-purchase documents containing personal data, so the
-- bucket is PRIVATE. Objects are addressed as {user_id}/{uuid}.jpg and the
-- policies below use the first path segment as the ownership check. The app
-- reads images through short-lived signed URLs; the extract-receipt Edge
-- Function reads them with the service role.
-- ─────────────────────────────────────────────────────────────────────────────

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'receipts',
  'receipts',
  false,
  10485760, -- 10 MB; images are downsized on-device before upload
  array['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif']
)
on conflict (id) do nothing;


create policy "Users can upload own receipts"
  on storage.objects for insert
  to authenticated
  with check (
    bucket_id = 'receipts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "Users can view own receipts"
  on storage.objects for select
  to authenticated
  using (
    bucket_id = 'receipts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "Users can update own receipts"
  on storage.objects for update
  to authenticated
  using (
    bucket_id = 'receipts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "Users can delete own receipts"
  on storage.objects for delete
  to authenticated
  using (
    bucket_id = 'receipts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
