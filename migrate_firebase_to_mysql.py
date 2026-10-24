"""Owner-directed, read-only-source Firebase -> relational MySQL cutover.

Run once with --dry-run first, then --apply. The Firebase project is never
written to. Anonymous Auth identities are deliberately not imported as
accounts; their historical Firestore documents are retained as source data.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import quote
from collections import Counter

import firebase_admin
from firebase_admin import auth, credentials
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account


ROOT_COLLECTIONS = (
    "admins", "appointments", "backup_history", "call_history", "conversations",
    "crisis_alerts", "emotion_logs", "journal_entries", "notifications",
    "provider_availability", "providers", "user_assessments", "user_profiles",
    "users", "video_signals", "wellness_plans",
)
ORDER = (
    "providers", "provider_availability", "conversations", "messages",
    "user_assessments", "appointments", "notifications", "video_signals",
    "call_history", "emotion_logs", "crisis_alerts", "user_profiles",
    "wellness_plans", "journal_entries", "app_content", "backup_history",
    "admins", "users",
)


def connect_firebase(key_path):
    if not os.path.isfile(key_path):
        raise RuntimeError(f"Firebase service-account key not found: {key_path}")
    cred = credentials.Certificate(key_path)
    app = firebase_admin.initialize_app(cred, name="read_only_migration")
    rest_credentials = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/datastore"])
    return app, AuthorizedSession(rest_credentials), cred


def decode_firestore_value(value):
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    for key in ("stringValue", "timestampValue", "referenceValue", "bytesValue", "geoPointValue"):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        return [decode_firestore_value(item) for item in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return {key: decode_firestore_value(item)
                for key, item in value["mapValue"].get("fields", {}).items()}
    return None


def list_firestore_documents(session, project_id, collection_path):
    base = ("https://firestore.googleapis.com/v1/projects/" + quote(project_id, safe="") +
            "/databases/(default)/documents/" + quote(collection_path, safe="/"))
    token = None
    while True:
        params = {"pageSize": 1000}
        if token:
            params["pageToken"] = token
        response = session.get(base, params=params, timeout=40)
        if response.status_code != 200:
            raise RuntimeError(f"Firestore read failed for {collection_path} (HTTP {response.status_code})")
        data = response.json()
        for document in data.get("documents", []):
            doc_path = document["name"].rsplit("/", 1)[-1]
            payload = {key: decode_firestore_value(value)
                       for key, value in document.get("fields", {}).items()}
            yield doc_path, payload
        token = data.get("nextPageToken")
        if not token:
            break


def collect_firestore(session, project_id):
    records = Counter()
    docs_by_collection = {}
    for collection_name in ROOT_COLLECTIONS:
        print(f"Reading Firestore collection: {collection_name}", flush=True)
        for document_id, payload in list_firestore_documents(session, project_id, collection_name):
            docs_by_collection.setdefault(collection_name, []).append((document_id, payload))
            records[collection_name] += 1
            if collection_name == "conversations":
                if len(docs_by_collection[collection_name]) % 25 == 0:
                    print(f"Read messages from {len(docs_by_collection[collection_name])} conversations...", flush=True)
                for message_id, message_payload in list_firestore_documents(
                        session, project_id, f"conversations/{document_id}/messages"):
                    message_payload["conversationId"] = document_id
                    docs_by_collection.setdefault("messages", []).append((message_id, message_payload))
                    records["messages"] += 1
    return docs_by_collection, records


def auth_inventory(firebase_app):
    users = []
    page = auth.list_users(app=firebase_app)
    while page:
        users.extend(page.users)
        page = page.get_next_page()
    return users


def user_data(user):
    raw = getattr(user, "_data", {}) or {}
    providers = getattr(user, "provider_data", ()) or ()
    google_sub = next((item.uid for item in providers if item.provider_id == "google.com"), None)
    email = (getattr(user, "email", None) or "").strip().lower()
    is_human = bool(email or providers)
    return {
        "uid": user.uid, "email": email, "human": is_human,
        "display_name": getattr(user, "display_name", None) or "",
        "photo_url": getattr(user, "photo_url", None),
        "email_verified": bool(getattr(user, "email_verified", False)),
        "disabled": bool(getattr(user, "disabled", False)),
        "google_sub": google_sub,
        "has_password_provider": any(item.provider_id == "password" for item in providers),
        "password_hash": raw.get("passwordHash") or getattr(user, "password_hash", None),
        "password_salt": raw.get("salt") or raw.get("passwordSalt") or getattr(user, "password_salt", None),
    }


def firebase_hash_config(project_id, key_path):
    credentials_obj = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/identitytoolkit"])
    response = AuthorizedSession(credentials_obj).get(
        f"https://identitytoolkit.googleapis.com/admin/v2/projects/{project_id}/config",
        timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Firebase password hash configuration request failed (HTTP {response.status_code})")
    config = response.json().get("signIn", {}).get("hashConfig") or {}
    required = ("algorithm", "signerKey", "saltSeparator", "rounds", "memoryCost")
    if config.get("algorithm") != "SCRYPT" or any(config.get(key) is None for key in required):
        raise RuntimeError("Firebase project does not expose a complete SCRYPT configuration; stopping safely")
    return config


def source_collection(documents, collection, identifier):
    for document_id, payload in documents.get(collection, ()):
        if str(payload.get(identifier, "")).strip().lower() == str(identifier).strip().lower():
            yield payload


def validate_records(documents):
    from database import _SCHEMAS, _SPECIAL_FIELDS
    errors = []
    unknown_by_collection = {}
    counts = Counter()
    for collection, records in documents.items():
        if collection not in _SCHEMAS:
            errors.append(f"No relational destination table is defined for {collection}")
            continue
        allowed = set(_SCHEMAS[collection]) | set(_SPECIAL_FIELDS.get(collection, ())) | {"id"}
        for document_id, payload in records:
            counts[collection] += 1
            unknown = set(payload or ()) - allowed
            if unknown:
                unknown_by_collection.setdefault(collection, set()).update(unknown)
    for collection, fields in unknown_by_collection.items():
        errors.append(f"Unmodeled source fields in {collection}: {', '.join(sorted(fields))}")
    return counts, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the validated import to MySQL (default: dry-run)")
    parser.add_argument("--key", default=os.getenv("FIREBASE_CREDENTIALS", "firebase-credentials.json"))
    args = parser.parse_args()

    firebase_app, client, service_credential = connect_firebase(args.key)
    project_id = service_credential.project_id
    print("Reading Firestore source (read-only)...", flush=True)
    documents, source_counts = collect_firestore(client, project_id)
    print("Reading Firebase Auth inventory...", flush=True)
    auth_users = [user_data(user) for user in auth_inventory(firebase_app)]
    humans = [user for user in auth_users if user["human"]]
    email_counts = Counter(user["email"] for user in humans if user["email"])
    duplicate_emails = sorted(email for email, count in email_counts.items() if count > 1)
    record_counts, errors = validate_records(documents)

    from app import app
    from database import (Account, LegacyPasswordHashConfig, _RECORD_MODELS,
                          _encrypt_legacy_config, _write_record,
                          apply_additive_schema_updates, db)

    with app.app_context():
        db.create_all()
        apply_additive_schema_updates()
        hash_config = firebase_hash_config(project_id, args.key)
        auth_by_email = {user["email"]: user for user in humans if user["email"]}
        # Never replace existing local app rows during a one-time cutover.
        existing = {account.email.lower() for account in Account.query.all()}
        collisions = sorted(existing & set(auth_by_email))
        if duplicate_emails:
            errors.append(f"Firebase Auth contains duplicate human email addresses ({len(duplicate_emails)})")
        if collisions:
            errors.append(f"MySQL already contains {len(collisions)} matching account email(s); reconcile before import")
        missing_hashes = [user for user in humans if user["password_hash"] and not user["password_salt"]]
        if missing_hashes:
            errors.append(f"{len(missing_hashes)} password account(s) lack an importable Firebase salt; stopping to preserve passwords")
        missing_password_hash = [user for user in humans if user["has_password_provider"] and not user["password_hash"]]
        if missing_password_hash:
            errors.append(f"{len(missing_password_hash)} password-provider account(s) do not expose a password hash; stopping per password-preservation requirement")
        no_email_humans = [user for user in humans if not user["email"]]
        if no_email_humans:
            errors.append(f"{len(no_email_humans)} non-anonymous Auth identity/identities have no email; target account schema requires email, so reconcile before import")

        def ids(collection):
            return {str(document_id) for document_id, _ in documents.get(collection, ())}
        def check_fk(source_collection, source_field, target_ids):
            dangling = sum(1 for _, payload in documents.get(source_collection, ())
                           if payload.get(source_field) and str(payload[source_field]) not in target_ids)
            if dangling:
                errors.append(f"{source_collection}.{source_field} has {dangling} reference(s) without a target; preserve/fix the relationship before import")
        auth_ids = {user["uid"] for user in humans}
        # The availability relation is keyed by the provider document ID.
        for doc_id, _ in documents.get("provider_availability", ()):
            if doc_id not in ids("providers"):
                errors.append("provider_availability contains an ID without a matching provider record")
                break
        check_fk("appointments", "providerId", ids("providers"))
        check_fk("appointments", "userId", auth_ids)
        check_fk("notifications", "appointmentId", ids("appointments"))
        check_fk("video_signals", "appointmentId", ids("appointments"))
        check_fk("call_history", "appointmentId", ids("appointments"))
        check_fk("messages", "conversationId", ids("conversations"))
        for doc_id, _ in documents.get("providers", ()):
            if doc_id not in auth_ids:
                errors.append("A provider record has no human Auth account; resolve before import")
                break

        admins = list(documents.get("admins", ()))
        admin_emails = {str(data.get("email", "")).strip().lower() for _, data in admins if data.get("email")}
        firebase_uids = {user["uid"] for user in humans}
        print(f"Firebase project: {project_id}")
        print(f"Auth users: {len(auth_users)} total; {len(humans)} human; {len(auth_users) - len(humans)} anonymous-like (will not create MySQL accounts)")
        print("Firestore document counts: " + ", ".join(f"{key}={value}" for key, value in sorted(source_counts.items())))
        print(f"Total Firestore docs scanned: {sum(source_counts.values())}")
        print(f"MySQL existing accounts: {len(existing)}")
        if errors:
            print("Preflight FAILED; no MySQL data was changed:")
            for error in errors:
                print(f" - {error}")
            raise SystemExit(2)
        print("Preflight passed: all source fields have relational destinations and account/password checks are safe.")
        if not args.apply:
            print("Dry-run only. Rerun with --apply to perform the one-time MySQL import.")
            return

        if not app.config.get("SECRET_KEY") or app.config["SECRET_KEY"] == "change-this-before-deployment":
            raise RuntimeError("A persistent, strong SECRET_KEY is required to encrypt imported Firebase SCRYPT parameters")
        if db.session.query(LegacyPasswordHashConfig).count():
            raise RuntimeError("Legacy Firebase hash config already exists; refusing a duplicate import")
        try:
            # All writes occur in one MySQL transaction. Firebase is only read.
            for item in humans:
                role = "admin" if item["email"] in admin_emails else "user"
                account = Account(id=item["uid"], email=item["email"],
                                  password_hash=item["password_hash"], password_salt=item["password_salt"],
                                  password_hash_scheme="firebase_scrypt" if item["password_hash"] else "werkzeug",
                                  google_sub=item["google_sub"], display_name=item["display_name"],
                                  photo_url=item["photo_url"], email_verified=item["email_verified"],
                                  role=role, active=not item["disabled"])
                db.session.add(account)
            db.session.flush()
            hash_config_row = LegacyPasswordHashConfig(id=1,
                signer_key_encrypted=_encrypt_legacy_config(hash_config["signerKey"].encode("ascii")),
                salt_separator_encrypted=_encrypt_legacy_config(hash_config["saltSeparator"].encode("ascii")),
                rounds=int(hash_config["rounds"]), memory_cost=int(hash_config["memoryCost"]))
            db.session.add(hash_config_row)
            for collection in ORDER:
                model = _RECORD_MODELS.get(collection)
                if model is None:
                    continue
                for document_id, payload in documents.get(collection, ()):
                    _write_record(collection, model, document_id, None, payload, commit=False)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        print("Import transaction committed. Per-table verification:")
        for collection in ORDER:
            model = _RECORD_MODELS.get(collection)
            expected = record_counts[collection]
            if model is not None:
                actual = model.query.count()
                if expected != actual:
                    raise RuntimeError(f"Post-import count mismatch for {collection}: expected {expected}, found {actual}")
                print(f" {collection}: {actual}")
        print(f" accounts: {Account.query.count()} (human Auth accounts only)")
        print("Firebase source was not modified.")

    firebase_admin.delete_app(firebase_app)


if __name__ == "__main__":
    main()
