Schema v2 replaces the old `observations` table with `trial_reports`
(source_key, process_a, process_b, low, high, revision). It is defined by the
SQLAlchemy models and created at startup via Base.metadata.create_all. There is
no in-place destructive migration: an existing `observations` table is left
untouched and simply unused. For a clean v2 database run
`docker compose down -v --remove-orphans` before starting again. Back up the
named db-data volume before changing schemas.
