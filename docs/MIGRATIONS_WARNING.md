# Migration handling

This package is prepared for a **fresh local SQLite database**. All app migration folders intentionally contain only `__init__.py`, so run:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py check
```

For an **existing production database**, do NOT delete migration files. Keep the migration history from the production codebase, back up the database, then run `python manage.py makemigrations` and `python manage.py migrate` from that unchanged history. If you previously deleted production migrations, stop before migrating and reconstruct the migration history against a backup.
