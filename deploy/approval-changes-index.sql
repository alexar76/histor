-- PostgreSQL: run outside a transaction with psql against the HISTOR database.
-- This optional index accelerates the global approval-change page and Atom feed.
-- Its predicate mirrors Store._select_approval_changes. SQLite and PostgreSQL
-- without the index still return the same results.
CREATE INDEX CONCURRENTLY IF NOT EXISTS extras_changes_approval_candidates ON extras_changes (seq)
WHERE from_extras IS NOT NULL AND to_extras IS NOT NULL
  AND (strpos(summary, '"readOnlyHint"') > 0
       OR strpos(summary, '"destructiveHint"') > 0
       OR strpos(summary, '"truncated"') > 0);
