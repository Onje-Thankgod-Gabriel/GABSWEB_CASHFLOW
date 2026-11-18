# Production deployment guide

This repository is ready for a TrueHost Passenger deployment backed by MySQL
8.4.13, Resend SMTP, Google OAuth and WhiteNoise. Nothing in this guide has
been executed against the live hosting account; every step is host-side manual
work. Keep all credentials in the hosting environment or an untracked `.env`
file and never commit them.

## 1. Runtime and dependencies

- Python: the same major/minor used locally (currently Python 3.14).
- Install dependencies from `requirements.txt` inside the virtual environment
  created by cPanel/Passenger:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`PyMySQL` is pinned as a pure-Python MySQL driver so shared hosting does not
need a compiler. `Gabsweb_Cashflow/__init__.py` prefers `mysqlclient` when the
account provides it and otherwise installs PyMySQL as the MySQLdb replacement.
`whitenoise` serves collected static files from the WSGI process.

## 2. Application root and Passenger

- Set the application root to the directory containing `manage.py` and
  `passenger_wsgi.py`.
- Passenger loads `passenger_wsgi.py`, which adds the project root to
  `sys.path`, defaults `DJANGO_SETTINGS_MODULE` to
  `Gabsweb_Cashflow.settings`, and exposes the Django WSGI `application`.
- Restart the application from cPanel after every deployment. A restart is
  required before a new service worker can be installed by users.

## 3. Environment variables

Start from `.env.example`. At minimum production requires:

```dotenv
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<strong random value>
DJANGO_ALLOWED_HOSTS=cashflow.gabsweb.com.ng
DJANGO_CSRF_TRUSTED_ORIGINS=https://cashflow.gabsweb.com.ng
DJANGO_TRUST_PROXY_SSL_HEADER=true

DJANGO_DB_ENGINE=mysql
DJANGO_DB_NAME=<cpanel database name>
DJANGO_DB_USER=<cpanel database user>
DJANGO_DB_PASSWORD=<database password>
DJANGO_DB_HOST=localhost
DJANGO_DB_PORT=3306

EMAIL_HOST=smtp.resend.com
EMAIL_PORT=587
EMAIL_HOST_USER=resend
EMAIL_HOST_PASSWORD=<resend API key>
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL="Gabsweb CashFlow <no-reply@cashflow.gabsweb.com.ng>"
PASSWORD_RESET_BASE_URL=https://cashflow.gabsweb.com.ng
EMAIL_LOGO_URL=https://cashflow.gabsweb.com.ng/static/assets/images/gabsweb-cashflow-logo.png

GOOGLE_OAUTH_CLIENT_ID=<google client id>
GOOGLE_OAUTH_CLIENT_SECRET=<google client secret>
```

Production startup fails fast if the secret key, HTTPS password-reset base URL,
email delivery or HTTPS email logo are missing or unsafe. Do not enable both
`EMAIL_USE_TLS` and `EMAIL_USE_SSL`.

## 4. MySQL 8.4.13 database

1. Create the database and user in cPanel with `utf8mb4` and grant all
   privileges on the database to that user.
2. Keep `DJANGO_DB_ENGINE=mysql`; MySQL-specific options are applied only in
   that branch and SQLite options are never sent to MySQL.
3. Run migrations from the application root:

```bash
python manage.py migrate --noinput
```

The project uses ordinary unique constraints on `(owner, client_reference)` and
`(owner, dedupe_key)`. MySQL treats NULLs as distinct in unique indexes, so
optional idempotency keys remain nullable while real references stay unique.
No partial/conditional indexes are used because MySQL does not support them.

Do not run this against a database that has not been backed up. Database
creation and migration are host-side actions and were not performed locally.

## 5. Static files, media and private uploads

```bash
python manage.py collectstatic --noinput
```

- `STATIC_ROOT` is `staticfiles/`; WhiteNoise serves it from `/static/`.
- In production, `CompressedManifestStaticFilesStorage` gives every asset a
  content hash, so a new deployment cannot be served with stale CSS or JS.
- `MEDIA_ROOT` is `media/`; uploaded public files are not served by Django in
  production. Configure the web server only if such files must be public.
