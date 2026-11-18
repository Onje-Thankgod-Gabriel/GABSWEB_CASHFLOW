"""Passenger WSGI entrypoint for the TrueHost production account.

Passenger looks for ``passenger_wsgi.py`` in the application root and expects a
module-level ``application`` callable. Keeping this file thin means the normal
Django WSGI module remains the single application definition.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Gabsweb_Cashflow.settings")

from Gabsweb_Cashflow.wsgi import application  # noqa: E402,F401
