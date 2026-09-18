-- Backfill content.title from the stored /api/content payload.
--
-- Many content rows have a blank title while their raw JSON (content.content, stored as
-- text) carries the page title at the top level. This fills the gap from that JSON.
--
-- Postgres only. Run once (or the chunked version below) with:
--   set -a; . ~/gov-uk-corpus.env
--   PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f jobs/backfill-content-title.sql
--
-- Safe to re-run: it only touches rows whose title is NULL/'' and whose payload has a
-- non-empty title. Genuinely title-less pages (some redirects/errors) are left blank.

-- ---------------------------------------------------------------------------
-- One-shot backfill.
-- The left(btrim(content),1) = '{' guard keeps the ::jsonb cast off non-JSON rows.
-- On Postgres 16+ you can instead use  AND content IS JSON  and drop that guard.
-- ---------------------------------------------------------------------------
UPDATE content
SET    title = (content::jsonb) ->> 'title'
WHERE  (title IS NULL OR title = '')
  AND  content IS NOT NULL
  AND  left(btrim(content), 1) = '{'
  AND  (content::jsonb) ->> 'title' IS NOT NULL
  AND  (content::jsonb) ->> 'title' <> '';

-- ---------------------------------------------------------------------------
-- Chunked alternative (keeps locks short on the ~230k-row backlog). Run this
-- statement repeatedly until it reports "UPDATE 0"; comment out the one-shot above.
-- ---------------------------------------------------------------------------
-- UPDATE content
-- SET    title = (content::jsonb) ->> 'title'
-- WHERE  url IN (
--   SELECT url FROM content
--   WHERE (title IS NULL OR title = '') AND left(btrim(content), 1) = '{'
--   LIMIT 20000
-- ) AND (content::jsonb) ->> 'title' <> '';