- Profile images use `PRIVATE_MEDIA_ROOT` (`private_media/`) through
  `PrivateProfileImageStorage`. The storage refuses to produce public URLs and
  images are streamed only to their owner. Ensure `private_media/` is writable
  by the application user and is not mapped to a public URL.
- `media/profile-images/` requests are explicitly denied by the URL
  configuration to prevent legacy paths leaking private uploads.

## 6. Resend email and password reset

- Create a Resend API key and use it as `EMAIL_HOST_PASSWORD` (or as
  `RESEND_API_KEY`). SMTP host is `smtp.resend.com`, username `resend`.
- Verify the sending domain in Resend and publish the SPF/DKIM records in DNS.
- Password-reset links are built from `PASSWORD_RESET_BASE_URL`; production
  requires `https://`.
- `PASSWORD_RESET_TIMEOUT` defaults to 3600 seconds.
- `EMAIL_LOGO_URL` must be an absolute HTTPS URL or left empty for the
  text-only fallback.

## 7. Google OAuth

1. In Google Cloud Console create an OAuth 2.0 Client ID of type
   "Web application".
2. Add the production authorized redirect URI exactly:

```text
https://cashflow.gabsweb.com.ng/accounts/google/login/callback/
```

3. Add the matching JavaScript origin:

```text
https://cashflow.gabsweb.com.ng
```

Keep the local development redirect URI if local Google sign-in is needed:

```text
http://127.0.0.1:8000/accounts/google/login/callback/
```

Set both `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, or neither.
Google sign-in stays hidden while they are unset; email/password login keeps
working.

## 8. HTTPS, proxy and security headers

- Terminate HTTPS at the host proxy and keep
  `DJANGO_TRUST_PROXY_SSL_HEADER=true` so Django trusts
  `X-Forwarded-Proto: https`.
- Production defaults enable `SECURE_SSL_REDIRECT`, secure session/CSRF
  cookies and `SECURE_REFERRER_POLICY=same-origin`.
- Keep `DJANGO_SECURE_HSTS_SECONDS=0` until HTTPS is verified, then raise it
  (for example to `31536000`) and optionally enable subdomains/preload.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` must contain the real domain.

## 9. PWA update lifecycle

The service worker is served from `/sw.js` with `Cache-Control: no-store` and
`Service-Worker-Allowed: /`. The manifest is served from
`/manifest.webmanifest`.

- `static/assets/js/pwa.js` checks `registration.update()` immediately after
  registration, whenever the app becomes visible, and hourly while it stays
  open. A new deployment is therefore detected without reinstalling the PWA or
  clearing browsing data.
- A changed `static/assets/js/service-worker.js` (including a bumped
  `CACHE_VERSION`) creates a waiting worker. The existing update toast appears
  and the Refresh button posts `SKIP_WAITING`. Only then does the worker claim
  clients and reload the page, so unsaved work is not interrupted
  unexpectedly.
- Activation deletes older `gabsweb-` caches and keeps current and unrelated
  caches.
- HTML navigations are never cached. Only public `/static/` assets are cached,
  and responses marked `private`, `no-store` or Vary on cookie/authorization
  are excluded. Financial APIs, reports, profile routes and account pages are
  network-only.
- The branded offline page remains precached. Offline navigation shows it
  instead of any authenticated dashboard or financial data.

Bump `CACHE_VERSION` in `static/assets/js/service-worker.js` whenever static
assets change in a way that must invalidate previously cached files.

## 10. Deployment checklist

```bash
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Then:

1. Restart the Passenger application.
2. Verify `/manifest.webmanifest` and `/sw.js` return 200 with no-store headers.
3. Sign in with email/password and Google.
4. Request a password reset and confirm the Resend email and HTTPS link.
5. Upload and view a profile image; confirm the private file is not publicly
   reachable.
6. Open DevTools > Application > Service Workers and confirm the worker is
   active. Deploy a second build with a bumped `CACHE_VERSION`, keep a form
   partially filled, and confirm the update toast appears and only refreshes
   after clicking Refresh.
7. Test offline navigation and confirm the offline page appears without cached
   financial data.

A real subsequent production deployment, live MySQL migration and live OAuth /
Resend round-trip still need to be performed and verified on the hosting
account.
