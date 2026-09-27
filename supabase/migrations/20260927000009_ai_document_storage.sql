-- ─────────────────────────────────────────────────────────────────────────────
-- Storage for product documents
--
-- Manuals and warranty cards carry names, addresses and serial numbers, so the
-- bucket is PRIVATE, like `receipts`. Objects are addressed
-- {user_id}/{product_id}/{uuid}.{ext} and the policies authorise on the first
-- path segment.
--
-- The second segment is not authorised here — storage policies cannot cheaply
-- join to `products` — but it is checked when the document is registered
-- through the API, and product_documents.storage_path carries a CHECK
-- constraint pinning the path to that document's own user and product. So a
-- stray object can be uploaded under one's own prefix but can never be indexed
-- against someone else's product.
-- ─────────────────────────────────────────────────────────────────────────────

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'product-documents',
  'product-documents',
  false,
  -- 15 MB. Large enough for a scanned 20-page warranty booklet, small enough
  -- that a mobile upload finishes and the page cap in the extractor is the
  -- binding limit rather than a timeout.
  15728640,
  array[
    'application/pdf',
    'text/plain',
    'image/jpeg',
    'image/png',
    'image/webp'
  ]
)
on conflict (id) do nothing;


create policy "Users can upload own product documents"
  on storage.objects for insert
  to authenticated
  with check (
    bucket_id = 'product-documents'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "Users can view own product documents"
  on storage.objects for select
  to authenticated
  using (
    bucket_id = 'product-documents'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "Users can delete own product documents"
  on storage.objects for delete
  to authenticated
  using (
    bucket_id = 'product-documents'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
