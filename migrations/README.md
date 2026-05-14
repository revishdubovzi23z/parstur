# SQL migrations

Lightweight schema-versioning for the SQLite app database. Item 5.6
in the code review tracker.

## Why a custom runner instead of Alembic?

The whole DB lives in a single file (`app_data.db`). Alembic adds a
SQLAlchemy dependency and an autogenerate workflow that's overkill
for this size of schema, and the existing `db.init_schema()` is
already idempotent CREATE-TABLE-IF-NOT-EXISTS. The runner here is
~50 lines: it reads `PRAGMA user_version`, applies any
numerically-greater `<NNNN>_<slug>.sql` file from this directory in
order, then bumps `user_version`.

## Adding a migration

1. Pick the next free number (look at the highest existing
   `NNNN_*.sql` and add one). Use a 4-digit, zero-padded prefix so
   alphabetical ordering matches numeric ordering.
2. Create `migrations/NNNN_what_it_does.sql` with the DDL/DML.
   Migration files are concatenated and executed in a single
   `executescript` call inside a transaction, so write idiomatic
   SQLite SQL with explicit `;` terminators.
3. Test on a copy of `app_data.db`:

   ```bash
   cp app_data.db /tmp/app_data.db.test
   python -c "
   from db import Database
   d = Database('/tmp/app_data.db.test')
   d.init_schema()
   "
   sqlite3 /tmp/app_data.db.test 'PRAGMA user_version;'
   ```

4. Commit BOTH the SQL file and any code that depends on the new
   schema in the same change.

## Conventions

- **Never edit a migration after it ships.** Add a new one that
  fixes/reverts the change. Existing installs may already have run
  it and won't re-run.
- **Idempotent statements only** (`CREATE TABLE IF NOT EXISTS`,
  `CREATE INDEX IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` guarded
  by a check, etc.) so the runner is safe to re-run.
- **Don't `DROP TABLE` user-data tables** without a migration that
  first copies the data into the replacement.
- **Backups**: bundle `cp app_data.db app_data.db.<timestamp>`
  before running schema-changing migrations on production.

## Baseline (user_version 0 → 1)

Any database that exists today is at `user_version = 0`. The first
migration (`0001_baseline.sql`) is a no-op marker — it just lets
the runner stamp `user_version = 1` so future migrations have a
known floor. Tables for fresh installs are still created by
`Database.init_schema()` itself (the historical path); the runner
runs *after* that and only handles deltas.
