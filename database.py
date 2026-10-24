"""MySQL persistence for Menti.

Feature records live in one typed MySQL table per domain collection. The
Firestore-shaped reference/query objects below are only a transitional Python
API: they map to domain tables and never persist a JSON document/blob.
Structured values are decomposed into typed, collection-specific relational
child tables. No generic attribute or JSON payload tables are used.
"""
from __future__ import annotations

import json
import base64
import hashlib
import hmac
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace

from flask import current_app
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy import asc, desc
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.mysql import LONGTEXT
from werkzeug.security import check_password_hash, generate_password_hash
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

db = SQLAlchemy()
DELETE_FIELD = object()


class Account(db.Model):
    __tablename__ = "accounts"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255))
    password_salt = db.Column(db.String(255))
    password_hash_scheme = db.Column(db.String(32), nullable=False, default="werkzeug", server_default=db.text("'werkzeug'"))
    google_sub = db.Column(db.String(255), unique=True, index=True)
    display_name = db.Column(db.String(255), nullable=False, default="")
    photo_url = db.Column(db.Text)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    role = db.Column(db.String(32), nullable=False, default="user", index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.password_salt = None
        self.password_hash_scheme = "werkzeug"

    def check_password(self, password):
        if not self.password_hash:
            return False
        scheme = self.password_hash_scheme or "werkzeug"
        if scheme == "firebase_scrypt":
            config = LegacyPasswordHashConfig.query.get(1)
            if not config:
                return False
            try:
                signer_key = _decrypt_legacy_config(config.signer_key_encrypted)
                salt_separator = _decrypt_legacy_config(config.salt_separator_encrypted)
                actual = firebase_scrypt_hash(password, self.password_salt, signer_key,
                                              salt_separator, config.rounds, config.memory_cost)
            except (ValueError, TypeError):
                return False
            valid = hmac.compare_digest(actual, self.password_hash)
        elif scheme == "legacy_sha256":
            valid = hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(), self.password_hash)
        elif scheme == "werkzeug":
            valid = check_password_hash(self.password_hash, password)
        else:
            return False
        if valid and scheme != "werkzeug":
            # Preserve the user's existing password while upgrading the imported
            # Firebase/legacy digest to the application's current salted hash.
            self.set_password(password)
        return valid

    @property
    def uid(self):
        return self.id


class LegacyPasswordHashConfig(db.Model):
    """Encrypted parameters needed to verify imported Firebase SCRYPT hashes."""
    __tablename__ = "legacy_password_hash_config"
    id = db.Column(db.Integer, primary_key=True)
    signer_key_encrypted = db.Column(db.LargeBinary, nullable=False)
    salt_separator_encrypted = db.Column(db.LargeBinary, nullable=False)
    rounds = db.Column(db.Integer, nullable=False)
    memory_cost = db.Column(db.Integer, nullable=False)


def _legacy_config_cipher():
    from flask import current_app
    secret = str(current_app.config.get("SECRET_KEY") or "")
    if not secret or secret == "change-this-before-deployment":
        raise RuntimeError("Set a strong SECRET_KEY before importing legacy password hashes")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = hashlib.sha256(b"menti-legacy-password-config-v1\0" + secret.encode("utf-8")).digest()
    return AESGCM(key)


def _encrypt_legacy_config(value):
    nonce = os.urandom(12)
    encrypted = _legacy_config_cipher().encrypt(nonce, bytes(value), b"menti-legacy-password-config-v1")
    return nonce + encrypted


def _decrypt_legacy_config(value):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not value or len(value) < 29:
        raise ValueError("Invalid encrypted password hash configuration")
    nonce, encrypted = value[:12], value[12:]
    secret = str(current_app.config.get("SECRET_KEY") or "")
    if not secret or secret == "change-this-before-deployment":
        raise RuntimeError("Set a strong SECRET_KEY to verify imported password hashes")
    key = hashlib.sha256(b"menti-legacy-password-config-v1\0" + secret.encode("utf-8")).digest()
    return AESGCM(key).decrypt(nonce, encrypted, b"menti-legacy-password-config-v1")


def firebase_scrypt_hash(password, base64_salt, signer_key, salt_separator, rounds, memory_cost):
    """Compute Firebase's modified SCRYPT output using stored project parameters."""
    try:
        salt = base64.b64decode(base64_salt, validate=True)
        separator = base64.b64decode(salt_separator, validate=True)
        signer = base64.b64decode(signer_key, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Malformed Firebase SCRYPT parameters") from exc
    if not (1 <= int(rounds) <= 8 and 1 <= int(memory_cost) <= 14):
        raise ValueError("Unsupported Firebase SCRYPT cost parameters")
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt + separator,
                             n=1 << int(memory_cost), r=int(rounds), p=1,
                             dklen=32, maxmem=128 * 1024 * 1024)
    encryptor = Cipher(algorithms.AES(derived), modes.CTR(bytes(16))).encryptor()
    return base64.b64encode(encryptor.update(signer) + encryptor.finalize()).decode("ascii")


