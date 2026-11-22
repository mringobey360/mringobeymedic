# Automated daily backups on Railway

`python manage.py backup_db` creates a full backup (database dump + all
uploaded media) and now does TWO things with it:

1. Writes a local copy to `BACKUP_DIR` (skipped with `--skip-local`).
2. Pushes a copy to `backups/<filename>.zip` on whichever storage backend
   `default_storage` is configured with — the same S3-compatible bucket your
   media files use in production (see `docs/STORAGE.md`).

Step 2 is the one that actually protects you: Railway's container
filesystem is wiped on every deploy (and a Cron Job's container is
destroyed the moment the command exits), so a backup that only exists on
local disk can vanish before anyone downloads it.

## Why not just use the "Manage Backup" button in the app?

`hq` → the Shop Owner's "Download Backup" button (`trigger_backup_view`) is
for on-demand, manual backups streamed straight to a browser download. It's
useful, but it depends on a human remembering to click it. The steps below
make a backup happen automatically, every day, with no one involved.

## Prerequisite

An S3-compatible bucket must be configured (`AWS_STORAGE_BUCKET_NAME`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — see `docs/RAILWAY_PRODUCTION.md`).
Without one, the "off-box" push has nowhere durable to land — a Railway
Volume mounted at `BACKUP_DIR` is the fallback (see the note at the bottom).

## Setting up the Railway Cron Job

Railway currently has a known issue with cron schedules defined via
`railway.json` (`deploy.cronSchedule`) getting "stuck" — Railway's own
support team recommends setting the schedule from the dashboard instead.
Do it this way:

1. In your Railway project, click **"+ New"** → **"Empty Service"**.
2. Under **Settings → Source**, connect it to the **same GitHub repo** as
   your web service (same Dockerfile).
3. Under **Settings → Deploy**, set the **Custom Start Command** to:
   ```
   python manage.py backup_db --skip-local
   ```
   (`--skip-local` because this container is destroyed right after the
   command exits — writing to its local disk would be pointless.)
4. Under **Settings → Cron Schedule**, enter a crontab expression, e.g.:
   ```
   0 1 * * *
   ```
   (Runs daily at 01:00 UTC — pick a quiet hour for your pharmacy's timezone.)
5. Under **Variables**, add the SAME environment variables as your web
   service: `DATABASE_URL` (reference `${{Postgres.DATABASE_URL}}`),
   `SECRET_KEY`, `ALLOWED_HOSTS`, and the three `AWS_*` bucket variables.
   Easiest way: use Railway's "Variable Reference" to point at your web
   service's variables instead of retyping them.
6. Deploy. Railway will now run the backup command once a day and exit —
   billed only for the few seconds/minutes it takes to run, not sitting
   idle like a normal web service.

## Checking it worked

- Railway → your cron service → **Deployments** tab shows each run and its
  logs. A successful run prints `Off-box backup saved to storage: backups/...`.
- Check your S3 bucket's `backups/` folder directly to confirm files are
  landing there.

## Restoring from a backup

Download the zip from the `backups/` folder in your bucket, extract it, and
use `python manage.py loaddata database.json` against the target database,
then copy the `media/` folder back into place (or re-upload it to the bucket).
Test this at least once on a throwaway environment — a backup you've never
restored from is unverified.

## If you're not using S3 (Volume-only setup)

If you followed the Railway Volume guidance instead of S3, mount a Volume
onto THIS cron service too (Settings → Volumes), set `BACKUP_DIR` to that
mount path as an environment variable, and run the command WITHOUT
`--skip-local` (drop that flag) so the local write — the one that persists,
since it's on the Volume — is the one you keep. The "off-box" push to
`default_storage` will still attempt to run and will simply write to that
same local storage backend in that case, which is fine (backups will end up
in `<MEDIA_ROOT>/backups/` on the volume, so raise `--keep` if you want a
lot of history).


## V15 deployment note

The tenant-safe `UserDataBackup` model intentionally does not define a custom database index.
The build pipeline generates migrations during image creation; removing the optional index keeps
the initial migration portable across the supported Django/PostgreSQL deployment environments.
Tenant isolation is enforced by the owner-scoped ORM queries and download/delete authorization,
not by this index.
