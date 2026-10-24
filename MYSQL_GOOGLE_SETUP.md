# Local MySQL and Google sign-in setup

## Laragon

1. Start Apache and MySQL in Laragon.
2. In HeidiSQL/phpMyAdmin, create a database named `menti_chatbot` using `utf8mb4`.
3. Copy `.env.example` to `.env`; set `DATABASE_URL`, a private `SECRET_KEY`, and `ADMIN_EMAIL`.
4. Create OAuth credentials in Google Cloud Console. Add the matching local callback URI `http://127.0.0.1:5000/auth/google/callback` to the OAuth client and set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` in `.env`.
5. Install packages with `pip install -r requirements.txt`.
6. Run `python seed_database.py`, then `python app.py`.

The seed command creates/updates the relational MySQL schema and provisions the configured admin email as an account with admin access. The one-time Firebase import has already been run for this workspace; do not rerun it against the populated database. New accounts can use email/password or Google OAuth.

## Hostinger KVM1

Create a production MySQL database and user, deploy the Flask application in a Python virtual environment, install `requirements.txt`, then run `python seed_database.py` once. Configure the application service to run `gunicorn --workers 2 --worker-class gthread --threads 8 --bind 127.0.0.1:8000 app:app` behind Nginx or the selected HTTPS reverse proxy. Keep the database reachable only through the VPS/private interface where possible.

Set these production environment values in the service configuration:

```text
DATABASE_URL=mysql+pymysql://DB_USER:DB_PASSWORD@127.0.0.1:3306/DB_NAME?charset=utf8mb4
SECRET_KEY=<long-random-secret>
GOOGLE_CLIENT_ID=<oauth-client-id>
GOOGLE_CLIENT_SECRET=<oauth-client-secret>
GOOGLE_REDIRECT_URI=https://YOUR_DOMAIN/auth/google/callback
ADMIN_EMAIL=<verified-google-email>
SESSION_COOKIE_SECURE=true
```

Keep the same strong `SECRET_KEY` that was used when the Firebase hash configuration was encrypted into MySQL, at least until every imported password account has successfully logged in and upgraded its hash. Do not rotate that key during the upgrade window unless you first re-encrypt `legacy_password_hash_config` with the replacement key. The Firebase service-account JSON is not needed on Hostinger.

Register the exact HTTPS callback URI in the Google OAuth client. Restart the Flask service after changing environment values. Configure regular MySQL backups before accepting production records.

## Storage and historical records

`python seed_database.py` initializes the schema selected by `DATABASE_URL`. Confirm that HeidiSQL/phpMyAdmin is displaying that same schema (`menti_chatbot` locally). Domain records use typed tables and relational child tables. The imported schema has no JSON columns or generic `*_attributes` tables. API responses/downloadable backups may still use JSON as a transport format. Firebase remains read-only and is not used by the application runtime. The one-time importer is `migrate_firebase_to_mysql.py`; it defaults to dry-run and should not be rerun after a successful import.

## Legacy files not used by the new runtime

These Firebase-era utilities/docs are not part of the MySQL/Google OAuth runtime or deployment path. They were left in place for your review; decide separately whether to delete or archive them:

- `firebase-credentials.json` — used only by the completed one-time import; never needed by the current app runtime. It contains a private service credential and must remain untracked/secret.
- `check_database.py`, `cleanup_database.py`, `add_admin.py` — legacy Firebase-era maintenance scripts; do not run for MySQL setup.
- `test_setup.py` — old setup verifier that still checks Firebase.
- `QUICK_SETUP_CHECKLIST.md`, `SETUP_NEW_LAPTOP.md` — older setup documentation; use this guide instead.
- `Procfile`, `render_build.sh` — Render deployment artifacts; not used by the Hostinger KVM1 run command.
