# Railway Production Runbook

## Services

- Web service: this Django application
- PostgreSQL: Railway PostgreSQL service
- Object storage: S3-compatible bucket

## Required variables

```text
SECRET_KEY=<strong-random-value>
DEBUG=False
TIME_ZONE=Africa/Dar_es_Salaam
DATABASE_URL=<provided by Railway PostgreSQL>
AWS_STORAGE_BUCKET_NAME=<bucket>
AWS_ACCESS_KEY_ID=<access-key>
AWS_SECRET_ACCESS_KEY=<secret-key>
AWS_S3_ENDPOINT_URL=<provider endpoint when required>
AWS_S3_REGION_NAME=auto
AWS_QUERYSTRING_AUTH=False
```

For a custom domain, configure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` as appropriate.

## Deployment behavior

The Docker image installs dependencies, generates fresh-database migrations and collects static assets. The runtime entrypoint runs deployment checks, applies migrations and starts Gunicorn.

Do not run the demo seed command automatically during deployment.

## Storage behavior

If all three bucket credentials are present, Django uses the configured S3-compatible bucket for uploaded media. Without bucket configuration, local development uses the Android-safe filesystem backend.

## Recovery

For database recovery, prefer PostgreSQL-native backups. For application-level export, use:

```bash
python manage.py backup_db
```

The application backup command reads media through Django's active storage backend.
