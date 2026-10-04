# Community Hub

A Django community hub starter with Tailwind CSS templates, built-in authentication, community posts, contact capture, and SQLite for local development. PostgreSQL is supported through `DATABASE_URL`.

## Local setup

1. Create and activate the virtual environment:

   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Copy the example environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Start Django:

   ```powershell
   python manage.py migrate
   python manage.py runserver
   ```

Open `http://127.0.0.1:8000/`.

SQLite uses `SQLITE_JOURNAL_MODE=MEMORY` in local development to avoid Windows workspace journal-file write errors. Remove that setting if you prefer SQLite's default disk journal mode on a filesystem that supports it.

## PostgreSQL locally

SQLite is the default local database so the app can run immediately. To use PostgreSQL instead, start PostgreSQL and set `.env` to:

```powershell
DATABASE_URL=postgres://community_hub:community_hub@127.0.0.1:5432/community_hub?connect_timeout=5
```

If you have Docker installed, the included Compose file can start PostgreSQL:

```powershell
docker compose up -d
```

For an existing local PostgreSQL install, create the matching database and role if they do not already exist:

```sql
CREATE USER community_hub WITH PASSWORD 'community_hub';
CREATE DATABASE community_hub OWNER community_hub;
```

## Frontend

The routed pages live in `templates/`:

- `landing.html` for the public marketing/contact page
- `signup.html` and `signin.html` for authentication
- `verify_otp.html` for email OTP verification
- `user-dashboard/user-dashboard.html` and `admin-dashboard/super_admin.html` for role-based dashboards

Tailwind CSS is loaded through CDN scripts in the templates, so you can refresh the browser after editing.

## OTP email locally

By default, OTP emails use Django's console backend. In local `DJANGO_DEBUG=True`,
the OTP also appears on the verification page so local testing does not depend on
an inbox. To send real email from `sujalnepaligaire2080@gmail.com`, set these
values in `.env`:

```powershell
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DJANGO_EMAIL_HOST=smtp.gmail.com
DJANGO_EMAIL_PORT=587
DJANGO_EMAIL_USE_TLS=True
DJANGO_EMAIL_HOST_USER=sujalnepaligaire2080@gmail.com
DJANGO_EMAIL_HOST_PASSWORD=your-gmail-app-password
DJANGO_DEFAULT_FROM_EMAIL=sujalnepaligaire2080@gmail.com
DJANGO_EMAIL_TIMEOUT=10
```

Use a Gmail app password rather than your normal Gmail password.

## Tests

Run the Django checks and test suite with:

```powershell
python manage.py check
python manage.py test
```

Tests use SQLite by default so they can run without a local PostgreSQL service. Set
`TEST_DATABASE_URL` if you want the test suite to use another database.

## eSewa ePay V2 payment collection

Paid community joins use the server-side eSewa ePay V2 flow when these values are configured:

```powershell
ESEWA_ENABLED=True
ESEWA_PRODUCT_CODE=your-production-merchant-code
ESEWA_SECRET_KEY=your-production-secret-key
ESEWA_FORM_URL=https://epay.esewa.com.np/api/epay/main/v2/form
ESEWA_STATUS_URL=https://epay.esewa.com.np/api/epay/transaction/status/
ESEWA_ALLOW_SIGNED_CALLBACK_FALLBACK=False
```

Obtain production credentials through eSewa merchant onboarding. The secret is used only by Django to generate the HMAC-SHA256 signature; it is never sent to the browser. The success callback verifies eSewa's signed response and calls status check before creating the membership. If eSewa is disabled, local development can continue using the existing manual fallback; production refuses to grant paid access without configured online payments.

For local UAT, the repository `.env` is configured with eSewa's documented test merchant values. Use the test eSewa account shown on the official [ePay V2 test credentials page](https://developer.esewa.com.np/pages/Epay-V2), then return to the local success URL. When `DJANGO_DEBUG=True`, `ESEWA_ALLOW_SIGNED_CALLBACK_FALLBACK=True` lets a valid signed `COMPLETE` callback finish local testing if eSewa's UAT status endpoint is unreachable. UAT does not move real money and must be replaced with approved production credentials before launch.

## Deployment

Set these environment variables on your host:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL`

The project includes `Procfile` for Gunicorn and uses WhiteNoise for static files.
