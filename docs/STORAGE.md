# Storage Policy

## Production

Uploaded files are stored in an S3-compatible bucket. This includes pharmacy logos, profile photos and chat attachments.

Supported providers include AWS S3, Cloudflare R2 and compatible object-storage services.

## Local Android / Termux

When no bucket is configured, the application uses `AndroidSafeFileSystemStorage`. It avoids the unsupported `flock()` behavior seen on some Android shared-storage mounts.

## Security

Bucket credentials must be stored only as environment variables. Use least-privilege access keys and configure public/private object access deliberately.
