"""Public PWA endpoints served from the project root.

The manifest and service worker must be available at the site root so the
manifest scope and the service-worker scope remain ``/``. Resolving them from
the existing static asset files keeps the PWA artifacts versioned with the rest
of the frontend without duplicating configuration.
"""

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse

PWA_ASSETS = Path(settings.BASE_DIR) / "static" / "assets"
MANIFEST_FILE = PWA_ASSETS / "manifest.webmanifest"
SERVICE_WORKER_FILE = PWA_ASSETS / "js" / "service-worker.js"


def _no_cache(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def pwa_manifest(request):
    if not MANIFEST_FILE.is_file():
        raise Http404("PWA manifest is not available.")
    response = FileResponse(
        MANIFEST_FILE.open("rb"),
        content_type="application/manifest+json",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return _no_cache(response)


def pwa_service_worker(request):
    if not SERVICE_WORKER_FILE.is_file():
        raise Http404("Service worker is not available.")
    response = HttpResponse(
        SERVICE_WORKER_FILE.read_text(encoding="utf-8"),
        content_type="application/javascript; charset=utf-8",
    )
    response["Service-Worker-Allowed"] = "/"
    response["X-Content-Type-Options"] = "nosniff"
    return _no_cache(response)
