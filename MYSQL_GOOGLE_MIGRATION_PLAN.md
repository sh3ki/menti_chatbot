# Firebase to MySQL / Google OAuth: Full Relational Cutover

## Confirmed requirements

- Develop against Laragon MySQL; deploy on Hostinger KVM1.
- Human accounts may register/sign in with email + password or Google OAuth. MySQL roles authorize users, providers, and administrators independently of sign-in method.
- Guest mode remains a temporary Flask session; it does not create an account.
- Read Firebase data for the owner-approved one-time import; never mutate or delete the Firebase source.
- One-time production cutover after schema and deployment configuration are verified.
- Keep the migration-time `SECRET_KEY` stable while Firebase password hashes are upgraded at first login; the imported hash parameters are encrypted with it.

## Why the initial MySQL view had only two tables

The first implementation pointed at `127.0.0.1:3306/menti_chatbot` and created only `accounts` plus a transitional `app_documents` JSON table. That compatibility design did not meet the full relational requirement. The current implementation replaces it with domain-specific tables; run `python seed_database.py` after deploying schema changes and inspect the `menti_chatbot` database in HeidiSQL/phpMyAdmin.

## Target relational schema

Persisted application domain data uses typed columns, foreign keys, and relational child tables. There are no JSON columns or catch-all JSON/attribute payload tables. Flexible values are mapped to domain-specific child tables.

- Identity/access: `accounts`, with unique email/Google subject, password hash, verified flag, active flag, and role; `providers`, linked to accounts and holding provider application/licensing/status fields.
- Chat: `conversations` (owner, guest/session marker, title, archive state, timestamps) and `messages` (conversation FK, sender, body, emotion, ordering, timestamp).
- User wellbeing: `user_profiles` and `user_profile_preferences`; `user_assessments` plus `assessment_answers`, `assessment_scores`, and `assessment_recommendations`; `wellness_plans` plus `wellness_goals`, `wellness_reflections`, `wellness_coping_exercises`, `wellness_coping_steps`, and emotion counts; `journal_entries`.
- Provider scheduling: `provider_availability`, `provider_availability_durations`, `provider_weekly_slots`, `provider_availability_intervals`, `provider_events`, and `appointments` with account/provider foreign keys and status/timestamps.
- Operations: `notifications`, `emotion_logs`, `crisis_alerts`, `video_signals` plus `video_session_descriptions` and `video_candidates`, `call_history`, the `app_content` resource configuration rows, and `backup_history`.

Relationship integrity, indexes for owner/status/time queries, and delete behavior are explicit for core entities. Unstructured content that is inherently user-authored (message/journal text, notes, descriptions, SDP/candidate strings) remains in text columns, not serialized objects. List/object data currently sent by the app is mapped to child rows or explicit columns; compatibility-only flexible fields use collection-specific `*_attributes` relations with owner foreign keys.

## Implemented migration and remaining deployment sequence

1. Inventory active routes and payloads; unrelated Firebase-only maintenance scripts remain untouched and are not invoked by the runtime.
2. Define per-domain MySQL tables and typed child tables; conversations/messages and user assessments also have dedicated child tables and foreign keys.
3. Keep existing route response shapes via a relational adapter; scalar filters, ordering, and limits are issued as SQL queries.
4. Keep password/Google OAuth identities and roles in `accounts`; guests remain non-permanent Flask sessions and never create account rows.
5. `seed_database.py` idempotently creates schema/admin account. It drops the obsolete `app_documents`/`record_attributes` tables only after confirming they are empty; if either has rows, it stops without deleting them.
6. Completed a strict dry-run and a one-transaction import. Verification matched all 1,893 Firestore documents across 17 relational domain tables, plus 30 human Auth accounts. The 112 anonymous-like Auth identities were not created as persistent accounts; their Firestore history remains imported. Firebase was not modified.
7. Laragon smoke tests passed for email/password signup/login, temporary guest sessions, account non-creation for guests, data row parity, and absence of JSON/`*_attributes` tables. Hostinger OAuth credentials/domain, live callback/session-cookie checks, and production backup/restore remain pre-deployment tasks.

## Safe cutover note

Firebase remains unmodified. The local schema seed removed the legacy `*_attributes` tables only after proving they were empty; no MySQL rows were lost. The imported Firestore collections retain source IDs and are split into typed columns and domain child tables.

## Runtime configuration

Laragon uses `DATABASE_URL` for the `menti_chatbot` schema. Hostinger uses a production MySQL URL, HTTPS-only session cookies, a strong `SECRET_KEY`, and Google OAuth client/secret/redirect URI. Set `ADMIN_EMAIL` to provision a role-bearing administrator account; the administrator still authenticates through an enabled sign-in method.