# Explicit scalar columns for the live application domains. Nested arrays and
# objects use collection-specific child tables; scalar fields remain
# typed/indexable SQL.
_SCHEMAS = {
    "providers": {"email": "text", "firstName": "text", "middleName": "text", "lastName": "text", "displayName": "text", "role": "text", "licenseNumber": "text", "status": "text", "createdAt": "text", "updatedAt": "text", "approvedAt": "text", "rejectedAt": "text", "rejectionReason": "text"},
    "conversations": {"userId": "text", "isAnonymous": "bool", "isIncognito": "bool", "title": "text", "createdAt": "text", "lastUpdated": "text", "isArchived": "bool", "lastMessage": "text"},
    "messages": {"conversationId": "text", "message": "long", "sender": "text", "timestamp": "text", "order": "int", "emotion": "text"},
    "user_assessments": {"userId": "text", "userEmail": "text", "userName": "text", "totalScore": "int", "maxScore": "int", "profile": "text", "profileDescription": "long", "crisisDetected": "bool", "completedAt": "text", "version": "int"},
    "provider_availability": {"timezone": "text", "slotMinutes": "int", "updatedAt": "text"},
    "provider_events": {"providerId": "text", "title": "text", "start": "text", "end": "text", "description": "long", "createdAt": "text"},
    "appointments": {"userId": "text", "userName": "text", "providerId": "text", "providerName": "text", "start": "text", "end": "text", "duration": "int", "status": "text", "reason": "long", "providerNotes": "long", "providerNote": "long", "declineReason": "long", "cancellationReason": "long", "createdAt": "text", "updatedAt": "text", "createdByProvider": "bool", "rescheduledFrom": "text", "rescheduledTo": "text", "callDurationSeconds": "int", "callEndedAt": "text", "callEndedBy": "text", "notesUpdatedAt": "text", "reminder24Sent": "bool"},
    "notifications": {"userId": "text", "providerId": "text", "type": "text", "appointmentId": "text", "message": "long", "read": "bool", "readAt": "text", "createdAt": "text"},
    "video_signals": {"appointmentId": "text", "active": "bool", "updatedAt": "text", "callerId": "text", "calleeId": "text", "userDeclined": "bool", "callEnded": "bool", "endedBy": "text", "connectedAt": "text", "endedAt": "text"},
    "call_history": {"appointmentId": "text", "userId": "text", "providerId": "text", "createdBy": "text", "action": "text", "timestamp": "text", "startedAt": "text", "endedAt": "text", "duration": "int", "status": "text"},
    "emotion_logs": {"userId": "text", "emotion": "text", "conversationId": "text", "isAnonymous": "bool", "isIncognito": "bool", "timestamp": "text", "date": "text"},
    "crisis_alerts": {"userId": "text", "isAnonymous": "bool", "crisisType": "text", "severity": "text", "emotion": "text", "mode": "text", "messageSnippet": "long", "timestamp": "text", "date": "text", "reviewed": "bool"},
    "user_profiles": {"displayName": "text", "email": "text", "birthday": "text", "photoURL": "huge", "createdAt": "text", "updatedAt": "text"},
    "wellness_plans": {"userId": "text", "status": "text", "cadence": "text", "notes": "long", "isPaused": "bool", "createdAt": "text", "updatedAt": "text", "generatedAt": "text", "dominantEmotion": "text"},
    "journal_entries": {"userId": "text", "prompt": "long", "content": "long", "mood": "text", "createdAt": "text", "updatedAt": "text"},
    "app_content": {"title": "text", "version": "int"},
    "backup_history": {"triggeredBy": "text", "timestamp": "text", "date": "text", "sizeBytes": "int", "status": "text"},
    "admins": {"email": "text", "created_at": "text", "created_by": "text", "password_hash": "text"},
    "users": {"displayName": "text", "email": "text", "firstName": "text", "middleName": "text", "lastName": "text", "isActive": "bool", "createdAt": "text", "lastLogin": "text"},
}


def _snake(name):
    import re
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _column(kind):
    return {"bool": db.Boolean, "int": db.Integer, "long": db.Text,
            "huge": LONGTEXT}.get(kind, db.String(191))


_RECORD_MODELS = {}
for _collection, _schema in _SCHEMAS.items():
    _attrs = {
        "__tablename__": _collection,
        "id": db.Column(db.String(255), ForeignKey("accounts.id", ondelete="CASCADE") if _collection == "providers" else
                         ForeignKey("providers.id", ondelete="CASCADE") if _collection == "provider_availability" else
                         ForeignKey("appointments.id", ondelete="CASCADE") if _collection == "video_signals" else None,
                         primary_key=True),
        "created_at": db.Column(db.DateTime, nullable=False, default=datetime.utcnow),
        "updated_at": db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow),
    }
    for _field_name, _kind in _schema.items():
        _fk = {
            ("messages", "conversationId"): "conversations.id",
            ("provider_events", "providerId"): "providers.id",
            ("appointments", "providerId"): "providers.id",
            ("appointments", "userId"): "accounts.id",
            ("notifications", "appointmentId"): "appointments.id",
            ("video_signals", "appointmentId"): "appointments.id",
            ("call_history", "appointmentId"): "appointments.id",
        }.get((_collection, _field_name))
        _attrs[_snake(_field_name)] = db.Column(_column(_kind), ForeignKey(_fk, ondelete="CASCADE") if _fk else None, index=_field_name in {
            "userId", "providerId", "status", "conversationId", "appointmentId", "timestamp", "date"
        })
    _RECORD_MODELS[_collection] = type(f"{_collection.title().replace('_', '')}Record", (db.Model,), _attrs)


