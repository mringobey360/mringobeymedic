# Redis / Celery fix

Problem: kombu (via Celery 5.7) requires redis-py >= 6.1.0, but requirements pinned redis<6,
so the worker crashed with `VersionMismatch ... You have 5.3.1`.

Fix: `redis>=6.1.0,<7` in requirements.txt and requirements-postgres.txt.

After deploy, logs must show `celery@... ready` and no VersionMismatch.
