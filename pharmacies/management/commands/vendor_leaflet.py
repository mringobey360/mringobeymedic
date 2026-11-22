"""Install Leaflet 1.9.4 locally under static/vendor/leaflet/ so branch maps work
even when public CDNs are blocked or slow.

Usage:
    python manage.py vendor_leaflet            # download + verify + install
    python manage.py vendor_leaflet --check    # report what is installed
    python manage.py vendor_leaflet --force    # re-download everything

leaflet.js and leaflet.css are verified against the official SHA-256 (SRI) hashes
published for 1.9.4; the download is aborted if a hash does not match.
"""
import base64
import hashlib
import os
import tempfile
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

VERSION = '1.9.4'
SOURCES = [
    'https://unpkg.com/leaflet@%s/dist/' % VERSION,
    'https://cdn.jsdelivr.net/npm/leaflet@%s/dist/' % VERSION,
]
# name -> expected SRI (None = no pinned hash available, size sanity check only)
FILES = {
    'leaflet.js': 'sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=',
    'leaflet.css': 'sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=',
    'images/marker-icon.png': None,
    'images/marker-icon-2x.png': None,
    'images/marker-shadow.png': None,
    'images/layers.png': None,
    'images/layers-2x.png': None,
}


def _sri(data):
    return 'sha256-' + base64.b64encode(hashlib.sha256(data).digest()).decode()


class Command(BaseCommand):
    help = 'Download and verify Leaflet %s into static/vendor/leaflet/.' % VERSION

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Only report installed files.')
        parser.add_argument('--force', action='store_true', help='Re-download existing files.')

    def handle(self, *args, **opts):
        target = os.path.join(str(settings.BASE_DIR), 'static', 'vendor', 'leaflet')
        if opts['check']:
            missing = [n for n in FILES if not os.path.exists(os.path.join(target, n))]
            if missing:
                self.stdout.write(self.style.WARNING('Missing: ' + ', '.join(missing)))
                self.stdout.write('Run: python manage.py vendor_leaflet')
            else:
                self.stdout.write(self.style.SUCCESS('Leaflet %s is installed locally.' % VERSION))
            return

        failures = []
        for name, sri in FILES.items():
            dest = os.path.join(target, name)
            if os.path.exists(dest) and not opts['force']:
                self.stdout.write('exists  %s' % name)
                continue
            data, last_err = None, None
            for base in SOURCES:
                try:
                    req = urllib.request.Request(base + name, headers={'User-Agent': 'SmartPOS-vendor-leaflet'})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        data = r.read()
                    if sri and _sri(data) != sri:
                        last_err = 'checksum mismatch from %s' % base
                        data = None
                        continue
                    if not data or len(data) < 200:
                        last_err = 'empty/short response from %s' % base
                        data = None
                        continue
                    break
                except Exception as exc:  # network, DNS, HTTP errors
                    last_err = '%s (%s)' % (exc, base)
            if data is None:
                failures.append((name, last_err))
                self.stdout.write(self.style.ERROR('failed  %s: %s' % (name, last_err)))
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest))
            with os.fdopen(fd, 'wb') as fh:
                fh.write(data)
            os.replace(tmp, dest)
            self.stdout.write(self.style.SUCCESS('saved   %s%s' % (name, ' (verified)' if sri else '')))

        if failures:
            raise CommandError('Some files could not be installed. Check the internet connection and retry.')
        self.stdout.write(self.style.SUCCESS('Leaflet %s ready. Run collectstatic on production if you use it.' % VERSION))