def apply_additive_schema_updates():
    """Apply safe additive column/index updates after create_all().

    SQLAlchemy create_all does not alter an existing MySQL table. This small
    migration step makes schema additions deployable without ever dropping or
    rewriting user data. Destructive/renaming changes require an explicit
    versioned migration instead.
    """
    engine = db.engine
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    preparer = engine.dialect.identifier_preparer
    with engine.begin() as connection:
        for table in db.metadata.sorted_tables:
            if table.name not in tables:
                continue
            reflected = {column["name"]: column for column in inspect(connection).get_columns(table.name)}
            present = set(reflected)
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.server_default is None:
                    raise RuntimeError(f"Refusing unsafe non-null schema addition: {table.name}.{column.name}")
                table_name = preparer.quote(table.name)
                column_name = preparer.quote(column.name)
                sql_type = column.type.compile(dialect=engine.dialect)
                default = f" DEFAULT {column.server_default.arg}" if column.server_default is not None else ""
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}{default}"))
                present.add(column.name)
                reflected[column.name] = {"name": column.name, "type": column.type}
            for column in table.columns:
                actual = reflected.get(column.name, {})
                actual_type = actual.get("type")
                if (engine.dialect.name == "mysql" and table.name == "user_profiles"
                        and column.name == _snake("photoURL")
                        and "LONGTEXT" not in str(actual_type).upper()):
                    table_name = preparer.quote(table.name)
                    column_name = preparer.quote(column.name)
                    connection.execute(text(
                        f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} LONGTEXT NULL"
                    ))
                if (engine.dialect.name == "mysql" and isinstance(column.type, String)
                        and isinstance(actual_type, String) and actual_type.length
                        and column.type.length and actual_type.length > column.type.length):
                    table_name = preparer.quote(table.name)
                    column_name = preparer.quote(column.name)
                    max_length = connection.execute(text(
                        f"SELECT MAX(CHAR_LENGTH({column_name})) FROM {table_name}"
                    )).scalar_one()
                    if max_length is None or max_length <= column.type.length:
                        sql_type = column.type.compile(dialect=engine.dialect)
                        nullable = " NULL" if column.nullable else " NOT NULL"
                        connection.execute(text(
                            f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} {sql_type}{nullable}"
                        ))
            for index in table.indexes:
                index.create(connection, checkfirst=True)
            if engine.dialect.name == "mysql":
                current_fks = inspect(connection).get_foreign_keys(table.name)
                existing = {(tuple(fk.get("constrained_columns") or ()), fk.get("referred_table"),
                             tuple(fk.get("referred_columns") or ())) for fk in current_fks}
                for foreign_key in table.foreign_keys:
                    parent_column = foreign_key.column
                    signature = ((foreign_key.parent.name,), parent_column.table.name, (parent_column.name,))
                    if signature in existing:
                        continue
                    child_table = preparer.quote(table.name)
                    child_column = preparer.quote(foreign_key.parent.name)
                    parent_table = preparer.quote(parent_column.table.name)
                    parent_name = preparer.quote(parent_column.name)
                    orphan_count = connection.execute(text(
                        f"SELECT COUNT(*) FROM {child_table} c LEFT JOIN {parent_table} p "
                        f"ON c.{child_column}=p.{parent_name} "
                        f"WHERE c.{child_column} IS NOT NULL AND p.{parent_name} IS NULL"
                    )).scalar_one()
                    if orphan_count:
                        raise RuntimeError(
                            f"Cannot add foreign key {table.name}.{foreign_key.parent.name}: "
                            f"{orphan_count} existing rows have no matching parent"
                        )
                    constraint_name = preparer.quote(f"fk_{table.name}_{foreign_key.parent.name}_{parent_column.table.name}")
                    ondelete = f" ON DELETE {foreign_key.ondelete}" if foreign_key.ondelete else ""
                    connection.execute(text(
                        f"ALTER TABLE {child_table} ADD CONSTRAINT {constraint_name} "
                        f"FOREIGN KEY ({child_column}) REFERENCES {parent_table} ({parent_name}){ondelete}"
                    ))


class AssessmentAnswer(db.Model):
    __tablename__ = "assessment_answers"
    assessment_id = db.Column(db.String(255), ForeignKey("user_assessments.id", ondelete="CASCADE"), primary_key=True)
    question_number = db.Column(db.Integer, primary_key=True)
    answer = db.Column(db.Integer, nullable=False)


class AssessmentScore(db.Model):
    __tablename__ = "assessment_scores"
    assessment_id = db.Column(db.String(255), ForeignKey("user_assessments.id", ondelete="CASCADE"), primary_key=True)
    category = db.Column(db.String(80), primary_key=True)
    score = db.Column(db.Integer, nullable=False)


