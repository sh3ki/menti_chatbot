# Menti Emotional Support Chatbot

Flask-based emotional support app using MySQL for persistence, server-side Flask sessions for authentication, and OpenAI for chatbot features.

## Architecture

- MySQL domain tables and relational child tables; no JSON columns or catch-all JSON document table.
- Email/password registration/login and Google OAuth. Account roles in MySQL control user/provider/admin authorization.
- Guest sessions are temporary and do not create account rows.
- Firebase is not used by the app runtime. Its source data was left untouched during the completed read-only-source import; the credential is only for migration and must remain private.

## Run locally with Laragon

1. Start Laragon MySQL and create/select the `menti_chatbot` database.
2. Copy `.env.example` to `.env`; configure `SECRET_KEY`, `DATABASE_URL`, OpenAI settings, and Google OAuth values. Set `ADMIN_EMAIL` if you want to provision the first admin role.
3. Add `http://127.0.0.1:5000/auth/google/callback` to the Google OAuth client's authorized redirect URIs.
4. Run:

   ```powershell
   pip install -r requirements.txt
   python seed_database.py
   python app.py
   ```

`seed_database.py` creates/updates relational tables and seeds the configured administrator. It removes legacy MySQL compatibility tables only when they are empty. The one-time Firebase data import has already been completed for this local database; do not run `migrate_firebase_to_mysql.py --apply` again. Open `http://127.0.0.1:5000`.

Detailed Laragon and Hostinger KVM1 instructions are in [MYSQL_GOOGLE_SETUP.md](MYSQL_GOOGLE_SETUP.md); migration scope and relationships are in [MYSQL_GOOGLE_MIGRATION_PLAN.md](MYSQL_GOOGLE_MIGRATION_PLAN.md).

## Hostinger KVM1 deployment

Configure the production MySQL database and environment variables, register the exact HTTPS Google callback, run the seed command once, and run the Flask app behind HTTPS using Gunicorn/Nginx as described in the setup guide. Production backup/restore should be configured before accepting user records.

## Legacy files retained for your review

These Firebase-era utilities/docs are not called by the app runtime or Hostinger setup. They are left in place for you to decide whether to remove or archive:

- `firebase-credentials.json`
- `check_database.py`, `cleanup_database.py`, `add_admin.py`
- `test_setup.py`
- `QUICK_SETUP_CHECKLIST.md`, `SETUP_NEW_LAPTOP.md`
- `Procfile`, `render_build.sh` (Render-specific deployment files)