class AssessmentRecommendation(db.Model):
    __tablename__ = "assessment_recommendations"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    assessment_id = db.Column(db.String(255), ForeignKey("user_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    recommendation = db.Column(db.Text, nullable=False)


class AssessmentCrisisAnswer(db.Model):
    __tablename__ = "assessment_crisis_answers"
    assessment_id = db.Column(db.String(255), ForeignKey("user_assessments.id", ondelete="CASCADE"), primary_key=True)
    question_key = db.Column(db.String(191), primary_key=True)
    answer_type = db.Column(db.String(16), nullable=False)
    answer_text = db.Column(db.Text)
    answer_number = db.Column(db.Float)
    answer_boolean = db.Column(db.Boolean)


class ProviderWeeklySlot(db.Model):
    __tablename__ = "provider_weekly_slots"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    provider_id = db.Column(db.String(255), ForeignKey("provider_availability.id", ondelete="CASCADE"), nullable=False, index=True)
    weekday = db.Column(db.String(12), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.String(16), nullable=False)
    end_time = db.Column(db.String(16), nullable=False)


class ProviderAvailabilityDuration(db.Model):
    __tablename__ = "provider_availability_durations"
    provider_id = db.Column(db.String(255), ForeignKey("provider_availability.id", ondelete="CASCADE"), primary_key=True)
    duration_minutes = db.Column(db.Integer, primary_key=True)


class ProviderAvailabilityInterval(db.Model):
    __tablename__ = "provider_availability_intervals"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    provider_id = db.Column(db.String(255), ForeignKey("provider_availability.id", ondelete="CASCADE"), nullable=False, index=True)
    interval_type = db.Column(db.String(16), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    starts_at = db.Column(db.String(64), nullable=False)
    ends_at = db.Column(db.String(64), nullable=False)


class UserProfilePreference(db.Model):
    __tablename__ = "user_profile_preferences"
    profile_id = db.Column(db.String(255), ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    preference_key = db.Column(db.String(96), primary_key=True)
    value_type = db.Column(db.String(16), nullable=False)
    value = db.Column(db.Text)


class WellnessGoal(db.Model):
    __tablename__ = "wellness_goals"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    plan_id = db.Column(db.String(255), ForeignKey("wellness_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(255), nullable=False, default="")
    description = db.Column(db.Text)
    category = db.Column(db.String(96))
    target = db.Column(db.String(255))


class WellnessReflection(db.Model):
    __tablename__ = "wellness_reflections"
    plan_id = db.Column(db.String(255), ForeignKey("wellness_plans.id", ondelete="CASCADE"), primary_key=True)
    position = db.Column(db.Integer, primary_key=True)
    prompt = db.Column(db.Text, nullable=False)


class WellnessCopingExercise(db.Model):
    __tablename__ = "wellness_coping_exercises"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    plan_id = db.Column(db.String(255), ForeignKey("wellness_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(255), nullable=False, default="")
    duration = db.Column(db.String(96))


class WellnessCopingStep(db.Model):
    __tablename__ = "wellness_coping_steps"
    exercise_id = db.Column(db.BigInteger, ForeignKey("wellness_coping_exercises.id", ondelete="CASCADE"), primary_key=True)
    position = db.Column(db.Integer, primary_key=True)
    instruction = db.Column(db.Text, nullable=False)


class WellnessEmotionCount(db.Model):
    __tablename__ = "wellness_emotion_counts"
    plan_id = db.Column(db.String(255), ForeignKey("wellness_plans.id", ondelete="CASCADE"), primary_key=True)
    emotion = db.Column(db.String(64), primary_key=True)
    count = db.Column(db.Integer, nullable=False)


class VideoCandidate(db.Model):
    __tablename__ = "video_candidates"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    signal_id = db.Column(db.String(255), ForeignKey("video_signals.id", ondelete="CASCADE"), nullable=False, index=True)
    actor = db.Column(db.String(16), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    candidate = db.Column(db.Text, nullable=False)
    sdp_mid = db.Column(db.String(128))
    sdp_mline_index = db.Column(db.Integer)
    username_fragment = db.Column(db.String(128))


class VideoSessionDescription(db.Model):
    __tablename__ = "video_session_descriptions"
    signal_id = db.Column(db.String(255), ForeignKey("video_signals.id", ondelete="CASCADE"), primary_key=True)
    side = db.Column(db.String(16), primary_key=True)
    description_type = db.Column(db.String(32))
    sdp = db.Column(db.Text, nullable=False)


_SPECIAL_FIELDS = {
    "user_assessments": {"answers", "categoryScores", "recommendations", "crisisAnswers"},
    "provider_availability": {"durations", "weekly", "exceptions", "blocked"},
    "user_profiles": {"preferences"},
    "wellness_plans": {"goals", "reflections", "copingExercises", "emotionCounts"},
    "video_signals": {"offer", "answer", "providerCandidates", "userCandidates", "candidates"},
}


def _typed_value(value_type, value):
    if value_type == "null":
        return None
    if value_type == "bool":
        return value == "1"
    if value_type == "number":
        try:
            return float(value) if "." in value else int(value)
        except (TypeError, ValueError):
            return 0
    return value


def _sync_special_fields(collection, document_id, payload, merge=False):
    document_id = str(document_id)
    if collection == "provider_availability":
        if "durations" in payload:
            ProviderAvailabilityDuration.query.filter_by(provider_id=document_id).delete()
            if isinstance(payload["durations"], list):
                db.session.add_all([ProviderAvailabilityDuration(provider_id=document_id, duration_minutes=int(value))
                    for value in payload["durations"] if str(value).isdigit()])
        if "weekly" in payload:
            ProviderWeeklySlot.query.filter_by(provider_id=document_id).delete()
            weekly = payload["weekly"] if isinstance(payload["weekly"], dict) else {}
            for day, slots in weekly.items():
                for position, slot in enumerate(slots if isinstance(slots, list) else []):
                    if isinstance(slot, dict) and slot.get("start") and slot.get("end"):
                        db.session.add(ProviderWeeklySlot(provider_id=document_id, weekday=str(day), position=position,
                            start_time=str(slot["start"]), end_time=str(slot["end"])))
        for field, interval_type in (("exceptions", "exception"), ("blocked", "blocked")):
            if field not in payload:
                continue
            ProviderAvailabilityInterval.query.filter_by(provider_id=document_id, interval_type=interval_type).delete()
            intervals = payload[field] if isinstance(payload[field], list) else []
            for position, interval in enumerate(intervals):
                if isinstance(interval, dict) and interval.get("start") and interval.get("end"):
                    db.session.add(ProviderAvailabilityInterval(provider_id=document_id, interval_type=interval_type,
                        position=position, starts_at=str(interval["start"]), ends_at=str(interval["end"])))
    elif collection == "user_profiles" and "preferences" in payload:
        preferences = payload["preferences"]
        if preferences is DELETE_FIELD or not merge:
            UserProfilePreference.query.filter_by(profile_id=document_id).delete()
        if isinstance(preferences, dict):
            for key, value in preferences.items():
                current = UserProfilePreference.query.filter_by(profile_id=document_id, preference_key=str(key)).first()
                if value is DELETE_FIELD:
                    if current:
                        db.session.delete(current)
                    continue
                if isinstance(value, bool):
                    kind, encoded = "bool", "1" if value else "0"
                elif isinstance(value, (int, float)):
                    kind, encoded = "number", str(value)
                elif value is None:
                    kind, encoded = "null", None
                else:
                    kind, encoded = "text", str(value)
                if current is None:
                    current = UserProfilePreference(profile_id=document_id, preference_key=str(key))
                    db.session.add(current)
                current.value_type, current.value = kind, encoded
    elif collection == "wellness_plans":
        if "goals" in payload:
            WellnessGoal.query.filter_by(plan_id=document_id).delete()
            goals = payload["goals"] if isinstance(payload["goals"], list) else []
            db.session.add_all([WellnessGoal(plan_id=document_id, position=i, title=str(goal.get("title", "")),
                description=goal.get("description"), category=goal.get("category"), target=goal.get("target"))
                for i, goal in enumerate(goals) if isinstance(goal, dict)])
        if "reflections" in payload:
            WellnessReflection.query.filter_by(plan_id=document_id).delete()
            reflections = payload["reflections"] if isinstance(payload["reflections"], list) else []
            db.session.add_all([WellnessReflection(plan_id=document_id, position=i, prompt=str(value))
                for i, value in enumerate(reflections)])
        if "copingExercises" in payload:
            WellnessCopingExercise.query.filter_by(plan_id=document_id).delete()
            exercises = payload["copingExercises"] if isinstance(payload["copingExercises"], list) else []
            for position, item in enumerate(exercises):
                if not isinstance(item, dict):
                    continue
                exercise = WellnessCopingExercise(plan_id=document_id, position=position,
                    title=str(item.get("title", "")), duration=item.get("duration"))
                db.session.add(exercise)
                db.session.flush()
                steps = item.get("steps", [])
                if isinstance(steps, list):
                    db.session.add_all([WellnessCopingStep(exercise_id=exercise.id, position=i, instruction=str(step))
                        for i, step in enumerate(steps)])
        if "emotionCounts" in payload:
            WellnessEmotionCount.query.filter_by(plan_id=document_id).delete()
            counts = payload["emotionCounts"] if isinstance(payload["emotionCounts"], dict) else {}
            db.session.add_all([WellnessEmotionCount(plan_id=document_id, emotion=str(key), count=int(value))
                for key, value in counts.items() if str(value).lstrip("-").isdigit()])
    elif collection == "video_signals":
        for side in ("offer", "answer"):
            if side not in payload:
                continue
            existing = VideoSessionDescription.query.filter_by(signal_id=document_id, side=side).first()
            description = payload[side]
            if description is DELETE_FIELD:
                if existing:
                    db.session.delete(existing)
            elif isinstance(description, dict) and description.get("sdp") is not None:
                if existing is None:
                    existing = VideoSessionDescription(signal_id=document_id, side=side)
                    db.session.add(existing)
                existing.description_type = str(description.get("type", ""))
                existing.sdp = str(description["sdp"])
        for key, actor in (("providerCandidates", "provider"), ("userCandidates", "user")):
            if key not in payload:
                continue
            VideoCandidate.query.filter_by(signal_id=document_id, actor=actor).delete()
            values = payload[key] if isinstance(payload[key], list) else []
            for position, value in enumerate(values[-120:]):
                if isinstance(value, dict) and value.get("candidate"):
                    db.session.add(VideoCandidate(signal_id=document_id, actor=actor, position=position,
                        candidate=str(value["candidate"]), sdp_mid=value.get("sdpMid"),
                        sdp_mline_index=value.get("sdpMLineIndex"), username_fragment=value.get("usernameFragment")))


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def _json_copy(value):
    return json.loads(json.dumps(value, default=_json_default))


def _field(data, field):
    value = data
    for component in str(field).split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(component)
    return value


def _resolve_collection(path):
    """Resolve a collection path to its relational model and parent scope."""
    parts = str(path).split("/")
    if len(parts) == 3 and parts[0] == "conversations" and parts[2] == "messages":
        return _RECORD_MODELS["messages"], "messages", parts[1]
    name = parts[0]
    model = _RECORD_MODELS.get(name)
    if not model or len(parts) != 1:
        raise ValueError(f"Unsupported relational collection path: {path}")
    return model, name, None


def _record_payload(model, collection, row):
    data = {}
    schema = _SCHEMAS[collection]
    for public_name in schema:
        data[public_name] = getattr(row, _snake(public_name))
    if collection == "user_assessments":
        answers = AssessmentAnswer.query.filter_by(assessment_id=row.id).order_by(AssessmentAnswer.question_number).all()
        scores = AssessmentScore.query.filter_by(assessment_id=row.id).all()
        recommendations = AssessmentRecommendation.query.filter_by(assessment_id=row.id).order_by(AssessmentRecommendation.position).all()
        if answers:
            data["answers"] = {str(item.question_number): item.answer for item in answers}
        if scores:
            data["categoryScores"] = {item.category: item.score for item in scores}
        if recommendations:
            data["recommendations"] = [item.recommendation for item in recommendations]
        crisis_answers = AssessmentCrisisAnswer.query.filter_by(assessment_id=row.id).all()
        if crisis_answers:
            data["crisisAnswers"] = {
                item.question_key: (item.answer_boolean if item.answer_type == "bool" else
                                    item.answer_number if item.answer_type == "number" else
                                    None if item.answer_type == "null" else item.answer_text)
                for item in crisis_answers
            }
    elif collection == "provider_availability":
        data["durations"] = [row.duration_minutes for row in ProviderAvailabilityDuration.query.filter_by(provider_id=row.id).order_by(ProviderAvailabilityDuration.duration_minutes).all()]
        weekly = {}
        for slot in ProviderWeeklySlot.query.filter_by(provider_id=row.id).order_by(ProviderWeeklySlot.weekday, ProviderWeeklySlot.position).all():
            weekly.setdefault(slot.weekday, []).append({"start": slot.start_time, "end": slot.end_time})
        data["weekly"] = weekly
        intervals = ProviderAvailabilityInterval.query.filter_by(provider_id=row.id).order_by(ProviderAvailabilityInterval.position).all()
        data["exceptions"] = [{"start": item.starts_at, "end": item.ends_at} for item in intervals if item.interval_type == "exception"]
        data["blocked"] = [{"start": item.starts_at, "end": item.ends_at} for item in intervals if item.interval_type == "blocked"]
    elif collection == "user_profiles":
        preferences = UserProfilePreference.query.filter_by(profile_id=row.id).all()
        if preferences:
            data["preferences"] = {item.preference_key: _typed_value(item.value_type, item.value) for item in preferences}
    elif collection == "wellness_plans":
        goals = WellnessGoal.query.filter_by(plan_id=row.id).order_by(WellnessGoal.position).all()
        reflections = WellnessReflection.query.filter_by(plan_id=row.id).order_by(WellnessReflection.position).all()
        exercises = WellnessCopingExercise.query.filter_by(plan_id=row.id).order_by(WellnessCopingExercise.position).all()
        data["goals"] = [{"title": item.title, "description": item.description, "category": item.category, "target": item.target} for item in goals]
        data["reflections"] = [item.prompt for item in reflections]
        data["copingExercises"] = [{"title": item.title, "duration": item.duration,
            "steps": [step.instruction for step in WellnessCopingStep.query.filter_by(exercise_id=item.id).order_by(WellnessCopingStep.position).all()]}
            for item in exercises]
        counts = WellnessEmotionCount.query.filter_by(plan_id=row.id).all()
        if counts:
            data["emotionCounts"] = {item.emotion: item.count for item in counts}
    elif collection == "video_signals":
        descriptions = VideoSessionDescription.query.filter_by(signal_id=row.id).all()
        for item in descriptions:
            data[item.side] = {"type": item.description_type, "sdp": item.sdp}
        candidates = VideoCandidate.query.filter_by(signal_id=row.id).order_by(VideoCandidate.position).all()
        for actor, key in (("provider", "providerCandidates"), ("user", "userCandidates")):
            data[key] = [{"candidate": item.candidate, "sdpMid": item.sdp_mid,
                "sdpMLineIndex": item.sdp_mline_index, "usernameFragment": item.username_fragment}
                for item in candidates if item.actor == actor]
    if collection == "messages":
        data.pop("conversationId", None)
    return {key: value for key, value in data.items() if value is not None}


def _write_record(collection, model, document_id, parent_id, payload, merge=False, commit=True):
    row = db.session.get(model, str(document_id))
    if row is None:
        row = model(id=str(document_id))
        db.session.add(row)
    elif not merge:
        for public_name in _SCHEMAS[collection]:
            setattr(row, _snake(public_name), None)
    if not merge:
        if collection == "user_assessments":
            for child_model in (AssessmentAnswer, AssessmentScore, AssessmentRecommendation, AssessmentCrisisAnswer):
                child_model.query.filter_by(assessment_id=str(document_id)).delete()
        empty_values = {
            "provider_availability": {"durations": [], "weekly": {}, "exceptions": [], "blocked": []},
            "user_profiles": {"preferences": {}},
            "wellness_plans": {"goals": [], "reflections": [], "copingExercises": [], "emotionCounts": {}},
            "video_signals": {"offer": DELETE_FIELD, "answer": DELETE_FIELD,
                "providerCandidates": [], "userCandidates": []},
        }.get(collection)
        if empty_values:
            _sync_special_fields(collection, document_id, empty_values)

    known = set(_SCHEMAS[collection])
    for key, value in (payload or {}).items():
        if key == "id":
            continue
        if collection == "messages" and key == "conversationId":
            parent_id = value
        if key in known:
            setattr(row, _snake(key), None if value is DELETE_FIELD else value)
        elif key != "conversationId" and key not in _SPECIAL_FIELDS.get(collection, set()):
            raise ValueError(f"Unmodeled field {collection}.{key}; add an explicit column or relational child table")
    if collection == "user_assessments":
        structured = {
            "answers": AssessmentAnswer,
            "categoryScores": AssessmentScore,
            "recommendations": AssessmentRecommendation,
        }
        for key, child_model in structured.items():
            if key not in (payload or {}):
                continue
            raw = payload[key]
            if key == "answers":
                child_model.query.filter_by(assessment_id=str(document_id)).delete()
                if isinstance(raw, dict) and raw is not DELETE_FIELD:
                    db.session.add_all([AssessmentAnswer(assessment_id=str(document_id), question_number=int(question), answer=int(answer))
                                       for question, answer in raw.items()])
            elif key == "categoryScores":
                child_model.query.filter_by(assessment_id=str(document_id)).delete()
                if isinstance(raw, dict) and raw is not DELETE_FIELD:
                    db.session.add_all([AssessmentScore(assessment_id=str(document_id), category=str(category), score=int(score))
                                       for category, score in raw.items()])
            else:
                child_model.query.filter_by(assessment_id=str(document_id)).delete()
                if isinstance(raw, list) and raw is not DELETE_FIELD:
                    db.session.add_all([AssessmentRecommendation(assessment_id=str(document_id), position=index, recommendation=str(value))
                                       for index, value in enumerate(raw)])
        if "crisisAnswers" in (payload or {}):
            AssessmentCrisisAnswer.query.filter_by(assessment_id=str(document_id)).delete()
            raw = (payload or {}).get("crisisAnswers")
            if isinstance(raw, dict) and raw is not DELETE_FIELD:
                rows = []
                for question, answer in raw.items():
                    if isinstance(answer, bool):
                        rows.append(AssessmentCrisisAnswer(assessment_id=str(document_id), question_key=str(question),
                                                           answer_type="bool", answer_boolean=answer))
                    elif isinstance(answer, (int, float)):
                        rows.append(AssessmentCrisisAnswer(assessment_id=str(document_id), question_key=str(question),
                                                           answer_type="number", answer_number=float(answer)))
                    elif answer is None:
                        rows.append(AssessmentCrisisAnswer(assessment_id=str(document_id), question_key=str(question),
                                                           answer_type="null"))
                    else:
                        rows.append(AssessmentCrisisAnswer(assessment_id=str(document_id), question_key=str(question),
                                                           answer_type="text", answer_text=str(answer)))
                db.session.add_all(rows)
    _sync_special_fields(collection, document_id, payload or {}, merge=merge)
    if collection == "messages" and parent_id:
        row.conversation_id = str(parent_id)
    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except Exception:
        db.session.rollback()
        raise
    return row


@dataclass
class Snapshot:
    id: str
    _payload: dict | None

    @property
    def exists(self):
        return self._payload is not None

    def to_dict(self):
        return _json_copy(self._payload) if self._payload is not None else None

    @property
    def reference(self):
        return DocumentReference(self._collection, self.id)


class DocumentReference:
    def __init__(self, collection, document_id):
        self.collection_path = collection
        self.id = str(document_id or uuid.uuid4())

    def collection(self, name):
        return CollectionReference(f"{self.collection_path}/{self.id}/{name}")

    def get(self):
        model, collection, parent_id = _resolve_collection(self.collection_path)
        row = db.session.get(model, self.id)
        if row is not None and parent_id and row.conversation_id != parent_id:
            row = None
        snap = Snapshot(self.id, _json_copy(_record_payload(model, collection, row)) if row else None)
        snap._collection = self.collection_path
        return snap

    def set(self, payload, merge=False, commit=True):
        model, collection, parent_id = _resolve_collection(self.collection_path)
        _write_record(collection, model, self.id, parent_id, payload or {}, merge=merge, commit=commit)

    def update(self, payload):
        model, collection, parent_id = _resolve_collection(self.collection_path)
        row = db.session.get(model, self.id)
        if not row or (parent_id and row.conversation_id != parent_id):
            raise KeyError(f"Document {self.collection_path}/{self.id} does not exist")
        _write_record(collection, model, self.id, parent_id, payload or {}, merge=True)

    def delete(self):
        model, collection, parent_id = _resolve_collection(self.collection_path)
        row = db.session.get(model, self.id)
        if row is not None and (not parent_id or row.conversation_id == parent_id):
            db.session.delete(row)
        if collection == "conversations":
            MessageRecord = _RECORD_MODELS["messages"]
            for message in MessageRecord.query.filter_by(conversation_id=self.id).all():
                db.session.delete(message)
        db.session.commit()

    def on_snapshot(self, callback):
        return _Watcher(lambda: [self.get()], callback)


class CollectionReference:
    def __init__(self, path):
        self.path = path

    def document(self, document_id=None):
        return DocumentReference(self.path, document_id or str(uuid.uuid4()))

    def add(self, payload):
        ref = self.document()
        ref.set(payload)
        return ref, datetime.utcnow()

    def where(self, field=None, operator=None, value=None, **kwargs):
        # Accept the keyword-filter form used by the old Firestore helper.
        if "filter" in kwargs:
            clause = kwargs["filter"]
            field, operator, value = clause.field_path, clause.op_string, clause.value
        return Query(self.path, [(str(field), operator, value)])

    def stream(self, **kwargs):
        return Query(self.path, []).stream(**kwargs)

    def order_by(self, field, direction="ASCENDING"):
        return Query(self.path, []).order_by(field, direction)

    def limit(self, count):
        return Query(self.path, []).limit(count)

    def on_snapshot(self, callback):
        return _Watcher(lambda: list(self.stream()), callback)


class Query(CollectionReference):
    def __init__(self, path, filters, ordering=None, count=None):
        super().__init__(path)
        self.filters = filters
        self.ordering = ordering
        self.count = count

    def where(self, field=None, operator=None, value=None, **kwargs):
        if "filter" in kwargs:
            clause = kwargs["filter"]
            field, operator, value = clause.field_path, clause.op_string, clause.value
        return Query(self.path, self.filters + [(str(field), operator, value)], self.ordering, self.count)

    def limit(self, count):
        return Query(self.path, self.filters, self.ordering, int(count))

    def order_by(self, field, direction="ASCENDING"):
        return Query(self.path, self.filters, (str(field), str(direction).upper()), self.count)

    def stream(self, **kwargs):
        model, collection, parent_id = _resolve_collection(self.path)
        query = model.query
        if parent_id:
            query = query.filter_by(conversation_id=parent_id)
        python_filters = []
        for field, operator, expected in self.filters:
            column_name = _snake(field)
            column = getattr(model, column_name, None) if "." not in field else None
            if column is None:
                python_filters.append((field, operator, expected))
            elif operator in ("==", "EQUAL"):
                query = query.filter(column == expected)
            elif operator in ("!=", "NOT_EQUAL"):
                query = query.filter(column != expected)
            elif operator in ("in", "IN"):
                query = query.filter(column.in_(list(expected)))
            else:
                raise ValueError(f"Unsupported MySQL document query operator: {operator}")
        if self.ordering:
            field, direction = self.ordering
            column = getattr(model, _snake(field), None) if "." not in field else None
            if column is not None:
                query = query.order_by(desc(column) if direction == "DESCENDING" else asc(column))
        if self.count is not None:
            query = query.limit(self.count)
        rows = query.all()
        for field, operator, expected in python_filters:
            if operator in ("==", "EQUAL"):
                rows = [row for row in rows if _field(_record_payload(model, collection, row), field) == expected]
            elif operator in ("!=", "NOT_EQUAL"):
                rows = [row for row in rows if _field(_record_payload(model, collection, row), field) != expected]
            elif operator in ("in", "IN"):
                rows = [row for row in rows if _field(_record_payload(model, collection, row), field) in expected]
            else:
                raise ValueError(f"Unsupported MySQL document query operator: {operator}")
        if self.ordering and ("." in self.ordering[0] or not hasattr(model, _snake(self.ordering[0]))):
            field, direction = self.ordering
            rows.sort(key=lambda row: (_field(_record_payload(model, collection, row), field) is None,
                                       _field(_record_payload(model, collection, row), field)),
                      reverse=direction == "DESCENDING")
        snapshots = []
        for row in rows:
            snap = Snapshot(row.id, _json_copy(_record_payload(model, collection, row)))
            snap._collection = self.path
            snapshots.append(snap)
        return iter(snapshots)

    def on_snapshot(self, callback):
        return _Watcher(lambda: list(self.stream()), callback)


class _Watcher:
    def __init__(self, fetch, callback):
        self._stop = threading.Event()
        self._fetch = fetch
        self._callback = callback
        self._app = current_app._get_current_object()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        previous = None
        while not self._stop.is_set():
            try:
                with self._app.app_context():
                    snapshots = self._fetch()
                    signature = json.dumps([(s.id, s.to_dict()) for s in snapshots], sort_keys=True, default=_json_default)
                    if signature != previous:
                        previous = signature
                        changes = [SimpleNamespace(document=s, type=SimpleNamespace(name="ADDED")) for s in snapshots]
                        self._callback(snapshots, changes, datetime.utcnow())
            except Exception as exc:
                with self._app.app_context():
                    db.session.rollback()
                print(f"[mysql watcher] {exc}")
            self._stop.wait(2)

    def unsubscribe(self):
        self._stop.set()


class MySQLDocumentStore:
    def collection(self, name):
        return CollectionReference(name)


class UserRecord:
    def __init__(self, account):
        self.uid = account.id
        self.email = account.email
        self.display_name = account.display_name
        self.photo_url = account.photo_url
        self.email_verified = account.email_verified
        # Firebase-compatible metadata used by the existing admin reports.
        created_ms = int(account.created_at.timestamp() * 1000) if account.created_at else None
        updated_ms = int(account.updated_at.timestamp() * 1000) if account.updated_at else created_ms
        self.user_metadata = SimpleNamespace(
            creation_timestamp=created_ms,
            last_sign_in_timestamp=updated_ms,
        )
        providers = []
        if account.password_hash:
            providers.append(SimpleNamespace(provider_id='password'))
        if account.google_sub:
            providers.append(SimpleNamespace(provider_id='google.com'))
        self.provider_data = providers


class UserListPage:
    def __init__(self, users):
        self.users = users

    def get_next_page(self):
        return None

    def iterate_all(self):
        return iter(self.users)


class MySQLAuthCompat:
    """Temporary method adapter for existing profile/admin reporting helpers."""
    @staticmethod
    def verify_id_token(_token):
        account = current_account()
        if not account:
            raise ValueError("Authentication session is missing")
        return {"uid": account.id, "sub": account.id, "email": account.email,
                "name": account.display_name, "picture": account.photo_url}

    @staticmethod
    def get_user(uid):
        account = db.session.get(Account, str(uid))
        if not account:
            raise LookupError("Account not found")
        return UserRecord(account)

    @staticmethod
    def get_user_by_email(email):
        account = Account.query.filter_by(email=str(email).lower()).first()
        if not account:
            raise LookupError("Account not found")
        return UserRecord(account)

    @staticmethod
    def update_user(uid, display_name=None, photo_url=None):
        account = db.session.get(Account, str(uid))
        if not account:
            raise LookupError("Account not found")
        if display_name is not None:
            account.display_name = display_name
        if photo_url is not None:
            account.photo_url = photo_url
        db.session.commit()
        return UserRecord(account)

    @staticmethod
    def list_users():
        return UserListPage([UserRecord(account) for account in Account.query.all()])


def current_account():
    from flask import session
    account_id = session.get("account_id")
    return db.session.get(Account, account_id) if account_id else None


def create_account(email, password=None, display_name="", google_sub=None, photo_url=None, email_verified=False):
    email = str(email or "").strip().lower()
    if not email:
        raise ValueError("Email is required")
    account = Account.query.filter_by(email=email).first()
    if account:
        if password:
            raise ValueError("An account with this email already exists. Please sign in instead.")
        if google_sub and account.google_sub and account.google_sub != google_sub:
            raise ValueError("This email is already connected to another Google account")
        account.google_sub = google_sub or account.google_sub
        account.display_name = display_name or account.display_name
        account.photo_url = photo_url or account.photo_url
        account.email_verified = account.email_verified or email_verified
    else:
        account = Account(email=email, google_sub=google_sub, display_name=display_name or email.split("@")[0],
                          photo_url=photo_url, email_verified=email_verified)
        db.session.add(account)
    if password:
        account.set_password(password)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError("An account with this email or Google identity already exists")
    return account


def promote_initial_admin(account, admin_email):
    if admin_email and account.email == admin_email.strip().lower():
        account.role = "admin"
        db.session.commit()
    return account

