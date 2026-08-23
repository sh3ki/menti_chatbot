"""
Menti Chatbot - Main Flask Application
Emotional Support Chatbot with Firebase Authentication and OpenAI Integration
"""

from flask import Flask, request, jsonify, render_template, session, redirect, url_for, Response
from flask_cors import CORS
from functools import wraps
import os, json, queue, threading
from dotenv import load_dotenv
from groq import Groq
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth
from datetime import datetime, timedelta
import hashlib
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

# Load environment variables
load_dotenv()


def _is_truthy_env(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


DEBUG_AI_PIPELINE = _is_truthy_env(os.getenv('MENTI_DEBUG_AI_PIPELINE', 'false'))


def _debug_ai_log(label, text):
    """Print debug logs for AI pipeline only when explicitly enabled."""
    if not DEBUG_AI_PIPELINE:
        return
    compact = ' '.join(str(text or '').split())
    if len(compact) > 900:
        compact = compact[:900] + '...'
    print(f"[AI-DEBUG] {label}: {compact}")

# Initialize Flask app
app = Flask(__name__, 
            static_folder='assets',
            static_url_path='/static')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
CORS(app)

# In-memory conversation storage (use Redis/database for production)
conversation_history = {}

# Unified offline response when Groq and model fallback both fail.
OFFLINE_REPLY = "Menti's server is currently offline. Please try again later."

# ==================== ADMIN CONFIGURATION ====================

# Admin credentials - override with environment variables
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin@menti.com')
_raw_admin_password = os.getenv('ADMIN_PASSWORD', 'Admin123!')
# Store as SHA-256 hash for comparison
ADMIN_PASSWORD_HASH = hashlib.sha256(_raw_admin_password.encode()).hexdigest()


def admin_required(f):
    """Decorator to protect admin routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated_function

# Initialize Groq Client
groq_api_key = os.getenv('GROQ_API_KEY')
groq_api_key_fallback = (os.getenv('GROQ_API_KEY_FALLBACK') or '').strip()
if not groq_api_key:
    print("❌ ERROR: GROQ_API_KEY not found in environment variables!")
    print("Please add GROQ_API_KEY to your .env file")
else:
    groq_client = Groq(api_key=groq_api_key)
    groq_client_fallback = None
    if groq_api_key_fallback and groq_api_key_fallback != groq_api_key:
        groq_client_fallback = Groq(api_key=groq_api_key_fallback)
        print("✅ Groq fallback API client initialized successfully")
    print("✅ Groq primary API client initialized successfully")
    # Prefer a chat-capable model by default.
    # Keep backward compatibility with GROQ_MODEL, but allow GROQ_CHAT_MODEL override.
    DEFAULT_CHAT_MODEL = 'openai/gpt-oss-20b'
    configured_model = os.getenv('GROQ_CHAT_MODEL') or os.getenv('GROQ_MODEL') or DEFAULT_CHAT_MODEL
    groq_fallback_model = os.getenv('GROQ_FALLBACK_MODEL', 'openai/gpt-oss-120b')

    # Guard against common non-chat classifier/moderation models that return empty content.
    _non_chat_hints = ('prompt-guard', 'guard', 'moderation', 'classifier')
    if any(h in configured_model.lower() for h in _non_chat_hints):
        print(
            f"⚠️ GROQ model '{configured_model}' appears non-chat. "
            f"Falling back to chat model '{DEFAULT_CHAT_MODEL}'."
        )
        groq_model = DEFAULT_CHAT_MODEL
    else:
        groq_model = configured_model

    print(f"ℹ️ Using Groq model: {groq_model}")
    print(f"ℹ️ Backup Groq model: {groq_fallback_model}")

    # Centralized Groq chat wrapper: returns the SDK response or None on any error
    def groq_chat_create(**kwargs):
        """Call Groq chat completions safely.
        Returns the raw response object, or None on any API error or when the model returns a
        non-chat/classifier-style output (e.g., numeric score outputs).
        """
        if 'groq_client' not in globals() or not groq_client:
            print('Groq client not initialized')
            return None

        if 'top_p' not in kwargs:
            kwargs['top_p'] = 1
        if 'stream' not in kwargs:
            kwargs['stream'] = False

        allow_empty_retry = kwargs.pop('_allow_empty_retry', True)
        allow_model_failover = kwargs.pop('_allow_model_failover', True)
        debug_label = kwargs.pop('_debug_label', '')
        import re as _local_re

        def _call_once(client_obj, request_kwargs, call_label):
            if DEBUG_AI_PIPELINE:
                req_messages = request_kwargs.get('messages') or []
                _debug_ai_log(
                    f"REQ {call_label}",
                    f"model={request_kwargs.get('model')} max_tokens={request_kwargs.get('max_tokens')} temp={request_kwargs.get('temperature')} msg_count={len(req_messages)}"
                )
            try:
                local_resp = client_obj.chat.completions.create(**request_kwargs)
            except Exception as ex:
                print(f"Groq API error ({call_label}): {ex}")
                _debug_ai_log(f"FAIL {call_label}", f"api_error={ex}")
                return None, ''

            try:
                choices = getattr(local_resp, 'choices', None)
                if not choices or len(choices) == 0:
                    print(f"Groq response contained no choices ({call_label})")
                    _debug_ai_log(f"FAIL {call_label}", 'no_choices')
                    return None, ''
                msg = getattr(choices[0], 'message', None)
                local_content = (getattr(msg, 'content', '') or '').strip() if msg else ''
            except Exception as ex:
                print(f"Error inspecting Groq response ({call_label}): {ex}")
                _debug_ai_log(f"FAIL {call_label}", f"inspect_error={ex}")
                return None, ''

            if not local_content:
                _debug_ai_log(f"FAIL {call_label}", 'empty_content')
                return None, ''

            if _local_re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", local_content):
                print(f"Groq returned numeric-only content ('{local_content}') in {call_label}; treating as non-chat output")
                _debug_ai_log(f"FAIL {call_label}", 'numeric_only_content')
                return None, ''

            return local_resp, local_content

        def _with_model_fallback(request_kwargs):
            variants = [dict(request_kwargs)]

            if allow_empty_retry:
                base_max = request_kwargs.get('max_tokens')
                if isinstance(base_max, int) and base_max < 220:
                    retry_kwargs = dict(request_kwargs)
                    retry_kwargs['max_tokens'] = min(max(base_max + 40, int(base_max * 1.5)), 220)
                    variants.append(retry_kwargs)

            req_model = request_kwargs.get('model')
            if allow_model_failover and groq_fallback_model and req_model != groq_fallback_model:
                model_kwargs = dict(request_kwargs)
                model_kwargs['model'] = groq_fallback_model
                if isinstance(model_kwargs.get('max_tokens'), int):
                    model_kwargs['max_tokens'] = min(model_kwargs['max_tokens'] + 100, 2000)  # Removed artificial 260 cap
                variants.append(model_kwargs)

            return variants

        base_kwargs = dict(kwargs)
        label_root = debug_label or 'groq_chat_create'

        # Attempt order:
        # 1) Primary API key + model/retry/model-fallback
        # 2) Fallback API key + model/retry/model-fallback
        client_attempts = [('primary_key', groq_client)]
        if 'groq_client_fallback' in globals() and groq_client_fallback:
            client_attempts.append(('fallback_key', groq_client_fallback))

        selected_resp = None
        selected_content = ''
        for key_label, client_obj in client_attempts:
            for idx, attempt_kwargs in enumerate(_with_model_fallback(base_kwargs), start=1):
                call_label = f"{label_root}:{key_label}:try{idx}"
                local_resp, local_content = _call_once(client_obj, attempt_kwargs, call_label)
                if local_resp is not None and local_content:
                    if key_label == 'fallback_key':
                        print("Groq API-key fallback succeeded")
                    if attempt_kwargs.get('model') != base_kwargs.get('model'):
                        print(f"Groq model fallback succeeded with model {attempt_kwargs.get('model')}")
                    selected_resp = local_resp
                    selected_content = local_content
                    break
            if selected_resp is not None:
                break

        if selected_resp is None:
            print('Groq returned no usable content after model + API-key fallback')
            _debug_ai_log(f"FAIL {label_root}", 'all_attempts_exhausted')
            return None

        if DEBUG_AI_PIPELINE:
            usage = getattr(selected_resp, 'usage', None)
            if usage:
                _debug_ai_log(
                    f"USAGE {debug_label or 'groq_chat_create'}",
                    f"prompt_tokens={getattr(usage, 'prompt_tokens', None)} completion_tokens={getattr(usage, 'completion_tokens', None)} total_tokens={getattr(usage, 'total_tokens', None)}"
                )
            _debug_ai_log(f"RAW {debug_label or 'groq_chat_create'}", selected_content)

        return selected_resp

    # Offline reply used when Groq + model fallback cannot produce a response
    OFFLINE_REPLY = "Menti's server is currently offline. Please try again later."

# Initialize Firebase Admin SDK
try:
    cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-credentials.json')
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase initialized successfully")
    else:
        print("⚠️  Firebase credentials not found. Firestore storage disabled.")
        db = None
except Exception as e:
    print(f"⚠️  Firebase initialization error: {e}")
    db = None


# ==================== ROUTES ====================

# ==================== ADMIN ROUTES ====================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page and authentication"""
    if request.method == 'GET':
        if session.get('is_admin'):
            return redirect('/admin/dashboard')
        return render_template('admin_login.html')

    # POST - handle login
    data = request.get_json() if request.is_json else request.form
    username = data.get('username', '').strip()
    password = data.get('password', '')

    # Hash the submitted password and compare
    submitted_hash = hashlib.sha256(password.encode()).hexdigest()

    # Check primary env-var admin first
    if username.lower() == ADMIN_USERNAME.lower() and submitted_hash == ADMIN_PASSWORD_HASH:
        session['is_admin'] = True
        session['admin_username'] = username
        session.permanent = True
        print(f'✅ Admin login successful (primary): {username}')
        return jsonify({'success': True, 'redirect': '/admin/dashboard'})

    # Check additional admins stored in Firestore
    if db:
        try:
            docs = db.collection('admins').where('email', '==', username.lower()).limit(1).stream()
            for doc in docs:
                data = doc.to_dict()
                if data.get('password_hash') == submitted_hash:
                    session['is_admin'] = True
                    session['admin_username'] = username.lower()
                    session.permanent = True
                    print(f'✅ Admin login successful (Firestore): {username}')
                    return jsonify({'success': True, 'redirect': '/admin/dashboard'})
        except Exception as e:
            print(f'[admin] Firestore admin lookup error: {e}')

    print(f'❌ Admin login failed for username: {username}')
    return jsonify({'success': False, 'error': 'Invalid username or password'}), 401


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    """Admin logout"""
    session.pop('is_admin', None)
    session.pop('admin_username', None)
    return jsonify({'success': True})


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard page"""
    return render_template('admin_dashboard.html', admin_username=session.get('admin_username', 'Admin'))


# ==================== ADMIN HELPER ====================

def _fetch_all_emotion_logs():
    """Fetch all emotion_logs documents from Firestore into a list of dicts.
    Returns [] if db not available or on error."""
    if not db:
        return []
    try:
        return [doc.to_dict() for doc in db.collection('emotion_logs').stream()]
    except Exception as e:
        print(f'[admin] Error fetching emotion_logs: {e}')
        return []


def _fetch_all_conversations_meta():
    """Fetch all conversations (top-level only, no sub-collections)."""
    if not db:
        return []
    try:
        return [doc.to_dict() for doc in db.collection('conversations').stream()]
    except Exception as e:
        print(f'[admin] Error fetching conversations: {e}')
        return []


def _list_all_auth_users():
    """Return list of Firebase Auth UserRecord objects. Empty list on error."""
    users = []
    try:
        page = firebase_auth.list_users()
        while page:
            users.extend(page.users)
            page = page.get_next_page()
    except Exception as e:
        print(f'[admin] Error listing Firebase Auth users: {e}')
    return users


def _is_anonymous_auth_user(user):
    """True if Firebase Auth user is anonymous (no linked providers)."""
    return len(getattr(user, 'provider_data', [])) == 0


def _get_admin_emails():
    """Return a set of all admin emails (env-var admin + Firestore admins collection)."""
    emails = {ADMIN_USERNAME.lower()}
    if db:
        try:
            for doc in db.collection('admins').stream():
                data = doc.to_dict()
                if data.get('email'):
                    emails.add(data['email'].lower())
        except Exception as e:
            print(f'[admin] Error fetching admin list: {e}')
    return emails


# ==================== PRIVACY MASKING UTILITIES ====================

def mask_username(name):
    """Mask a username/display name for privacy.
    Examples: 'John Doe' -> 'J*** D***', 'Alice' -> 'A***'
    """
    if not name or not isinstance(name, str):
        return '***'
    
    parts = name.strip().split()
    if len(parts) == 1:
        # Single name: show first letter + asterisks
        first = parts[0]
        if len(first) > 1:
            return f"{first[0]}***"
        else:
            return "***"
    else:
        # Multiple parts: show first letter of each part + asterisks
        masked_parts = []
        for part in parts:
            if part:
                masked_parts.append(f"{part[0]}***")
        return " ".join(masked_parts) if masked_parts else "***"


def mask_email(email):
    """Mask an email address for privacy.
    Examples: 'john.doe@gmail.com' -> 'j***@gmail.com', 'user@domain.co.uk' -> 'u***@domain.co.uk'
    """
    if not email or not isinstance(email, str):
        return '***@***'
    
    email = email.strip().lower()
    if '@' not in email:
        # Not a valid email format, just mask it
        if len(email) > 1:
            return f"{email[0]}***"
        else:
            return "***"
    
    local, domain = email.split('@', 1)
    if not local:
        return f"***@{domain}"
    
    # Show first character of local part + asterisks + @ + domain
    return f"{local[0]}***@{domain}"


# ==================== ADMIN API ROUTES ====================

@app.route('/admin/api/stats')
@admin_required
def admin_api_stats():
    """Return overall system statistics — all filtering done in Python."""
    try:
        today_str = datetime.now().strftime('%Y-%m-%d')
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        # ---- 1. Firebase Auth user counts ----
        auth_users = _list_all_auth_users()
        registered_users = [u for u in auth_users if not _is_anonymous_auth_user(u)]
        stats = {
            'totalRegisteredUsers': 0,
            'totalAnonymousUsers': 0,
            'totalConversations': 0,
            'totalMessages': 0,
            'messagesToday': 0,
            'activeUsersToday': 0,
            'newUsersToday': 0,
            'emotionCounts': {'happy': 0, 'calm': 0, 'sad': 0, 'anxious': 0, 'stressed': 0, 'angry': 0, 'confused': 0, 'motivated': 0, 'tired': 0, 'numb': 0},
            'riskAlertCount': 0
        }

        # ---- 2. Populate stats from Auth (exclude all admin accounts) ----
        admin_emails = _get_admin_emails()
        registered_users = [u for u in registered_users if not (u.email and u.email.lower() in admin_emails)]
        new_today = sum(
            1 for u in registered_users
            if u.user_metadata.creation_timestamp and
               datetime.fromtimestamp(u.user_metadata.creation_timestamp / 1000).strftime('%Y-%m-%d') == today_str
        )
        stats['totalRegisteredUsers'] = len(registered_users)
        stats['newUsersToday'] = new_today

        # ---- 3. Conversation count + anonymous distinct users ----
        convs = _fetch_all_conversations_meta()
        stats['totalConversations'] = len(convs)
        # Count anonymous users from Firebase Auth directly (most accurate source)
        anon_user_ids = set(u.uid for u in auth_users if _is_anonymous_auth_user(u))
        # Also union in any anonymous users found only in conversations (edge cases)
        for c in convs:
            if c.get('isAnonymous') and c.get('userId'):
                anon_user_ids.add(c['userId'])
        stats['totalAnonymousUsers'] = len(anon_user_ids)

        # ---- 4. Emotion log stats (one pass, all in Python) ----
        logs = _fetch_all_emotion_logs()
        stats['totalMessages'] = len(logs)
        active_today_set = set()
        risk_user_neg = {}

        for log in logs:
            emotion = log.get('emotion', 'neutral')
            date = log.get('date', '')
            uid = log.get('userId', '')

            if emotion in stats['emotionCounts']:
                stats['emotionCounts'][emotion] += 1

            if date == today_str:
                stats['messagesToday'] += 1
                if uid:
                    active_today_set.add(uid)

            if emotion in ('sad', 'anxious', 'stressed', 'angry', 'tired', 'numb') and date >= seven_days_ago:
                risk_user_neg[uid] = risk_user_neg.get(uid, 0) + 1

        stats['activeUsersToday'] = len(active_today_set)
        stats['riskAlertCount'] = sum(1 for cnt in risk_user_neg.values() if cnt >= 5)

        return jsonify(stats)
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f'Error in admin_api_stats: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/users')
@admin_required
def admin_api_users():
    """Return list of all users.
    Query param ?type=all|registered|anonymous (default: all)
    Results are sorted newest-first by createdAt / first seen.
    """
    try:
        user_type = request.args.get('type', 'all').lower()  # all | registered | anonymous
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        # ---- Build per-user stats from emotion_logs (single pass) ----
        logs = _fetch_all_emotion_logs()
        user_log_stats = {}
        for log in logs:
            uid = log.get('userId', '')
            if not uid:
                continue
            if uid not in user_log_stats:
                user_log_stats[uid] = {
                    'totalMessages': 0, 'negativeRecent': 0,
                    'lastActive': '', 'isAnonymous': log.get('isAnonymous', False),
                    'firstSeen': log.get('date', ''),
                    'emotionCounts': {'happy': 0, 'calm': 0, 'sad': 0, 'anxious': 0, 'stressed': 0, 'angry': 0, 'confused': 0, 'motivated': 0, 'tired': 0, 'numb': 0}
                }
            user_log_stats[uid]['totalMessages'] += 1
            ts = log.get('timestamp', '')
            if ts > user_log_stats[uid]['lastActive']:
                user_log_stats[uid]['lastActive'] = ts
            date = log.get('date', '')
            if date and (not user_log_stats[uid]['firstSeen'] or date < user_log_stats[uid]['firstSeen']):
                user_log_stats[uid]['firstSeen'] = date
            emotion = log.get('emotion', 'neutral')
            if emotion in user_log_stats[uid]['emotionCounts']:
                user_log_stats[uid]['emotionCounts'][emotion] += 1
            if emotion in ('sad', 'anxious', 'stressed', 'angry', 'tired', 'numb') and date >= seven_days_ago:
                user_log_stats[uid]['negativeRecent'] += 1

        def risk(neg):
            return 'High' if neg >= 10 else 'Medium' if neg >= 5 else 'Low'

        users_list = []

        # ---- REGISTERED users (Firebase Auth with provider_data) ----
        if user_type in ('all', 'registered'):
            auth_users = _list_all_auth_users()
            admin_emails = _get_admin_emails()
            for user in auth_users:
                if _is_anonymous_auth_user(user):
                    continue  # skip anonymous auth entries here
                if user.email and user.email.lower() in admin_emails:
                    continue  # exclude all admin accounts from user lists
                uid = user.uid
                created_ms = user.user_metadata.creation_timestamp
                last_sign_in_ms = user.user_metadata.last_sign_in_timestamp
                ustats = user_log_stats.get(uid, {})
                neg = ustats.get('negativeRecent', 0)

                # Determine provider label
                providers = [p.provider_id for p in getattr(user, 'provider_data', [])]
                provider_label = 'Google' if 'google.com' in providers else 'Email' if 'password' in providers else 'Other'

                # Mask displayName and email for privacy
                display_name = user.display_name or user.email or 'No Name'
                user_email = user.email or 'N/A'
                
                users_list.append({
                    'uid': uid,
                    'displayName': mask_username(display_name),
                    'email': mask_email(user_email),
                    'type': 'Registered',
                    'provider': provider_label,
                    'isAnonymous': False,
                    'totalMessages': ustats.get('totalMessages', 0),
                    'lastActive': ustats.get('lastActive', '')[:19].replace('T', ' ') if ustats.get('lastActive') else 'Never',
                    'createdAt': datetime.fromtimestamp(created_ms / 1000).strftime('%Y-%m-%d %H:%M') if created_ms else 'N/A',
                    'createdAtTs': created_ms or 0,
                    'lastSignIn': datetime.fromtimestamp(last_sign_in_ms / 1000).strftime('%Y-%m-%d %H:%M') if last_sign_in_ms else 'Never',
                    'riskLevel': risk(neg),
                    'negativeRecent': neg,
                    'emotionCounts': ustats.get('emotionCounts', {})
                })

        # ---- ANONYMOUS users ----
        # Sources: Firebase Auth anonymous accounts + conversations collection + emotion_logs
        if user_type in ('all', 'anonymous'):
            # 1. Pull directly from Firebase Auth (signInAnonymously users)
            auth_users_all = _list_all_auth_users()
            anon_meta = {}  # uid -> ISO creation date string
            for user in auth_users_all:
                if not _is_anonymous_auth_user(user):
                    continue
                uid = user.uid
                created_ms = user.user_metadata.creation_timestamp
                created_str = datetime.fromtimestamp(created_ms / 1000).strftime('%Y-%m-%dT%H:%M:%S') if created_ms else ''
                anon_meta[uid] = created_str

            # 2. Also merge from conversations collection (may include users no longer in Auth)
            convs = _fetch_all_conversations_meta()
            for c in convs:
                if not c.get('isAnonymous'):
                    continue
                uid = c.get('userId', '')
                if not uid:
                    continue
                created = c.get('createdAt', '')
                if uid not in anon_meta or (created and created < anon_meta[uid]):
                    anon_meta[uid] = created

            # 3. Also pick up anonymous users who only have emotion_log entries
            for uid, s in user_log_stats.items():
                if s.get('isAnonymous') and uid not in anon_meta:
                    anon_meta[uid] = s.get('firstSeen', '')

            for uid, created_at in anon_meta.items():
                ustats = user_log_stats.get(uid, {})
                neg = ustats.get('negativeRecent', 0)
                short_uid = uid[:20] + ('...' if len(uid) > 20 else '')
                users_list.append({
                    'uid': uid,
                    'displayName': 'Anonymous User',
                    'email': short_uid,
                    'type': 'Anonymous',
                    'provider': 'Guest',
                    'isAnonymous': True,
                    'totalMessages': ustats.get('totalMessages', 0),
                    'lastActive': ustats.get('lastActive', '')[:19].replace('T', ' ') if ustats.get('lastActive') else 'Never',
                    'createdAt': created_at[:19].replace('T', ' ') if created_at else 'N/A',
                    'createdAtTs': 0,
                    'lastSignIn': ustats.get('lastActive', '')[:19].replace('T', ' ') if ustats.get('lastActive') else 'Never',
                    'riskLevel': risk(neg),
                    'negativeRecent': neg,
                    'emotionCounts': ustats.get('emotionCounts', {})
                })

        # Sort: registered by createdAtTs desc, anonymous by createdAt desc, then interleave newest-first
        users_list.sort(key=lambda u: (u.get('createdAt') or ''), reverse=True)

        return jsonify(users_list)
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f'Error in admin_api_users: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/emotions')
@admin_required
def admin_api_emotions():
    """Return emotion trends for the last 30 days — all filtering in Python."""
    try:
        today = datetime.now()
        date_labels = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(29, -1, -1)]
        thirty_days_ago = date_labels[0]

        emotion_keys = ['happy', 'calm', 'sad', 'anxious', 'stressed', 'angry', 'confused', 'motivated', 'tired', 'numb']
        # trends[emotion][date] = count
        trends = {e: {d: 0 for d in date_labels} for e in emotion_keys}

        logs = _fetch_all_emotion_logs()
        for log in logs:
            date = log.get('date', '')
            emotion = log.get('emotion', 'neutral')
            if date >= thirty_days_ago and date in trends.get(emotion, {}):
                trends[emotion][date] += 1

        datasets = {e: [trends[e][d] for d in date_labels] for e in emotion_keys}
        short_labels = [d[5:] for d in date_labels]  # MM-DD

        return jsonify({
            'labels': short_labels,
            'full_labels': date_labels,
            'datasets': datasets,
            'emotion_keys': emotion_keys
        })
    except Exception as e:
        print(f'Error in admin_api_emotions: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/risk-alerts')
@admin_required
def admin_api_risk_alerts():
    """Return at-risk users — 5+ negative messages in last 7 days."""
    try:
        from collections import Counter
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        # Aggregate from emotion_logs entirely in Python
        logs = _fetch_all_emotion_logs()
        user_risk = {}
        for log in logs:
            date = log.get('date', '')
            emotion = log.get('emotion', 'neutral')
            uid = log.get('userId', '')
            if not uid or emotion not in ('sad', 'anxious', 'stressed', 'angry', 'tired', 'numb') or date < seven_days_ago:
                continue
            if uid not in user_risk:
                user_risk[uid] = {
                    'count': 0, 'emotions': [], 'isAnonymous': log.get('isAnonymous', False),
                    'lastActive': '', 'conversationId': log.get('conversationId', ''),
                    'allDates': []
                }
            user_risk[uid]['count'] += 1
            user_risk[uid]['emotions'].append(emotion)
            user_risk[uid]['allDates'].append(date)
            ts = log.get('timestamp', '')
            if ts > user_risk[uid]['lastActive']:
                user_risk[uid]['lastActive'] = ts

        alerts = []
        reason_map = {
            'sad':       'Recurring sadness',
            'anxious':   'Persistent anxiety',
            'stressed':  'Chronic stress',
            'angry':     'Persistent anger/frustration',
            'tired':     'Chronic mental exhaustion',
            'numb':      'Emotional disconnection'
        }
        for uid, data in user_risk.items():
            if data['count'] < 5:
                continue
            top_emotion = Counter(data['emotions']).most_common(1)[0][0]
            risk_level = 'High' if data['count'] >= 10 else 'Medium'
            display_name = uid[:8] + '...'
            email = '—'
            if not data['isAnonymous']:
                try:
                    auth_user = firebase_auth.get_user(uid)
                    # Skip all admin accounts
                    admin_emails = _get_admin_emails()
                    if auth_user.email and auth_user.email.lower() in admin_emails:
                        continue
                    display_name = auth_user.display_name or auth_user.email or display_name
                    email = auth_user.email or '—'
                except Exception:
                    pass
            
            # Mask sensitive information for privacy
            masked_name = mask_username(display_name)
            masked_email = mask_email(email) if email != '—' else '—'
            initials = ''.join(w[0] for w in display_name.split()[:2]).upper() or '?'
            
            alerts.append({
                'uid': uid,
                'displayName': masked_name,
                'email': masked_email,
                'initials': initials,
                'riskLevel': risk_level,
                'reason': reason_map.get(top_emotion, 'Negative emotional pattern'),
                'negativeCount': data['count'],
                'dominantEmotion': top_emotion,
                'lastActive': data['lastActive'][:19].replace('T', ' ') if data['lastActive'] else 'Unknown',
                'isAnonymous': data['isAnonymous'],
                'daysAffected': len(set(data['allDates']))
            })

        alerts.sort(key=lambda x: x['negativeCount'], reverse=True)
        return jsonify(alerts)
    except Exception as e:
        print(f'Error in admin_api_risk_alerts: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/activity')
@admin_required
def admin_api_activity():
    """Return recent activity — latest 100 emotion log entries, newest first."""
    try:
        logs = _fetch_all_emotion_logs()
        # Sort by timestamp descending in Python
        logs.sort(key=lambda l: l.get('timestamp', ''), reverse=True)
        logs = logs[:100]

        activity = []
        for log in logs:
            uid = log.get('userId', '')
            ts = log.get('timestamp', '')
            activity.append({
                'userId': (uid[:14] + '...') if len(uid) > 14 else uid,
                'fullUserId': uid,
                'emotion': log.get('emotion', 'neutral'),
                'timestamp': ts[:19].replace('T', ' ') if ts else 'Unknown',
                'isAnonymous': log.get('isAnonymous', False),
                'conversationId': log.get('conversationId', ''),
                'date': log.get('date', '')
            })
        return jsonify(activity)
    except Exception as e:
        print(f'Error in admin_api_activity: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/user-details/<uid>')
@admin_required
def admin_api_user_details(uid):
    """Return comprehensive details for a specific user including:
    - User info (masked)
    - Emotional progress and distribution
    - Interaction frequency and trends
    - Recent activity
    """
    try:
        # ---- Fetch all emotion logs for this user ----
        all_logs = _fetch_all_emotion_logs()
        user_logs = [log for log in all_logs if log.get('userId') == uid]
        
        if not user_logs:
            return jsonify({'error': 'User not found'}), 404
        
        # ---- Basic user info ----
        user_info = {
            'uid': uid,
            'displayName': 'Loading...',
            'email': '—',
            'type': 'Anonymous',
            'isAnonymous': True,
            'createdAt': '—',
            'totalMessages': len(user_logs)
        }
        
        # Try to get user from Firebase Auth
        try:
            auth_user = firebase_auth.get_user(uid)
            admin_emails = _get_admin_emails()
            
            if not (auth_user.email and auth_user.email.lower() in admin_emails):
                user_info['displayName'] = mask_username(auth_user.display_name or auth_user.email or 'User')
                user_info['email'] = mask_email(auth_user.email or '—')
                user_info['type'] = 'Registered'
                user_info['isAnonymous'] = False
                
                if auth_user.user_metadata.creation_timestamp:
                    created = datetime.fromtimestamp(auth_user.user_metadata.creation_timestamp / 1000)
                    user_info['createdAt'] = created.strftime('%Y-%m-%d %H:%M')
        except Exception:
            pass
        
        # ---- Emotional Progress & Distribution ----
        emotion_counts = {'happy': 0, 'calm': 0, 'sad': 0, 'anxious': 0, 'stressed': 0, 
                         'angry': 0, 'confused': 0, 'motivated': 0, 'tired': 0, 'numb': 0}
        emotion_dates = {e: [] for e in emotion_counts.keys()}  # For trend
        daily_emotions = {}  # date -> {emotion: count}
        
        for log in user_logs:
            emotion = log.get('emotion', 'neutral')
            date = log.get('date', '')
            
            if emotion in emotion_counts:
                emotion_counts[emotion] += 1
                if date:
                    emotion_dates[emotion].append(date)
                    if date not in daily_emotions:
                        daily_emotions[date] = {}
                    daily_emotions[date][emotion] = daily_emotions[date].get(emotion, 0) + 1
        
        # ---- Interaction Frequency ----
        # Count messages by date
        messages_by_date = {}
        for log in user_logs:
            date = log.get('date', '')
            if date:
                messages_by_date[date] = messages_by_date.get(date, 0) + 1
        
        # Calculate weekly stats
        today = datetime.now()
        weekly_stats = {}
        for i in range(7):
            d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            weekly_stats[d] = messages_by_date.get(d, 0)
        
        messages_this_week = sum(weekly_stats.values())
        messages_last_week = 0
        for i in range(7, 14):
            d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            messages_last_week += messages_by_date.get(d, 0)
        
        # ---- Activity Trends ----
        # Calculate positive vs negative emotions
        positive_emotions = ['happy', 'calm', 'motivated']
        negative_emotions = ['sad', 'anxious', 'stressed', 'angry', 'tired', 'numb']
        
        positive_count = sum(emotion_counts[e] for e in positive_emotions if e in emotion_counts)
        negative_count = sum(emotion_counts[e] for e in negative_emotions if e in emotion_counts)
        
        # Last active
        last_active = max((log.get('timestamp', '') for log in user_logs), default='')
        last_active_date = last_active[:19].replace('T', ' ') if last_active else 'Never'
        
        # Recent entries
        recent_logs = sorted(user_logs, key=lambda x: x.get('timestamp', ''), reverse=True)[:10]
        recent_activity = []
        for log in recent_logs:
            recent_activity.append({
                'emotion': log.get('emotion', 'neutral'),
                'timestamp': log.get('timestamp', '')[:19].replace('T', ' '),
                'date': log.get('date', ''),
                'conversationId': log.get('conversationId', '')
            })
        
        return jsonify({
            'userInfo': user_info,
            'emotionalProgress': {
                'distribution': emotion_counts,
                'positive': positive_count,
                'negative': negative_count,
                'neutral': len(user_logs) - positive_count - negative_count,
                'positivityIndex': round((positive_count / len(user_logs) * 100) if user_logs else 0, 1)
            },
            'interactionFrequency': {
                'totalMessages': len(user_logs),
                'messagesThisWeek': messages_this_week,
                'messagesLastWeek': messages_last_week,
                'averagePerDay': round(len(user_logs) / max((datetime.fromisoformat(max(messages_by_date.keys())).toordinal() - 
                                       datetime.fromisoformat(min(messages_by_date.keys())).toordinal() + 1), 1), 2) if messages_by_date else 0,
                'weeklyStats': dict(sorted(weekly_stats.items())),
                'messagesByDate': dict(sorted(messages_by_date.items(), reverse=True)[:30])  # Last 30 days
            },
            'activityTrends': {
                'lastActive': last_active_date,
                'mostFrequentEmotion': max(emotion_counts, key=emotion_counts.get) if emotion_counts else 'neutral',
                'emotionTrends': emotion_counts,
                'dailyEmotionTrends': dict(sorted(daily_emotions.items(), reverse=True)[:30])  # Last 30 days
            },
            'recentActivity': recent_activity
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f'Error in admin_api_user_details: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/user-cache')
@admin_required
def admin_api_user_cache():
    """Return uid → {name, email, isAnonymous, initials} for all known users.
    Used client-side for display name resolution in activity, risk, and crisis tables.
    All names and emails are MASKED for privacy."""
    try:
        cache = {}
        auth_users_all = _list_all_auth_users()
        for user in auth_users_all:
            if _is_anonymous_auth_user(user):
                # Anonymous Firebase Auth user
                short_uid = user.uid[:16] + ('...' if len(user.uid) > 16 else '')
                cache[user.uid] = {
                    'name': 'Anonymous User',
                    'email': short_uid,
                    'isAnonymous': True,
                    'initials': '?'
                }
            else:
                # Registered user — exclude all admins
                if user.email and user.email.lower() in _get_admin_emails():
                    continue
                name = user.display_name or user.email or 'Unknown User'
                masked_name = mask_username(name)
                masked_email = mask_email(user.email or '—')
                # Extract initials from masked name
                initials = ''.join(w[0] for w in masked_name.split()[:2]).upper() or 'U'
                cache[user.uid] = {
                    'name': masked_name,
                    'email': masked_email,
                    'isAnonymous': False,
                    'initials': initials
                }
        # Also add anonymous users from conversations that may not be in Auth anymore
        convs = _fetch_all_conversations_meta()
        for c in convs:
            uid = c.get('userId', '')
            if uid and c.get('isAnonymous') and uid not in cache:
                short_uid = uid[:16] + ('...' if len(uid) > 16 else '')
                cache[uid] = {
                    'name': 'Anonymous User',
                    'email': short_uid,
                    'isAnonymous': True,
                    'initials': '?'
                }
        return jsonify(cache)
    except Exception as e:
        print(f'Error in admin_api_user_cache: {e}')
        return jsonify({})


@app.route('/admin/api/admins', methods=['GET'])
@admin_required
def admin_api_admins_list():
    """List all admin accounts (primary env admin + Firestore admins)."""
    admins = [{'email': ADMIN_USERNAME, 'isPrimary': True, 'createdAt': 'System', 'createdBy': '—'}]
    if db:
        try:
            for doc in db.collection('admins').stream():
                data = doc.to_dict()
                admins.append({
                    'id': doc.id,
                    'email': data.get('email', ''),
                    'isPrimary': False,
                    'createdAt': data.get('created_at', ''),
                    'createdBy': data.get('created_by', '—')
                })
        except Exception as e:
            print(f'[admin] Error listing admins: {e}')
    return jsonify(admins)


@app.route('/admin/api/admins', methods=['POST'])
@admin_required
def admin_api_admins_create():
    """Create a new admin account and store in Firestore admins collection."""
    if not db:
        return jsonify({'error': 'Firestore not available'}), 503
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    # Check if already an admin
    if email in _get_admin_emails():
        return jsonify({'error': 'An admin with this email already exists'}), 409
    try:
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        db.collection('admins').add({
            'email': email,
            'password_hash': password_hash,
            'created_at': datetime.now().isoformat(),
            'created_by': session.get('admin_username', 'unknown')
        })
        return jsonify({'success': True, 'email': email})
    except Exception as e:
        print(f'[admin] Error creating admin: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/admins/<doc_id>', methods=['DELETE'])
@admin_required
def admin_api_admins_delete(doc_id):
    """Delete an admin from the Firestore admins collection (cannot delete primary env admin)."""
    if not db:
        return jsonify({'error': 'Firestore not available'}), 503
    try:
        doc_ref = db.collection('admins').document(doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            return jsonify({'error': 'Admin not found'}), 404
        email = doc.to_dict().get('email', '')
        if email.lower() == ADMIN_USERNAME.lower():
            return jsonify({'error': 'Cannot delete the primary admin account'}), 403
        doc_ref.delete()
        return jsonify({'success': True})
    except Exception as e:
        print(f'[admin] Error deleting admin: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/messages-per-day')
@admin_required
def admin_api_messages_per_day():
    """Return message counts per day for last 14 days — single fetch, Python aggregation."""
    try:
        today = datetime.now()
        date_range = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(13, -1, -1)]
        counts_map = {d: 0 for d in date_range}
        fourteen_days_ago = date_range[0]

        logs = _fetch_all_emotion_logs()
        for log in logs:
            date = log.get('date', '')
            if date >= fourteen_days_ago and date in counts_map:
                counts_map[date] += 1

        labels = [(today - timedelta(days=i)).strftime('%b %d') for i in range(13, -1, -1)]
        counts = [counts_map[d] for d in date_range]
        return jsonify({'labels': labels, 'counts': counts})
    except Exception as e:
        print(f'Error in admin_api_messages_per_day: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/stream')
@admin_required
def admin_api_stream():
    """Server-Sent Events stream — uses Firebase Admin SDK (bypasses Firestore rules).
    Per-client Firestore on_snapshot listeners are created and torn down with the connection.
    """
    if not db:
        def no_db():
            yield 'event: error\ndata: {"msg": "Firestore not available"}\n\n'
        return Response(no_db(), mimetype='text/event-stream')

    client_q = queue.SimpleQueue()
    is_first = {'logs': True, 'convs': True, 'crisis': True}

    def _safe_dict(doc, change_type=None):
        d = doc.to_dict() or {}
        d['_docId'] = doc.id
        if change_type:
            d['_changeType'] = change_type
        # Make Firestore timestamps JSON-serialisable
        for k, v in list(d.items()):
            if hasattr(v, 'isoformat'):
                d[k] = v.isoformat()
        return d

    def on_logs(col_snapshot, changes, read_time):
        try:
            if is_first['logs']:
                is_first['logs'] = False
                batch = [_safe_dict(c.document) for c in changes]
                client_q.put_nowait(f"event: logs_init\ndata: {json.dumps(batch)}\n\n")
            else:
                for c in changes:
                    d = _safe_dict(c.document, c.type.name)
                    client_q.put_nowait(f"event: logs_change\ndata: {json.dumps(d)}\n\n")
        except Exception as ex:
            print(f'[SSE] on_logs error: {ex}')

    def on_convs(col_snapshot, changes, read_time):
        try:
            if is_first['convs']:
                is_first['convs'] = False
                batch = [_safe_dict(c.document) for c in changes]
                client_q.put_nowait(f"event: convs_init\ndata: {json.dumps(batch)}\n\n")
            else:
                for c in changes:
                    d = _safe_dict(c.document, c.type.name)
                    client_q.put_nowait(f"event: convs_change\ndata: {json.dumps(d)}\n\n")
        except Exception as ex:
            print(f'[SSE] on_convs error: {ex}')

    def on_crisis(col_snapshot, changes, read_time):
        try:
            if is_first['crisis']:
                is_first['crisis'] = False
                batch = [_safe_dict(c.document) for c in changes]
                client_q.put_nowait(f"event: crisis_init\ndata: {json.dumps(batch)}\n\n")
            else:
                for c in changes:
                    d = _safe_dict(c.document, c.type.name)
                    client_q.put_nowait(f"event: crisis_change\ndata: {json.dumps(d)}\n\n")
        except Exception as ex:
            print(f'[SSE] on_crisis error: {ex}')

    logs_watch   = db.collection('emotion_logs').on_snapshot(on_logs)
    convs_watch  = db.collection('conversations').on_snapshot(on_convs)
    crisis_watch = db.collection('crisis_alerts').on_snapshot(on_crisis)

    def generate():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    msg = client_q.get(timeout=25)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"  # keep connection alive
        finally:
            try: logs_watch.unsubscribe()
            except Exception: pass
            try: convs_watch.unsubscribe()
            except Exception: pass
            try: crisis_watch.unsubscribe()
            except Exception: pass

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


# ==================== ROUTES ====================

@app.route('/')
def index():
    """Render landing page"""
    return render_template('index.html')


@app.route('/login')
def login():
    """Render login page"""
    return render_template('login.html')


@app.route('/signup')
def signup():
    """Render signup page"""
    return render_template('signup.html')


@app.route('/chat-page')
def chat_page():
    """Render chatbot page"""
    return render_template('chat.html')


@app.route('/chat', methods=['POST'])
def chat():
    """
    Main chatbot endpoint
    - Receives user message, user ID, and guest mode status
    - Maintains conversation history
    - Detects emotion using OpenAI
    - Generates supportive response with context
    - Stores chat in Firestore ONLY for logged-in users (not guest mode)
    - Returns emotion and bot reply
    """
    try:
        data = request.json
        user_message = data.get('message', '').strip()
        user_id = data.get('user_id', 'anonymous')
        is_guest = data.get('is_guest', False)  # Check if user is in guest mode
        conversation_id = data.get('conversation_id')  # Get conversation ID for logged-in users
        save_only = data.get('save_only', False)  # Flag to only save without generating response
        mode = data.get('mode', 'friendly')  # Conversation style mode: friendly / supportive / professional
        response_length = data.get('response_length', 'short')  # short / detailed
        if response_length not in ('short', 'detailed'):
            response_length = 'short'
        
        # If save_only mode, just save the existing messages
        if save_only:
            bot_reply = data.get('bot_reply', '')
            emotion = data.get('emotion', 'neutral')
            
            if db and conversation_id:
                try:
                    store_chat_message(user_id, user_message, bot_reply, emotion, conversation_id, is_anonymous=is_guest)
                    print(f"✅ Chat retroactively saved to conversation: {conversation_id}")
                except Exception as e:
                    print(f"❌ Error storing chat: {e}")
            
            return jsonify({
                'success': True,
                'emotion': emotion,
                'reply': bot_reply
            })
        
        if not user_message:
            return jsonify({'error': 'Message is required'}), 400
        
        # Initialize conversation history for new users
        if user_id not in conversation_history:
            conversation_history[user_id] = []
            print(f"🆕 New conversation started for user: {user_id}")
        else:
            print(f"🔄 Continuing conversation for user: {user_id} (History length: {len(conversation_history[user_id])})")
        
        # Step 1: Add user message to history BEFORE generating response
        conversation_history[user_id].append({
            "role": "user",
            "content": user_message
        })
        
        # Step 2: Single-call analysis (context, follow-up, risk, emotion, masking)
        # Pass conversation_id to ensure only current conversation history is used
        analysis = analyze_message_context(user_id, user_message, conversation_id=conversation_id)
        analysis_ok = bool(analysis.get('analysis_ok', False))

        if analysis_ok:
            emotion = analysis.get('emotion', 'calm')
            is_masking = bool(analysis.get('is_masking', False))
            is_crisis = bool(analysis.get('is_high_risk', False))
            crisis_type = analysis.get('risk_type', '') if is_crisis else ''
            crisis_severity = analysis.get('risk_severity', 'MEDIUM') if is_crisis else ''

            print(f"😊 Detected emotion: {emotion}")
            if is_masking:
                print("🎭 Emotional masking flag raised — will probe gently")
            if is_crisis:
                print(f"🚨 CRISIS DETECTED: {crisis_type} [{crisis_severity}]")
                log_crisis_alert(user_id, user_message, crisis_type, crisis_severity, emotion, mode, is_anonymous=is_guest)

            # Step 3: Generate final reply using analysis result
            bot_reply = generate_supportive_response(
                user_message,
                emotion,
                user_id,
                is_masking=is_masking,
                mode=mode,
                is_crisis=is_crisis,
                crisis_type=crisis_type,
                response_length=response_length,
                analysis=analysis,
            )
        else:
            print('⚠️ Analysis unavailable after Groq/model fallback; returning offline reply')
            emotion = 'calm'
            bot_reply = OFFLINE_REPLY

        # If final generation also fails after API/model fallback, use offline reply.
        if not bot_reply:
            bot_reply = OFFLINE_REPLY
        
        # Step 4: Add bot response to history
        conversation_history[user_id].append({
            "role": "assistant",
            "content": bot_reply
        })
        print(f"💬 Bot reply generated. Total messages in history: {len(conversation_history[user_id])}")
        
        # Keep only last 12 messages (6 exchanges) to manage token usage
        if len(conversation_history[user_id]) > 12:
            conversation_history[user_id] = conversation_history[user_id][-12:]
            print(f"✂️ Trimmed conversation history to last 12 messages")
        
        # Step 5: Store chat in Firestore for BOTH guest and logged-in users
        # Guest data will be deleted on logout, logged-in data persists
        if db:
            try:
                store_chat_message(user_id, user_message, bot_reply, emotion, conversation_id, is_anonymous=is_guest)
                if is_guest:
                    print(f"💾 Guest chat stored temporarily (will be deleted on logout)")
                else:
                    print(f"✅ Chat stored for logged-in user: {user_id} in conversation: {conversation_id}")
            except Exception as e:
                print(f"❌ Error storing chat: {e}")
        
        # Step 6: Return response
        return jsonify({
            'emotion': emotion,
            'reply': bot_reply,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        print(f"Error in /chat endpoint: {e}")
        return jsonify({'error': 'Failed to process message'}), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'message': 'Menti chatbot is running'})


@app.route('/clear-history', methods=['POST'])
def clear_history():
    """
    Clear conversation history for a user
    For guest users: Also delete all their chats from Firestore
    For logged-in users: Only clear in-memory history (keep database records)
    """
    try:
        data = request.json
        user_id = data.get('user_id', 'anonymous')
        is_guest = data.get('is_guest', False)
        
        # Clear in-memory conversation history
        if user_id in conversation_history:
            conversation_history[user_id] = []
            print(f"🗑️ In-memory history cleared for user: {user_id}")
        
        # For guest users: DELETE all their chats from Firestore
        if is_guest and db:
            try:
                # Delete all conversations where userId = current user AND isAnonymous = true
                conversations_ref = db.collection('conversations')\
                    .where('userId', '==', user_id)\
                    .where('isAnonymous', '==', True)
                
                deleted_count = 0
                
                # Get all conversations for this guest
                for conv_doc in conversations_ref.stream():
                    conversation_id = conv_doc.id
                    
                    # Delete all messages in this conversation
                    messages_ref = db.collection('conversations')\
                        .document(conversation_id)\
                        .collection('messages')
                    
                    for msg_doc in messages_ref.stream():
                        msg_doc.reference.delete()
                    
                    # Delete the conversation document
                    conv_doc.reference.delete()
                    deleted_count += 1
                
                print(f"🗑️ Deleted {deleted_count} guest conversation(s) from Firestore for user: {user_id}")
                return jsonify({
                    'message': 'Guest conversation history cleared from memory and database',
                    'deleted_conversations': deleted_count
                })
            except Exception as e:
                print(f"❌ Error deleting guest conversations from Firestore: {e}")
                return jsonify({'message': 'History cleared from memory, but error deleting from database'}), 500
        
        return jsonify({'message': 'Conversation history cleared from memory'})
    
    except Exception as e:
        print(f"❌ Error clearing history: {e}")
        return jsonify({'error': 'Failed to clear history'}), 500


# ==================== HELPER FUNCTIONS ====================

def _fetch_conversation_messages_from_firestore(conversation_id, n=4):
    """
    Fetch the last n user and n bot messages from a specific conversation in Firestore.
    Returns: (user_msgs, assistant_msgs) - lists of message strings
    """
    user_msgs = []
    assistant_msgs = []
    
    if not db or not conversation_id:
        return user_msgs, assistant_msgs
    
    try:
        messages_ref = db.collection('conversations').document(conversation_id).collection('messages')
        # Fetch messages ordered by timestamp, most recent first
        docs = messages_ref.order_by('timestamp', direction='DESCENDING').limit(n * 2).stream()
        
        # Collect all messages and reverse to get chronological order
        all_messages = []
        for doc in docs:
            data = doc.to_dict()
            sender = data.get('sender', '').lower()
            message_text = data.get('message', '').strip()
            if message_text:
                all_messages.append({
                    'sender': sender,
                    'message': message_text
                })
        
        # Reverse to get chronological order (oldest first)
        all_messages.reverse()
        
        # Separate by sender and keep last n of each
        for msg in all_messages:
            if msg['sender'] == 'user':
                user_msgs.append(msg['message'])
            elif msg['sender'] == 'bot':
                assistant_msgs.append(msg['message'])
        
        # Keep only last n of each
        user_msgs = user_msgs[-n:]
        assistant_msgs = assistant_msgs[-n:]
        
    except Exception as e:
        print(f"⚠️ Error fetching conversation messages from Firestore: {e}")
    
    return user_msgs, assistant_msgs


def _last_n_role_messages(user_id, role, n=4):
    """Return last n messages for a role from in-memory conversation history.
    Fallback when conversation_id is not available.
    """
    history = conversation_history.get(user_id, []) or []
    msgs = []
    for turn in history:
        if (turn.get('role') or '').lower() == role:
            content = ' '.join((turn.get('content') or '').split())
            if content:
                msgs.append(content)
    return msgs[-n:]


def _format_context_for_analysis(conversation_id=None, user_id=None):
    """Build compact role-separated context (last 4 user + last 4 assistant).
    Priority: Use Firestore if conversation_id provided, fallback to in-memory history.
    """
    user_msgs = []
    assistant_msgs = []
    
    # Try to fetch from Firestore first (preferred for conversation-specific history)
    if conversation_id:
        user_msgs, assistant_msgs = _fetch_conversation_messages_from_firestore(conversation_id, n=4)
    
    # Fallback to in-memory history if no Firestore data or no conversation_id
    if not user_msgs and user_id:
        user_msgs = _last_n_role_messages(user_id, 'user', n=4)
    if not assistant_msgs and user_id:
        assistant_msgs = _last_n_role_messages(user_id, 'assistant', n=4)

    user_lines = [f"U{i+1}: {m}" for i, m in enumerate(user_msgs)] or ['U: (none)']
    assistant_lines = [f"M{i+1}: {m}" for i, m in enumerate(assistant_msgs)] or ['M: (none)']
    return '\n'.join([
        'RECENT_USER_MESSAGES:',
        *user_lines,
        'RECENT_MENTI_MESSAGES:',
        *assistant_lines,
    ])


def _extract_first_json_object(text):
    """Extract first JSON object from model output, tolerating extra text."""
    raw = (text or '').strip()
    if not raw:
        return None
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except Exception:
        return None


def _to_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value or '').strip().lower()
    return text in ('yes', 'true', '1', 'y')


def _normalize_risk_type(value):
    allowed = {'suicide', 'self_harm', 'homicide', 'medical', 'abuse', 'none'}
    text = str(value or '').strip().lower()
    return text if text in allowed else 'none'


def _normalize_emotion(value):
    valid = {'happy', 'calm', 'sad', 'anxious', 'stressed', 'angry', 'confused', 'motivated', 'tired', 'numb'}
    text = str(value or '').strip().lower()
    if text in valid:
        return text
    for label in valid:
        if label in text:
            return label
    return 'calm'


def _heuristic_emotion_only(message):
    """Local non-API fallback for emotion classification."""
    text = (message or '').lower().strip()
    heuristic_map = [
        (r'\b(joy|excited|grateful|happy|thrilled|great)\b', 'happy'),
        (r'\b(anxious|panic|nervous|worried|fear|scared)\b', 'anxious'),
        (r'\b(stress|stressed|overwhelmed|pressure|burnout)\b', 'stressed'),
        (r'\b(angry|mad|furious|rage|pissed|annoyed)\b', 'angry'),
        (r'\b(sad|cry|depressed|down|hopeless|empty|lonely|grief)\b', 'sad'),
        (r'\b(confused|lost|unsure|uncertain)\b', 'confused'),
        (r'\b(motivated|determined|driven|focused)\b', 'motivated'),
        (r'\b(tired|exhausted|drained|fatigue|sleepy)\b', 'tired'),
        (r'\b(numb|disconnected|blank|nothing)\b', 'numb'),
    ]
    for pat, emo in heuristic_map:
        if _re.search(pat, text):
            return emo
    return 'calm'


def _risk_from_regex_only(message):
    """Local non-API fallback for risk typing/severity."""
    text = (message or '').lower().strip()
    patterns = [
        ('suicide', 'HIGH', [
            r'\bkill myself\b', r'\bend my life\b', r'\bwant to die\b', r'\bwanna die\b',
            r'\bsuicide\b', r'\bsuicidal\b', r'\btake my (own )?life\b', r'\bdon\'?t want to live\b',
            r'\bno reason to live\b', r'\bbetter off dead\b', r'\bi want to end myself\b',
        ]),
        ('self_harm', 'HIGH', [
            r'\bcut(ting)? (myself|me)\b', r'\bhurt(ing)? (myself|me)\b', r'\bself.?harm\b',
            r'\bburning? (myself|my skin)\b',
        ]),
        ('homicide', 'HIGH', [
            r'\bkill (someone|them|him|her|people)\b', r'\bmurder\b', r'\bgoing to (hurt|attack|stab|shoot)\b',
        ]),
        ('medical', 'MEDIUM', [
            r'\bcan\'?t breathe\b', r'\bheart attack\b', r'\bchest (pain|tightness|hurts?)\b', r'\boverdos(e|ing)\b',
        ]),
        ('abuse', 'MEDIUM', [
            r'\b(being|getting) (abused|beaten|hit|assaulted|raped|molested)\b', r'\bdomestic (violence|abuse)\b', r'\bbully|bullying|bullied\b',
        ]),
    ]
    for rtype, sev, pats in patterns:
        for pat in pats:
            if _re.search(pat, text):
                return True, rtype, sev
    return False, 'none', 'LOW'


def analyze_message_context(user_id, last_user_message, conversation_id=None):
    """Part 1: Single Groq prompt to analyze context, follow-up need, risk, emotion, and masking.
    Uses conversation_id to fetch conversation-specific history from Firestore.
    """
    context_block = _format_context_for_analysis(conversation_id=conversation_id, user_id=user_id)

    result = {
        'analysis_ok': False,
        'context_summary': 'Recent chat context captured from last 4 user and 4 Menti messages.',
        'last_message': last_user_message,
        'follow_up_needed': False,
        'follow_up_reason': '',
        'follow_up_question': '',
        'is_high_risk': False,
        'risk_type': 'none',
        'risk_severity': 'LOW',
        'risk_reason': '',
        'emotion': 'calm',
        'emotion_reason': '',
        'is_masking': False,
        'masking_reason': '',
    }

    try:
        prompt = (
            "Analyze LAST_USER_MESSAGE using RECENT_USER_MESSAGES and RECENT_MENTI_MESSAGES. "
            "Return JSON only with keys exactly: "
            "context_summary, follow_up_needed, follow_up_reason, follow_up_question, "
            "is_high_risk, risk_type, risk_severity, risk_reason, emotion, emotion_reason, is_masking, masking_reason. "
            "Rules: follow_up_needed/is_high_risk/is_masking are booleans. "
            "risk_type one of suicide,self_harm,homicide,medical,abuse,none. "
            "risk_severity one of HIGH,MEDIUM,LOW. "
            "emotion one of happy,calm,sad,anxious,stressed,angry,confused,motivated,tired,numb. "
            "Keep reasons short (<=12 words). follow_up_question empty string if not needed."
            f"\n\n{context_block}\nLAST_USER_MESSAGE:\n{last_user_message}"
        )

        response = groq_chat_create(
            model=groq_model,
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'You are a strict JSON analyzer for a mental-health companion. '
                        'Do not write prose outside JSON. '
                    ),
                },
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=4096,  # High ceiling; word count instructions in prompt control output
            temperature=0.0,
            _debug_label='part1_analysis',
        )
        if response is None:
            _debug_ai_log('PART1 fail', 'no_response_after_model_and_api_fallback')
            return result

        raw = getattr(response.choices[0].message, 'content', '') or ''
        _debug_ai_log('PART1 raw', raw)
        parsed = _extract_first_json_object(raw)
        if not isinstance(parsed, dict):
            _debug_ai_log('PART1 fail', 'invalid_json_from_model')
            return result

        risk_type = _normalize_risk_type(parsed.get('risk_type'))
        is_high_risk = _to_bool(parsed.get('is_high_risk')) and risk_type != 'none'
        severity = str(parsed.get('risk_severity', '') or '').strip().upper()
        if severity not in ('HIGH', 'MEDIUM', 'LOW'):
            severity = 'HIGH' if risk_type in ('suicide', 'self_harm', 'homicide') else ('MEDIUM' if risk_type in ('medical', 'abuse') else 'LOW')

        result.update({
            'analysis_ok': True,
            'context_summary': str(parsed.get('context_summary', result['context_summary']))[:300],
            'follow_up_needed': _to_bool(parsed.get('follow_up_needed')),
            'follow_up_reason': str(parsed.get('follow_up_reason', '') or '')[:120],
            'follow_up_question': str(parsed.get('follow_up_question', '') or '')[:220],
            'is_high_risk': is_high_risk,
            'risk_type': risk_type,
            'risk_severity': severity,
            'risk_reason': str(parsed.get('risk_reason', '') or '')[:120],
            'emotion': _normalize_emotion(parsed.get('emotion')),
            'emotion_reason': str(parsed.get('emotion_reason', '') or '')[:120],
            'is_masking': _to_bool(parsed.get('is_masking')),
            'masking_reason': str(parsed.get('masking_reason', '') or '')[:120],
        })

        if not result['follow_up_needed']:
            result['follow_up_question'] = ''
    except Exception as e:
        print(f"Analysis pipeline error: {e}")
        _debug_ai_log('PART1 fail', f'exception={e}')

    return result

def detect_emotion(message):
    """
    Detect emotion from user message using Groq.
    Returns one of 10 emotion categories:
    happy, calm, sad, anxious, stressed, angry, confused, motivated, tired, numb
    """
    try:
        text = (message or '').lower().strip()
        heuristic_map = [
            (r'\b(joy|excited|grateful|happy|thrilled|great)\b', 'happy'),
            (r'\b(anxious|panic|nervous|worried|fear|scared)\b', 'anxious'),
            (r'\b(stress|stressed|overwhelmed|pressure|burnout)\b', 'stressed'),
            (r'\b(angry|mad|furious|rage|pissed|annoyed)\b', 'angry'),
            (r'\b(sad|cry|depressed|down|hopeless|empty|lonely|grief)\b', 'sad'),
            (r'\b(confused|lost|unsure|uncertain)\b', 'confused'),
            (r'\b(motivated|determined|driven|focused)\b', 'motivated'),
            (r'\b(tired|exhausted|drained|fatigue|sleepy)\b', 'tired'),
            (r'\b(numb|disconnected|blank|nothing)\b', 'numb'),
        ]
        heuristic_hit = next((emo for pat, emo in heuristic_map if _re.search(pat, text)), None)

        response = groq_chat_create(
            model=groq_model,
            messages=[
                {
                    "role": "system",
                    "content": """You are an emotion classifier for mental-health text.
Return EXACTLY one label from:
happy, calm, sad, anxious, stressed, angry, confused, motivated, tired, numb
Rules:
- pick strongest current emotion,
- prefer sad/anxious/stressed/angry when distress is explicit,
- use calm only when message is neutral/stable,
- output one label only."""
                },
                {
                    "role": "user",
                    "content": message
                }
            ],
            max_tokens=20,
            temperature=0.0,
        )
        if response is None:
            print('Emotion detection skipped: no usable Groq response')
            return heuristic_hit or 'calm'

        emotion = (getattr(response.choices[0].message, 'content', '') or '').strip().lower()
        
        # Validate emotion — must be exactly one of the 10
        valid_emotions = ['happy', 'calm', 'sad', 'anxious', 'stressed', 'angry', 'confused', 'motivated', 'tired', 'numb']
        if emotion not in valid_emotions:
            # Try partial match for minor model variance (e.g. 'calmness' -> 'calm')
            matched = next((e for e in valid_emotions if e in emotion), None)
            emotion = matched if matched else (heuristic_hit or 'calm')
        
        return emotion
    
    except Exception as e:
        print(f"Error detecting emotion: {e}")
        return 'calm'


# ==================== EMOTIONAL MASKING DETECTION ====================

import re as _re

# Common emotional masking/avoidance patterns (fast pre-screen, no API call)
_MASKING_PATTERNS = [
    r"\bi'?m fine\b", r"\bi am fine\b",
    r"\bit'?s okay\b", r"\bit is okay\b", r"\bit'?s ok\b",
    r"\bdon'?t worry about me\b", r"\bdon'?t worry\b",
    r"\bi'?m okay\b", r"\bi am okay\b",
    r"\bi'?m alright\b", r"\bi am alright\b",
    r"\bno worries\b", r"\bi'?m good\b", r"\bi am good\b",
    r"\bdon'?t mind me\b", r"\bforget it\b", r"\bnever mind\b",
    r"\bwhatever\b", r"\bi'?ll be fine\b", r"\bi'?ll be okay\b",
    r"\bi'?ll manage\b", r"\bit doesn'?t matter\b",
    r"\bit'?s nothing\b", r"\bno big deal\b",
    r"\bnothing'?s wrong\b", r"\bi'?m just tired\b",
    r"\bjust ignore me\b", r"\bi'?m used to it\b",
    r"\bi can handle it\b", r"\bi'?ll be alright\b",
    r"\bdoesn'?t matter\b", r"\bnot a big deal\b",
    r"\bsame as always\b", r"\bsame old\b",
]

# Default ON for better safety/intelligence. Can be disabled via env when needed.
ENABLE_AI_MASKING_CHECK = os.getenv('ENABLE_AI_MASKING_CHECK', 'true').lower() == 'true'
ENABLE_AI_CRISIS_CHECK = os.getenv('ENABLE_AI_CRISIS_CHECK', 'true').lower() == 'true'

# ==================== CRISIS HOTLINES - PHILIPPINES ====================
# National & Local Crisis Support Services for Morong, Rizal
PHILIPPINES_CRISIS_HOTLINES = {
    'national': {
        'hopeline': {
            'name': 'Hopeline PH',
            'number': '(02) 8804-4673 or Text HOPE to 2929',
            'type': 'Mental Health Crisis Support',
            'availability': '24/7',
        },
        'ncmh': {
            'name': 'National Center for Mental Health (NCMH)',
            'number': '(02) 8928-6666',
            'type': 'Psychiatric Emergency',
            'availability': '24/7',
        },
        'emergency': {
            'name': 'National Emergency Response',
            'number': '911',
            'type': 'Police & Emergency Services',
            'availability': '24/7',
        },
        'red_cross': {
            'name': 'Philippine Red Cross',
            'number': '143 or (02) 8527-8001',
            'type': 'Emergency Medical Services',
            'availability': '24/7',
        },
        'pnp_wcpc': {
            'name': 'PNP Women & Children Protection Center',
            'number': '1388 or (02) 8532-8378',
            'type': 'Abuse & Harassment Support',
            'availability': '24/7',
        },
    },
    'morong_rizal': {
        'police': {
            'name': 'Morong Police Station',
            'number': 'Local dial 117 or (02) 1234-5678',
            'type': 'Local Law Enforcement',
            'availability': '24/7',
        },
        'health_center': {
            'name': 'Morong Municipal Health Center',
            'number': 'Emergency response available 24/7',
            'type': 'Local Health Services',
            'availability': '24/7',
        },
    },
}

def _format_crisis_hotlines(response_length='short', crisis_type='none'):
    """
    Format crisis hotline information based on response length.
    Short: Brief mention of 1-2 key hotlines
    Detailed: Comprehensive crisis resources with all hotlines
    """
    if response_length == 'short':
        # For short responses: 1-2 key hotlines
        lines = []
        lines.append('🆘 Immediate Help Available:')
        lines.append(f"  • Hopeline PH: (02) 8804-4673 or Text HOPE to 2929")
        lines.append(f"  • Emergency: 911")
        return '\n'.join(lines)
    else:
        # For detailed responses: comprehensive crisis resources
        lines = []
        lines.append('🆘 CRISIS SUPPORT AVAILABLE - PLEASE REACH OUT NOW:')
        lines.append('')
        lines.append('National Crisis Hotlines:')
        for key, hotline in PHILIPPINES_CRISIS_HOTLINES['national'].items():
            lines.append(f"  • {hotline['name']}: {hotline['number']}")
            lines.append(f"    ({hotline['type']})")
        lines.append('')
        lines.append('Local Morong, Rizal Services:')
        for key, hotline in PHILIPPINES_CRISIS_HOTLINES['morong_rizal'].items():
            lines.append(f"  • {hotline['name']}: {hotline['number']}")
        lines.append('')
        lines.append('You are not alone. Professional help is available right now.')
        return '\n'.join(lines)


def detect_emotional_masking(message):
    """
    Detect if a user is emotionally masking or avoiding their true feelings.
    Phase 1: Fast regex pre-screen for common dismissive/avoidant phrases.
    Phase 2: AI check for subtle masking in short messages.
    Returns True if emotional masking/avoidance is detected.
    """
    msg_lower = message.lower().strip()

    # Phase 1 — fast regex check (no API cost)
    for pattern in _MASKING_PATTERNS:
        if _re.search(pattern, msg_lower):
            print(f"🎭 Emotional masking detected via pattern: '{pattern}'")
            return True

    # Phase 2 — AI check for subtle masking cues (primary path)
    if ENABLE_AI_MASKING_CHECK:
        try:
            response = groq_chat_create(
                model=groq_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You detect emotional masking in mental-health text. "
                            "Return YES if the user appears to downplay, deflect, or hide distress behind minimizing language, "
                            "even when not explicit (e.g., forced positivity, avoidance, dismissive phrasing). "
                            "Return NO otherwise. Output only YES or NO."
                        )
                    },
                    {
                        "role": "user",
                        "content": message
                    }
                ],
                max_tokens=16,
                temperature=0.0,
            )
            if response is None:
                return False
            raw = getattr(response.choices[0].message, 'content', '') or ''
            is_masking = raw.strip().upper().startswith('YES')
            if is_masking:
                print(f"🎭 Emotional masking detected via AI analysis")
            return is_masking
        except Exception as e:
            print(f"Masking detection error: {e}")

    return False


# ==================== CRISIS DETECTION ====================

def detect_crisis(message):
    """
    Detect crisis indicators in a user message.
    Phase 1: Fast regex check.
    Phase 2: AI confirmation for subtle/ambiguous cases.
    Returns: (is_crisis: bool, crisis_type: str, severity: str)
      crisis_type: 'suicide' | 'self_harm' | 'homicide' | 'medical' | 'abuse'
      severity:    'HIGH' | 'MEDIUM'
    """
    text = message.lower().strip()

    # --- Phase 1: Regex safety net patterns ---
    suicide_patterns = [
        r'\bkill myself\b', r'\bend my life\b', r'\bwant to die\b', r'\bwanna die\b',
        r'\bsuicide\b', r'\bsuicidal\b', r'\btake my (own )?life\b', r'\bnot want to (be here|live|exist)\b',
        r'\bdon\'?t want to live\b', r'\bno reason to live\b', r'\bbetter off dead\b',
        r'\bthinking of (ending|killing)\b', r'\bplan to kill\b', r"\bi('m| am) going to kill myself\b",
        r'\bend myself\b', r'\bi want to end myself\b', r'\bi am about to die\b', r'\bi think i am about to die\b',
    ]
    self_harm_patterns = [
        r'\bcut(ting)? (myself|me)\b', r'\bhurt(ing)? (myself|me)\b', r'\bself.?harm\b',
        r'\bburning? (myself|my skin)\b', r'\bscratch(ing)? (myself|my skin)\b',
        r'\bblood(ing)?\b.{0,30}\bmyself\b', r'\bpunch(ing)? (myself|a wall|the wall)\b',
        r'\bbruise(s|d)?\b.{0,20}\b(me|myself)\b', r'\binjur(y|ies)\b.{0,20}\b(me|myself)\b',
    ]
    homicide_patterns = [
        r'\bkill (someone|them|him|her|people)\b', r'\bmurder\b', r'\bwant to hurt (someone|them|him|her)\b',
        r'\bgoing to (hurt|attack|stab|shoot)\b', r'\bhomicid\b',
        r'\bmake (them|someone|people) disappear\b', r'\bmake others disappear\b',
    ]
    medical_patterns = [
        r'\bcan\'?t breathe\b', r'\bcan not breathe\b', r'\bpanic attack\b',
        r'\bheart attack\b', r'\bchest (pain|tightness|hurts?)\b', r'\bpassing out\b',
        r'\bfainting\b', r'\boverdos(e|ing)\b', r'\bseizure\b',
    ]
    abuse_patterns = [
        r'\b(being|getting) (abused|beaten|hit|assaulted|raped|molested)\b',
        r'\b(someone|he|she|they) (hurt|hits|beats|abuses) me\b',
        r'\bdomestic (violence|abuse)\b', r'\bsexual(ly)? (abuse|assault)\b',
        r'\b(bully|bullying|bullied)\b', r'\b(punch|kick|bruise|injur(y|ies))\b',
    ]

    # --- Phase 2: AI-first crisis determination ---
    word_count = len(text.split())
    if ENABLE_AI_CRISIS_CHECK and groq_client and word_count < 120:
        try:
            resp = groq_chat_create(
                model=groq_model,
                messages=[
                    {"role": "system", "content": (
                        "You are a crisis triage classifier for mental-health conversations. "
                        "Assess explicit and strong implied risk. "
                        "Return exactly one label only: SUICIDE|SELF_HARM|HOMICIDE|MEDICAL|ABUSE|NONE. "
                        "Use SUICIDE for death wish/end-life intent; SELF_HARM for self-injury urges; "
                        "HOMICIDE for intent to seriously harm others; MEDICAL for urgent physical danger/medical distress; "
                        "ABUSE for violence/bullying/ongoing assault situations."
                    )},
                    {"role": "user", "content": message}
                ],
                max_tokens=20,
                temperature=0.0,
            )
            if resp is None:
                # Groq unavailable or returned non-chat output; skip AI crisis check
                print('Crisis AI check skipped: no usable Groq response')
            else:
                raw = getattr(resp.choices[0].message, 'content', '') or ''
                tokens = raw.strip().upper().split()
                if not tokens:
                    print('Crisis AI returned empty content; skipping')
                else:
                    answer = tokens[0]
                    type_map = {
                        'SUICIDE':   ('suicide',   'HIGH'),
                        'SELF_HARM': ('self_harm', 'HIGH'),
                        'HOMICIDE':  ('homicide',  'HIGH'),
                        'MEDICAL':   ('medical',   'MEDIUM'),
                        'ABUSE':     ('abuse',     'MEDIUM'),
                    }
                    if answer in type_map:
                        crisis_type, severity = type_map[answer]
                        print(f"🚨 Crisis detected via AI: {crisis_type} [{severity}]")
                        return True, crisis_type, severity
        except Exception as e:
            print(f"Crisis AI detection error: {e}")

    # --- Phase 3: Regex safety net fallback ---
    for pat in suicide_patterns:
        if _re.search(pat, text):
            return True, 'suicide', 'HIGH'
    for pat in self_harm_patterns:
        if _re.search(pat, text):
            return True, 'self_harm', 'HIGH'
    for pat in homicide_patterns:
        if _re.search(pat, text):
            return True, 'homicide', 'HIGH'
    for pat in medical_patterns:
        if _re.search(pat, text):
            return True, 'medical', 'MEDIUM'
    for pat in abuse_patterns:
        if _re.search(pat, text):
            return True, 'abuse', 'MEDIUM'

    return False, '', ''


def log_crisis_alert(user_id, message, crisis_type, severity, emotion, mode, is_anonymous=False):
    """Write a crisis alert to Firestore crisis_alerts collection."""
    if not db:
        return
    try:
        now = datetime.now()
        # Store a truncated message snippet (max 200 chars) for privacy
        snippet = message[:200] + ('...' if len(message) > 200 else '')
        db.collection('crisis_alerts').document().set({
            'userId':      user_id,
            'isAnonymous': is_anonymous,
            'crisisType':  crisis_type,
            'severity':    severity,
            'emotion':     emotion,
            'mode':        mode,
            'messageSnippet': snippet,
            'timestamp':   now.isoformat(),
            'date':        now.strftime('%Y-%m-%d'),
            'reviewed':    False,
        })
        print(f"🚨 Crisis alert logged: {crisis_type} [{severity}] for user {user_id}")
    except Exception as e:
        print(f"Error logging crisis alert: {e}")


def summarize_conversation_history(user_id, keep_last=6):
    """Create a compact summary of earlier turns and keep recent turns verbatim."""
    history = conversation_history.get(user_id, []) or []
    if len(history) <= keep_last:
        return '', history

    older = history[:-keep_last]
    recent = history[-keep_last:]

    user_points = []
    assistant_points = []
    for turn in older:
        role = (turn.get('role') or '').lower()
        content = ' '.join((turn.get('content') or '').split())
        if not content:
            continue
        # Trim each point aggressively for token efficiency.
        clipped = (content[:120] + '...') if len(content) > 120 else content
        if role == 'user':
            user_points.append(clipped)
        elif role == 'assistant':
            assistant_points.append(clipped)

    chunks = []
    if user_points:
        chunks.append('User previously shared: ' + ' | '.join(user_points[-4:]))
    if assistant_points:
        chunks.append('Menti previously responded: ' + ' | '.join(assistant_points[-3:]))

    return (' '.join(chunks)).strip(), recent


def generate_supportive_response(
    message,
    emotion,
    user_id,
    is_masking=False,
    mode='friendly',
    is_crisis=False,
    crisis_type=None,
    response_length='short',
    analysis=None,
):
    """Part 2: Generate final user-facing reply from analysis + mode/length rules.
    For HIGH RISK/CRISIS cases:
      - Always prioritize emotional support, coping strategies, and encouragement to seek help
      - Include Philippines crisis hotlines (Morong, Rizal & national)
      - For SHORT responses: Brief hotline mention
      - For DETAILED responses: Focus ENTIRELY on crisis resources, no follow-up questions
    """
    try:
        mode = mode if mode in ('friendly', 'supportive', 'professional') else 'friendly'
        response_length = response_length if response_length in ('short', 'detailed') else 'short'
        analysis = analysis or {}
        
        # Extract analysis - PART 1 data
        follow_up_needed = bool(analysis.get('follow_up_needed', False))
        follow_up_question = (analysis.get('follow_up_question') or '').strip()
        risk_type = analysis.get('risk_type', crisis_type or 'none')
        risk_reason = analysis.get('risk_reason', '')
        risk_severity = analysis.get('risk_severity', 'LOW')
        emotion_reason = analysis.get('emotion_reason', '')
        masking_reason = analysis.get('masking_reason', '')
        context_summary = analysis.get('context_summary', '')
        
        # Determine if high risk (from Part 1 analysis)
        is_high_risk = bool(analysis.get('is_high_risk', False)) or is_crisis
        
        mode_rules = {
            'friendly': 'Warm, human, comforting, everyday words, emotionally present.',
            'supportive': 'Encouraging and grounding, reinforce strengths and realistic action.',
            'professional': 'Calm, structured, counselor-like, precise and compassionate language.',
        }
        length_rules = {
            'short': 'Output exactly 1 complete sentence (max 25 words).',
            'detailed': 'Output 2 to 3 complete sentences (max 85 words total).',
        }

        # Enhanced safety rule for crisis cases
        if is_high_risk:
            if response_length == 'short':
                safety_rule = (
                    'CRITICAL RESPONSE: Prioritize emotional support and comfort. '
                    'Validate pain deeply. Suggest one coping strategy. '
                    'MUST include Hopeline PH: (02) 8804-4673 or Emergency 911. '
                    'Encourage reaching out to trusted individuals (family, friends, teachers, pastors). '
                    'Do NOT ask follow-up questions.'
                )
            else:  # detailed
                safety_rule = (
                    'CRITICAL RESPONSE: Focus ENTIRELY on crisis support and resources. '
                    'Express deep empathy and validation of pain. '
                    'MUST include these hotlines: Hopeline PH (02) 8804-4673, NCMH (02) 8928-6666, Emergency 911. '
                    'Include Morong local resources when relevant. '
                    'Encourage immediate professional help and reaching trusted individuals. '
                    'NO follow-up questions - only crisis support and encouragement.'
                )
        else:
            safety_rule = (
                'Provide empathy first. Suggest one practical coping strategy. Encourage reaching out to trusted people (family, friends, teachers, pastors, trusted mentors). '
                'ONLY ask a follow-up question if context is genuinely unclear or emotion/reason cannot be determined. '
                'Prioritize support over clarification questions.'
            )

        # Determine if follow-up is truly needed based on analysis clarity
        # Follow-up IS needed if context/situation is unclear for good support
        
        # Questions that ask for situation clarification (not coping strategies or safety assessment)
        question_clarifies_situation = (
            follow_up_question 
            and any(phrase in follow_up_question.lower() for phrase in [
                "what's", "what is", "can you tell", "can you share", "tell me about", "tell me more", "could you share"
            ])
        )
        
        # emotion_reason is generic if it starts with "User" (wrapper) + generic verb
        emotion_reason_is_generic = (
            emotion_reason 
            and emotion_reason.lower().startswith('user')
            and any(word in emotion_reason.lower() for word in ['indicate', 'says', 'expresses', 'mentions'])
        )
        emotion_is_unclear = emotion == 'calm' or not emotion_reason
        
        needs_clarification = (
            follow_up_needed 
            and follow_up_question 
            and (question_clarifies_situation or emotion_reason_is_generic or emotion_is_unclear)
        )
        
        # For crisis cases: NEVER ask follow-up questions
        if is_high_risk:
            follow_up_rule = (
                'DO NOT ask follow-up questions. Focus ONLY on crisis support, empathy, and crisis hotlines/resources. '
                'Include emergency contact information prominently.'
            )
            follow_up_question = ''  # Remove question to prevent temptation
        # For non-crisis: ONLY ask follow-up if truly needed for clarity
        elif needs_clarification:
            follow_up_rule = (
                f"Context needs clarification to provide best support. Center the reply on this question: {follow_up_question}"
            )
        else:
            # Context is clear enough - provide support WITHOUT follow-up
            follow_up_rule = (
                'Context is clear and emotions/reasons are understood. '
                'Provide empathetic support with ONE practical coping strategy. '
                'ABSOLUTELY NO QUESTIONS. Not a single question mark. Only supportive statement.'
            )
            follow_up_question = ''  # Remove question to prevent temptation

        # Build crisis hotlines block if high risk
        crisis_hotlines_block = ''
        if is_high_risk:
            crisis_hotlines_block = '\n\n' + _format_crisis_hotlines(response_length, risk_type)
        
        system_prompt = (
            'You are Menti, a mental-health companion focused on comfort, safety, and practical support. '
            f'Mode rule: {mode_rules[mode]} '
            f'Length rule: {length_rules[response_length]} '
            f'{safety_rule} '
            'Short responses: EXACTLY 1 sentence, around 25 words only.'
            'Detailed responses: 2-3 sentences, around 85 words only.'
            'Do not use bullet points. Do not output labels or JSON. Output only the final reply to the user.'
            f'{crisis_hotlines_block}'
        )

        user_prompt = (
            f"CONTEXT_SUMMARY: {context_summary}\n"
            f"LAST_USER_MESSAGE: {message}\n"
            f"ANALYSIS: high_risk={str(is_high_risk).lower()} risk_type={risk_type} risk_severity={risk_severity} risk_reason={risk_reason}\n"
            f"ANALYSIS: emotion={emotion} emotion_reason={emotion_reason}\n"
            f"ANALYSIS: masking={str(is_masking).lower()} masking_reason={masking_reason}\n"
            f"ANALYSIS: follow_up_needed={str(follow_up_needed).lower()} follow_up_question={follow_up_question}\n"
            f"CLARITY: context_clear={bool(emotion_reason and risk_reason)} needs_clarification={needs_clarification}\n"
            f"INSTRUCTION: {follow_up_rule}"
        )

        response = groq_chat_create(
            model=groq_model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=4096,  # High ceiling; word count constraints in system_prompt control output
            temperature=0.6,
            _debug_label='part2_reply',
        )
        bot_reply = (getattr(response.choices[0].message, 'content', '') or '').strip() if response is not None else ''
        if not bot_reply:
            _debug_ai_log('PART2 warn', 'primary_part2_empty_trying_retry_prompt')
            retry_system = system_prompt + ' Keep the reply direct, complete, and natural.'
            retry_user = (
                f"User message: {message}\n"
                f"Context summary: {context_summary}\n"
                f"Flags: high_risk={str(is_high_risk).lower()}, emotion={emotion}, masking={str(is_masking).lower()}, follow_up_needed={str(follow_up_needed).lower()}\n"
                f"If follow_up_needed=true and follow_up_question exists, ask exactly that question: {follow_up_question}"
                f'{crisis_hotlines_block}'
            )
            retry = groq_chat_create(
                model=groq_model,
                messages=[
                    {'role': 'system', 'content': retry_system},
                    {'role': 'user', 'content': retry_user},
                ],
                max_tokens=4096,  # High ceiling; word count constraints enforce limits
                temperature=0.45,
                _debug_label='part2_reply_retry',
            )
            bot_reply = (getattr(retry.choices[0].message, 'content', '') or '').strip() if retry is not None else ''
        if not bot_reply:
            print('Groq returned no response for final generation')
            _debug_ai_log('PART2 fail', 'no_reply_after_model_and_api_fallback')
            return None

        _debug_ai_log('PART2 final_reply', bot_reply)

        print(f"✅ [{mode}/{response_length}] Response generated")
        return bot_reply

    except Exception as e:
        print(f"Error generating response: {e}")
        _debug_ai_log('PART2 fail', f'exception={e}')
        return None


def generate_smart_title(user_message):
    """
    Generate a smart, concise title for a conversation based on the user's first message
    Uses Groq to create an intelligent summary (3-6 words)
    """
    try:
        response = groq_chat_create(
            model=groq_model,
            messages=[
                {
                    "role": "system",
                    "content": "Generate ONLY a conversation title. Rules: 3-6 words; capture main concern or emotion; empathetic and clear language; Title Case; no quotes; no trailing punctuation."
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            max_tokens=40,
            temperature=0.7
        )
        
        if response is None:
            print('Groq returned no response for smart title — using fallback title')
            # Fallback: Use first 50 chars (keeps behavior minimal)
            fallback_title = user_message[:50] + '...' if len(user_message) > 50 else user_message
            return fallback_title

        title = response.choices[0].message.content.strip()
        
        # Remove quotes if AI added them
        title = title.strip('"\'')
        
        # Ensure title is not too long (fallback)
        if len(title) > 60:
            title = title[:57] + '...'
        
        print(f"✨ Generated smart title: {title}")
        return title
    
    except Exception as e:
        print(f"Error generating smart title: {e}")
        # Fallback: Use first 50 chars of message
        fallback_title = user_message[:50] + '...' if len(user_message) > 50 else user_message
        return fallback_title


def log_emotion(user_id, emotion, conversation_id, is_anonymous=False):
    """Write a single emotion log entry for admin analytics"""
    if not db:
        return
    try:
        now = datetime.now()
        db.collection('emotion_logs').document().set({
            'userId': user_id,
            'emotion': emotion,
            'conversationId': conversation_id or '',
            'isAnonymous': is_anonymous,
            'timestamp': now.isoformat(),
            'date': now.strftime('%Y-%m-%d')
        })
    except Exception as e:
        print(f'Error writing emotion log: {e}')


def store_chat_message(user_id, user_message, bot_reply, emotion, conversation_id=None, is_anonymous=False):
    """
    Store chat message in Firestore
    Structure: /conversations/{conversationID}/messages/{messageID}
    Uses camelCase for consistency with new database design
    """
    if not db:
        return
    
    try:
        # Log emotion for admin analytics
        log_emotion(user_id, emotion, conversation_id, is_anonymous)

        # Use separate timestamps to ensure proper ordering
        user_timestamp = datetime.now().isoformat()
        
        if conversation_id:
            # Store user message first
            user_message_ref = db.collection('conversations').document(conversation_id).collection('messages').document()
            user_message_ref.set({
                'message': user_message,
                'sender': 'user',
                'timestamp': user_timestamp,
                'order': 0  # User message comes first
            })
            
            # Add a small delay to ensure bot message has a later timestamp
            import time
            time.sleep(0.01)
            
            bot_timestamp = datetime.now().isoformat()
            
            # Store bot reply second
            bot_message_ref = db.collection('conversations').document(conversation_id).collection('messages').document()
            bot_message_ref.set({
                'message': bot_reply,
                'sender': 'bot',
                'emotion': emotion,
                'timestamp': bot_timestamp,
                'order': 1  # Bot message comes second
            })
            
            # Update conversation lastUpdated and lastMessage (camelCase)
            conversation_ref = db.collection('conversations').document(conversation_id)
            conversation_ref.update({
                'lastUpdated': bot_timestamp,
                'lastMessage': user_message[:50]
            })
        else:
            # No conversation_id provided - this shouldn't happen
            print(f"⚠️ Warning: No conversation_id provided for user {user_id}. Message NOT saved.")
            print(f"   This indicates the conversation wasn't created properly.")
        
        print(f"✅ Chat stored for user: {user_id}")
    
    except Exception as e:
        print(f"Error storing chat in Firestore: {e}")
        raise


# ==================== CONVERSATION MANAGEMENT ROUTES ====================

@app.route('/conversations', methods=['GET', 'POST'])
def manage_conversations():
    """Get all conversations or create new conversation"""
    if request.method == 'GET':
        user_id = request.args.get('user_id')
        is_archived = request.args.get('is_archived', 'false').lower() == 'true'
        is_guest = request.args.get('is_guest', 'false').lower() == 'true'
        
        if not user_id or not db:
            return jsonify([])
        
        try:
            print(f"🔍 Querying conversations: userId={user_id}, isGuest={is_guest}, isArchived={is_archived}")
            
            # Query conversations with proper filters
            # NOTE: Firestore requires composite index for multiple where clauses
            conversations_ref = db.collection('conversations')\
                .where('userId', '==', user_id)\
                .where('isAnonymous', '==', is_guest)\
                .where('isArchived', '==', is_archived)\
                .order_by('lastUpdated', direction=firestore.Query.DESCENDING)
            
            conversations = []
            for doc in conversations_ref.stream():
                conv_data = doc.to_dict()
                conv_data['id'] = doc.id
                conversations.append(conv_data)
                print(f"   📄 Found conversation: {doc.id} - {conv_data.get('title', 'No title')}")
            
            print(f"✅ Loaded {len(conversations)} {'archived' if is_archived else 'active'} conversations for {'guest' if is_guest else 'user'}: {user_id}")
            
            # Debug: If no conversations found, check if any exist for this user at all
            if len(conversations) == 0:
                print(f"⚠️ No conversations found with filters. Checking all conversations for user...")
                all_convs_ref = db.collection('conversations').where('userId', '==', user_id)
                all_count = len(list(all_convs_ref.stream()))
                print(f"   Total conversations for this user (no filters): {all_count}")
            
            return jsonify(conversations)
        except Exception as e:
            print(f"❌ Error fetching conversations: {e}")
            import traceback
            traceback.print_exc()
            return jsonify([])
    
    elif request.method == 'POST':
        data = request.json
        user_id = data.get('user_id')
        is_guest = data.get('is_guest', False)
        generate_smart_title_flag = data.get('generate_smart_title', False)
        first_message = data.get('first_message', '')
        title = data.get('title', 'New Conversation')
        
        if not user_id or not db:
            return jsonify({'error': 'Invalid request'}), 400
        
        try:
            # Generate smart title if requested
            if generate_smart_title_flag and first_message:
                title = generate_smart_title(first_message)
                print(f"✨ Using AI-generated title: {title}")
            
            conversation_ref = db.collection('conversations').document()
            conversation_data = {
                'userId': user_id,
                'isAnonymous': is_guest,
                'title': title,
                'createdAt': datetime.now().isoformat(),
                'lastUpdated': datetime.now().isoformat(),
                'isArchived': False,
                'lastMessage': ''
            }
            conversation_ref.set(conversation_data)
            
            conversation_data['id'] = conversation_ref.id
            print(f"✅ Created new conversation '{title}' for {'guest' if is_guest else 'user'}: {user_id}")
            return jsonify(conversation_data), 201
        except Exception as e:
            print(f"Error creating conversation: {e}")
            return jsonify({'error': 'Failed to create conversation'}), 500


@app.route('/conversations/<conversation_id>', methods=['PUT', 'DELETE'])
def update_conversation(conversation_id):
    """Update or delete a conversation"""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    
    if request.method == 'PUT':
        data = request.json
        title = data.get('title')
        
        if not title:
            return jsonify({'error': 'Title required'}), 400
        
        try:
            conversation_ref = db.collection('conversations').document(conversation_id)
            conversation_ref.update({'title': title})
            return jsonify({'success': True})
        except Exception as e:
            print(f"Error updating conversation: {e}")
            return jsonify({'error': 'Failed to update'}), 500
    
    elif request.method == 'DELETE':
        try:
            # Delete all messages in conversation
            messages_ref = db.collection('conversations').document(conversation_id).collection('messages')
            for msg in messages_ref.stream():
                msg.reference.delete()
            
            # Delete conversation
            db.collection('conversations').document(conversation_id).delete()
            return jsonify({'success': True})
        except Exception as e:
            print(f"Error deleting conversation: {e}")
            return jsonify({'error': 'Failed to delete'}), 500


@app.route('/conversations/<conversation_id>/archive', methods=['PUT'])
def archive_conversation(conversation_id):
    """Archive or unarchive a conversation"""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    
    data = request.json
    is_archived = data.get('is_archived', False)
    
    try:
        conversation_ref = db.collection('conversations').document(conversation_id)
        conversation_ref.update({
            'isArchived': is_archived,
            'lastUpdated': datetime.now().isoformat()
        })
        print(f"✅ Conversation {conversation_id} {'archived' if is_archived else 'unarchived'}")
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error archiving conversation: {e}")
        return jsonify({'error': 'Failed to archive'}), 500


@app.route('/conversations/<conversation_id>/messages', methods=['GET'])
def get_conversation_messages(conversation_id):
    """Get all messages in a conversation"""
    if not db:
        return jsonify([])
    
    try:
        messages_ref = db.collection('conversations').document(conversation_id)\
            .collection('messages')\
            .order_by('timestamp')
        
        messages = []
        for msg_doc in messages_ref.stream():
            msg_data = msg_doc.to_dict()
            messages.append(msg_data)
        
        return jsonify(messages)
    except Exception as e:
        print(f"Error fetching messages: {e}")
        return jsonify([])


@app.route('/logout', methods=['POST'])
def logout():
    """
    Handle user logout
    For guest users: Delete all their conversations
    For logged-in users: Just clear in-memory data (conversations persist)
    """
    try:
        data = request.json
        user_id = data.get('user_id')
        is_guest = data.get('is_guest', False)
        
        # Clear in-memory conversation history
        if user_id in conversation_history:
            conversation_history[user_id] = []
            print(f"🗑️ In-memory history cleared for user: {user_id}")
        
        # For guest users: DELETE all conversations from Firestore
        if is_guest and db and user_id:
            try:
                # Query all guest conversations
                conversations_ref = db.collection('conversations')\
                    .where('userId', '==', user_id)\
                    .where('isAnonymous', '==', True)
                
                deleted_count = 0
                
                for conv_doc in conversations_ref.stream():
                    conversation_id = conv_doc.id
                    
                    # Delete all messages in this conversation
                    messages_ref = db.collection('conversations')\
                        .document(conversation_id)\
                        .collection('messages')
                    
                    for msg_doc in messages_ref.stream():
                        msg_doc.reference.delete()
                    
                    # Delete the conversation document
                    conv_doc.reference.delete()
                    deleted_count += 1
                
                print(f"🗑️ Guest logout: Deleted {deleted_count} conversation(s) for user: {user_id}")
                return jsonify({
                    'success': True,
                    'message': 'Guest data deleted',
                    'deleted_conversations': deleted_count
                })
            except Exception as e:
                print(f"❌ Error deleting guest data on logout: {e}")
                return jsonify({'success': False, 'error': 'Failed to delete guest data'}), 500
        
        # For logged-in users: Just confirm logout
        print(f"✅ Logged-in user logout: Data persisted for user: {user_id}")
        return jsonify({
            'success': True,
            'message': 'Logged out successfully'
        })
    
    except Exception as e:
        print(f"❌ Error in logout: {e}")
        return jsonify({'success': False, 'error': 'Logout failed'}), 500


# ==================== USER PROGRESS ROUTES ====================

@app.route('/progress/<user_id>')
def progress_page(user_id):
    """Render the progress page for a specific user."""
    return render_template('progress.html')


@app.route('/user/progress/<user_id>')
def user_progress(user_id):
    """Return progress/analytics data for a specific user."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    try:
        logs = [doc.to_dict() for doc in
                db.collection('emotion_logs').where('userId', '==', user_id).stream()]

        today = datetime.now()
        week_start = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
        month_start = today.strftime('%Y-%m-01')
        thirty_ago = (today - timedelta(days=29)).strftime('%Y-%m-%d')

        emotion_keys = ['happy', 'calm', 'sad', 'anxious', 'stressed',
                        'angry', 'confused', 'motivated', 'tired', 'numb']
        overall_counts = {e: 0 for e in emotion_keys}
        weekly_counts  = {e: 0 for e in emotion_keys}
        monthly_counts = {e: 0 for e in emotion_keys}

        # Daily trend for last 30 days
        date_labels = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(29, -1, -1)]
        daily_emotion = {d: {e: 0 for e in emotion_keys} for d in date_labels}
        daily_total   = {d: 0 for d in date_labels}

        dates_active = set()
        for log in logs:
            e = log.get('emotion', 'neutral')
            d = log.get('date', '')
            if e in overall_counts:
                overall_counts[e] += 1
            if d >= week_start and e in weekly_counts:
                weekly_counts[e] += 1
            if d >= month_start and e in monthly_counts:
                monthly_counts[e] += 1
            if d in daily_emotion and e in daily_emotion[d]:
                daily_emotion[d][e] += 1
                daily_total[d] += 1
            if d:
                dates_active.add(d)

        total_messages = len(logs)
        streak = _calculate_streak(sorted(dates_active, reverse=True), today.strftime('%Y-%m-%d'))

        # Positive vs negative ratio
        positive_emotions = {'happy', 'calm', 'motivated'}
        negative_emotions = {'sad', 'anxious', 'stressed', 'angry', 'tired', 'numb'}
        pos_count = sum(overall_counts[e] for e in positive_emotions)
        neg_count = sum(overall_counts[e] for e in negative_emotions)

        # Dominant emotion per day (for mood history)
        mood_history = []
        for d in date_labels:
            day_data = daily_emotion[d]
            dominant = max(day_data, key=day_data.get) if daily_total[d] > 0 else None
            mood_history.append({'date': d, 'dominant': dominant, 'total': daily_total[d]})

        # Personal growth milestones
        milestones = _calculate_milestones(total_messages, streak, pos_count, neg_count, len(dates_active))

        return jsonify({
            'totalMessages': total_messages,
            'daysActive': len(dates_active),
            'currentStreak': streak,
            'overallEmotions': overall_counts,
            'weeklyEmotions': weekly_counts,
            'monthlyEmotions': monthly_counts,
            'moodHistory': mood_history,
            'dateLabels': [d[5:] for d in date_labels],
            'positiveCount': pos_count,
            'negativeCount': neg_count,
            'milestones': milestones
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _calculate_streak(sorted_dates_desc, today_str):
    """Calculate consecutive days active ending today or yesterday."""
    if not sorted_dates_desc:
        return 0
    streak = 0
    check = today_str
    for d in sorted_dates_desc:
        if d == check:
            streak += 1
            dt = datetime.strptime(check, '%Y-%m-%d') - timedelta(days=1)
            check = dt.strftime('%Y-%m-%d')
        elif d < check:
            break
    return streak


def _calculate_milestones(total_msgs, streak, pos_count, neg_count, days_active):
    milestones = []
    if total_msgs >= 1:
        milestones.append({'icon': '💬', 'title': 'First Conversation', 'desc': 'You started your journey with Menti.', 'unlocked': True})
    if total_msgs >= 10:
        milestones.append({'icon': '🌱', 'title': 'Growing Bond', 'desc': '10 messages shared — you\'re opening up!', 'unlocked': True})
    if total_msgs >= 50:
        milestones.append({'icon': '🌿', 'title': 'Regular Sharer', 'desc': '50 messages — consistency is key!', 'unlocked': True})
    if total_msgs >= 100:
        milestones.append({'icon': '🌳', 'title': 'Deep Talker', 'desc': '100 messages — you\'re truly committed.', 'unlocked': True})
    if streak >= 3:
        milestones.append({'icon': '🔥', 'title': '3-Day Streak', 'desc': 'Chatted 3 days in a row!', 'unlocked': True})
    if streak >= 7:
        milestones.append({'icon': '⚡', 'title': 'Week Warrior', 'desc': '7-day streak — incredible dedication!', 'unlocked': True})
    if days_active >= 14:
        milestones.append({'icon': '📅', 'title': '2 Weeks Strong', 'desc': 'Active across 14 different days.', 'unlocked': True})
    if pos_count > neg_count and total_msgs >= 10:
        milestones.append({'icon': '☀️', 'title': 'Positive Vibes', 'desc': 'Your positive emotions outweigh the negative!', 'unlocked': True})
    # Locked teasers
    if total_msgs < 50:
        milestones.append({'icon': '🔒', 'title': 'Regular Sharer', 'desc': 'Send 50 messages to unlock.', 'unlocked': False})
    if streak < 7:
        milestones.append({'icon': '🔒', 'title': 'Week Warrior', 'desc': 'Maintain a 7-day streak to unlock.', 'unlocked': False})
    return milestones


def _latest_iso(a, b):
    """Return the later ISO-like string value."""
    return b if (b or '') > (a or '') else a


def _build_user_cache_meta(user_id):
    """Build lightweight cache metadata so clients refresh only when user data changed."""
    if not db:
        return {'revision': 'no-db', 'updatedAt': datetime.now().isoformat()}

    profile_updated = ''
    conversations_updated = ''
    conversations_count = 0
    emotions_updated = ''
    emotions_count = 0
    journal_updated = ''
    journal_count = 0
    plan_updated = ''
    plan_status = 'active'

    try:
        prof_doc = db.collection('user_profiles').document(user_id).get()
        if prof_doc.exists:
            p = prof_doc.to_dict() or {}
            profile_updated = p.get('updatedAt', '')

        for conv_doc in db.collection('conversations').where('userId', '==', user_id).stream():
            conversations_count += 1
            c = conv_doc.to_dict() or {}
            stamp = c.get('lastUpdated') or c.get('updatedAt') or c.get('createdAt') or ''
            conversations_updated = _latest_iso(conversations_updated, stamp)

        for log_doc in db.collection('emotion_logs').where('userId', '==', user_id).stream():
            emotions_count += 1
            l = log_doc.to_dict() or {}
            stamp = l.get('timestamp') or l.get('date') or ''
            emotions_updated = _latest_iso(emotions_updated, stamp)

        for j_doc in db.collection('journal_entries').where('userId', '==', user_id).stream():
            journal_count += 1
            j = j_doc.to_dict() or {}
            stamp = j.get('updatedAt') or j.get('createdAt') or ''
            journal_updated = _latest_iso(journal_updated, stamp)

        plan_doc = db.collection('wellness_plans').document(user_id).get()
        if plan_doc.exists:
            plan = plan_doc.to_dict() or {}
            plan_updated = plan.get('updatedAt', '')
            plan_status = plan.get('status', 'active')
    except Exception as e:
        print(f'[cache-meta] Error for user {user_id}: {e}')

    meta = {
        'profileUpdatedAt': profile_updated,
        'conversationsUpdatedAt': conversations_updated,
        'conversationsCount': conversations_count,
        'emotionsUpdatedAt': emotions_updated,
        'emotionsCount': emotions_count,
        'journalUpdatedAt': journal_updated,
        'journalCount': journal_count,
        'planUpdatedAt': plan_updated,
        'planStatus': plan_status,
    }
    revision_source = json.dumps(meta, sort_keys=True)
    meta['revision'] = hashlib.sha256(revision_source.encode()).hexdigest()[:20]
    meta['updatedAt'] = datetime.now().isoformat()
    return meta


def _generate_wellness_plan(user_id):
    """Generate a supportive, realistic wellness plan based on user trends."""
    emotion_counts = {}
    try:
        if db:
            for log_doc in db.collection('emotion_logs').where('userId', '==', user_id).stream():
                emotion = (log_doc.to_dict() or {}).get('emotion', 'neutral')
                emotion_counts[emotion] = emotion_counts.get(emotion, 0) + 1
    except Exception as e:
        print(f'[wellness plan] Error collecting emotions for {user_id}: {e}')

    dominant = 'calm'
    if emotion_counts:
        dominant = max(emotion_counts, key=emotion_counts.get)

    intensity = emotion_counts.get('stressed', 0) + emotion_counts.get('anxious', 0)
    cadence = 'weekly' if intensity < 3 else 'daily'

    goals = [
        {
            'title': 'Emotional Check-In',
            'description': 'Spend 3-5 minutes each day naming how you feel without judgment.',
            'category': 'emotional-balance',
            'target': '5 check-ins this week'
        },
        {
            'title': 'Stress Reset Routine',
            'description': 'Use one calming activity when stress feels high.',
            'category': 'stress-management',
            'target': 'At least 4 times this week'
        },
        {
            'title': 'Gentle Self-Care',
            'description': 'Choose one realistic self-care action each day (sleep, hydration, movement, or rest).',
            'category': 'self-care',
            'target': '1 action daily'
        }
    ]

    reflections = [
        'What is one thing you are proud of handling today?',
        'What emotion felt strongest today, and what might it be asking for?',
        'What is one thought you can release tonight?'
    ]

    coping_exercises = [
        {
            'title': 'Box Breathing',
            'duration': '2-4 minutes',
            'steps': ['Inhale for 4', 'Hold for 4', 'Exhale for 4', 'Hold for 4', 'Repeat 4 cycles']
        },
        {
            'title': '5-4-3-2-1 Grounding',
            'duration': '3 minutes',
            'steps': ['5 things you see', '4 things you feel', '3 things you hear', '2 things you smell', '1 thing you taste']
        }
    ]

    if dominant in ('sad', 'tired', 'numb'):
        goals.append({
            'title': 'Energy Support',
            'description': 'Pick one small uplifting activity (sunlight, shower, short walk, or music).',
            'category': 'self-care',
            'target': '3 times this week'
        })

    return {
        'userId': user_id,
        'status': 'active',
        'cadence': cadence,
        'dominantEmotion': dominant,
        'emotionCounts': emotion_counts,
        'goals': goals,
        'reflections': reflections,
        'copingExercises': coping_exercises,
        'notes': 'This plan is adaptive and non-pressuring. You can modify or pause anytime.',
        'updatedAt': datetime.now().isoformat(),
        'generatedAt': datetime.now().isoformat()
    }


def _default_offline_resources():
    """Default offline-capable mental health resource library payload."""
    return {
        'version': '2026-07-23',
        'title': 'Offline Mental Health Resource Library',
        'items': [
            {
                'id': 'article-001',
                'category': 'Mental Health Articles',
                'title': 'Understanding Anxiety Disorders: A Comprehensive Guide',
                'summary': 'Learn about different types of anxiety, their symptoms, and evidence-based treatments.',
                'content': '''UNDERSTANDING ANXIETY DISORDERS: A COMPREHENSIVE GUIDE

What is Anxiety?
Anxiety is a natural emotional response to stress. However, anxiety disorders occur when anxiety becomes excessive, persistent, and interferes with daily life. Common anxiety disorders include generalized anxiety disorder (GAD), panic disorder, social anxiety disorder, and specific phobias.

Common Symptoms:
- Physical: rapid heartbeat, sweating, trembling, dizziness, muscle tension
- Emotional: persistent worry, fear, irritability, feelings of dread
- Cognitive: difficulty concentrating, racing thoughts, catastrophic thinking
- Behavioral: avoidance, restlessness, sleep disturbances

Evidence-Based Treatments:
1. Cognitive Behavioral Therapy (CBT) - Helps identify and change negative thought patterns
2. Exposure Therapy - Gradual exposure to feared situations in a safe environment
3. Medication - SSRIs and other anti-anxiety medications prescribed by healthcare providers
4. Lifestyle Changes - Regular exercise, sleep hygiene, stress reduction techniques

When to Seek Help:
- Anxiety persists for more than 6 months
- Symptoms interfere with work, school, or relationships
- You experience panic attacks
- Anxiety significantly impacts your quality of life

Resources:
- National Institute of Mental Health (NIMH)
- Anxiety and Depression Association of America (ADAA)
- Local mental health professionals and therapists'''
            },
            {
                'id': 'article-002',
                'category': 'Mental Health Articles',
                'title': 'Depression: Recognition and Recovery',
                'summary': 'Understand depression symptoms, causes, and paths to recovery.',
                'content': '''DEPRESSION: RECOGNITION AND RECOVERY

What is Clinical Depression?
Depression (Major Depressive Disorder) is more than sadness - it\'s a medical condition affecting mood, thoughts, physical health, and behavior. It affects millions globally and is highly treatable.

Key Symptoms (lasting 2+ weeks):
- Persistent sad, empty, or hopeless mood
- Loss of interest in activities once enjoyed
- Changes in appetite or sleep patterns
- Low energy and fatigue
- Difficulty concentrating or making decisions
- Feelings of worthlessness or guilt
- Thoughts of death or suicide

Risk Factors:
- Family history of depression
- Significant life stressors (loss, trauma, major changes)
- Chronic medical conditions
- Substance abuse
- Social isolation

Treatment Options:
1. Psychotherapy (CBT, interpersonal therapy, behavioral activation)
2. Medication (antidepressants prescribed by doctors)
3. Lifestyle modifications (exercise, sleep, social connection)
4. Combination therapy (therapy + medication for moderate to severe depression)

Recovery is Possible:
Most people with depression respond well to treatment. Recovery is not linear - setbacks are normal. With proper support and treatment, people regain their quality of life and rediscover purpose.

Getting Help:
- Talk to your doctor or mental health professional
- Crisis Text Line: Text HOME to 741741
- National Suicide Prevention Lifeline: 988'''
            },
            {
                'id': 'guide-001',
                'category': 'Wellness Guides',
                'title': 'Sleep Hygiene: Better Sleep for Better Mental Health',
                'summary': 'Practical tips for improving sleep quality and supporting mental wellness.',
                'content': '''SLEEP HYGIENE: BETTER SLEEP FOR BETTER MENTAL HEALTH

Why Sleep Matters:
Quality sleep is essential for mental health, emotional regulation, immune function, and cognitive performance. Poor sleep exacerbates anxiety and depression, while good sleep supports recovery and resilience.

Sleep Hygiene Practices:

1. Consistent Sleep Schedule
   - Go to bed and wake up at the same time daily (even weekends)
   - Helps regulate your body\'s internal clock
   - Aim for 7-9 hours for adults

2. Create a Restful Environment
   - Keep bedroom cool (65-68°F ideal), dark, and quiet
   - Use blackout curtains or eye mask
   - Consider white noise machines for distracting sounds
   - Invest in comfortable bedding

3. Pre-Sleep Routine (30-60 minutes before bed)
   - Dim the lights to increase melatonin production
   - Avoid screens (blue light suppresses melatonin)
   - Try relaxing activities: reading, stretching, meditation
   - Warm bath or shower

4. Dietary Considerations
   - Avoid caffeine 6+ hours before bed
   - Limit alcohol (disrupts sleep quality)
   - Avoid large meals close to bedtime
   - Herbal tea (chamomile, passionflower) may help

5. Exercise and Physical Activity
   - Regular exercise improves sleep quality
   - Avoid vigorous exercise 3 hours before bed
   - Even 30 minutes of moderate activity helps

6. Mind-Body Techniques
   - Progressive muscle relaxation
   - 4-7-8 breathing technique
   - Guided meditation or body scan
   - Journaling before bed (brain dump)

If Sleep Problems Persist:
- Consult a sleep specialist
- Consider Cognitive Behavioral Therapy for Insomnia (CBT-I)
- Sleep tracking apps may help identify patterns
- Discuss with healthcare provider about other options'''
            },
            {
                'id': 'guide-002',
                'category': 'Wellness Guides',
                'title': 'Exercise and Mental Health: Movement as Medicine',
                'summary': 'How physical activity supports emotional well-being and mental health.',
                'content': '''EXERCISE AND MENTAL HEALTH: MOVEMENT AS MEDICINE

The Mind-Body Connection:
Regular exercise is one of the most powerful tools for mental health. It reduces stress, improves mood, enhances self-esteem, and can be as effective as medication for mild to moderate depression and anxiety.

How Exercise Helps Mental Health:

Biochemical Changes:
- Increases endorphins ("feel-good" chemicals)
- Reduces stress hormones (cortisol, adrenaline)
- Improves sleep quality
- Regulates neurotransmitters (serotonin, dopamine, GABA)

Psychological Benefits:
- Increased sense of accomplishment
- Improved body image and self-confidence
- Distraction from negative thoughts
- Enhanced focus and mental clarity
- Sense of control and mastery

Getting Started:

For Beginners:
- Start with 10-15 minutes of moderate activity
- Choose activities you enjoy (walking, dancing, swimming)
- Build gradually to 150 minutes weekly
- No gym required - nature walks work great

Best Types of Exercise for Mental Health:
1. Aerobic exercise (running, cycling, swimming)
2. Strength training (builds confidence and resilience)
3. Mind-body exercises (yoga, tai chi, Pilates)
4. Team sports (adds social connection)
5. Outdoor activities (combines nature benefits)

Making It Stick:
- Schedule exercise like an appointment
- Find an accountability partner or group
- Track your mood improvements
- Mix activities to prevent boredom
- Start small and build consistency

Exercise for Specific Conditions:
- Anxiety: Aerobic exercise reduces physical tension
- Depression: Consistent activity combats low motivation
- ADHD: Physical activity improves focus
- Stress: Any enjoyable movement helps

Remember: Something is always better than nothing. Even a 10-minute walk provides mental health benefits.'''
            },
            {
                'id': 'guide-003',
                'category': 'Wellness Guides',
                'title': 'Nutrition for Mental Health: Food and Mood Connection',
                'summary': 'Understanding how diet impacts mental well-being and emotional health.',
                'content': '''NUTRITION FOR MENTAL HEALTH: FOOD AND MOOD CONNECTION

The Gut-Brain Axis:
Mounting scientific evidence shows that nutrition directly impacts mental health. The foods we eat influence brain chemistry, mood regulation, anxiety levels, and overall psychological well-being.

Key Nutrients for Mental Health:

1. Omega-3 Fatty Acids
   Benefits: Reduce inflammation, support brain cell function, may alleviate depression
   Sources: Fatty fish (salmon, mackerel), walnuts, flaxseeds, chia seeds
   Target: 2-3 servings weekly of fatty fish

2. B Vitamins (especially B6, B12, Folate)
   Benefits: Support neurotransmitter production, reduce homocysteine levels
   Sources: Whole grains, leafy greens, eggs, chickpeas, almonds
   Deficiency linked to: Depression, anxiety, cognitive decline

3. Amino Acids
   Benefits: Build neurotransmitters (serotonin, dopamine)
   Sources: Lean proteins, legumes, dairy, nuts, seeds
   Function: Support mood regulation and stress resilience

4. Magnesium
   Benefits: Calms nervous system, reduces anxiety, improves sleep
   Sources: Pumpkin seeds, almonds, spinach, dark chocolate, avocado
   Effects: Deficiency increases anxiety and depression risk

5. Probiotics and Gut Health
   Benefits: 90% of serotonin produced in gut; healthy microbiome supports mental health
   Sources: Yogurt, kefir, sauerkraut, kimchi, miso
   Effect: Improves mood and reduces inflammation

Mental Health Eating Guidelines:

✓ DO:
- Eat whole foods (minimize processed foods)
- Include colorful fruits and vegetables (antioxidants)
- Stay hydrated (even mild dehydration affects mood)
- Eat regular meals (prevents blood sugar crashes)
- Include healthy fats (avocado, olive oil, nuts)

✗ AVOID:
- Excessive sugar (mood crashes, inflammation)
- Alcohol in large amounts (worsens depression, anxiety)
- Caffeine excess (increases anxiety)
- Ultra-processed foods (linked to depression)
- Skipping meals (destabilizes mood and energy)

Sample Day of Brain-Healthy Eating:
Breakfast: Oatmeal with walnuts and berries
Lunch: Grilled chicken with quinoa and roasted vegetables
Snack: Almonds and an apple
Dinner: Baked salmon with sweet potato and broccoli
Beverages: Water, herbal tea, moderate coffee

The Mediterranean Diet:
Research shows the Mediterranean diet (olive oil, fish, vegetables, whole grains, legumes) is particularly beneficial for mental health and may reduce depression risk by up to 30%.

Remember: Food is not a cure, but proper nutrition is foundational to mental health treatment.'''
            },
            {
                'id': 'technique-001',
                'category': 'Coping Techniques',
                'title': 'Cognitive Behavioral Therapy (CBT) Basics for Self-Help',
                'summary': 'Learn core CBT principles to challenge unhelpful thoughts and change patterns.',
                'content': '''COGNITIVE BEHAVIORAL THERAPY (CBT) BASICS FOR SELF-HELP

What is CBT?
CBT is a proven psychological treatment based on the connection between thoughts, feelings, and behaviors. By identifying and changing unhelpful thought patterns, we can improve our emotions and actions.

The CBT Triangle:

Thoughts → Feelings → Behaviors
   ↑____________↓___________↓
     (all interconnected)

Example:
Thought: "I\'ll fail my exam"
Feeling: Anxiety, dread
Behavior: Avoid studying, isolate

Core CBT Techniques:

1. Thought Records (Identifying Automatic Thoughts)
   - Write down triggering situation
   - Notice automatic thoughts that follow
   - Rate belief strength (0-100%)
   - Identify resulting emotions
   - Challenge and reframe the thought

2. Thought Challenging Questions:
   - Is this thought based on facts or feelings?
   - What evidence supports/contradicts this thought?
   - What would I tell a friend in this situation?
   - What\'s the worst realistic outcome? Could I handle it?
   - Is there another way to look at this?

3. Behavioral Activation
   - Depression thrives on avoidance
   - Schedule activities even without motivation
   - Mix pleasurable and meaningful activities
   - Track mood before/after activities
   - Gradually increase activity level

4. Exposure (Facing Fears Gradually)
   - Create hierarchy of feared situations
   - Expose yourself gradually (step-by-step)
   - Stay in situation until anxiety reduces
   - Repeat until habituation occurs
   - Prevents avoidance from strengthening fears

5. Problem-Solving Technique:
   Step 1: Define the problem clearly
   Step 2: Brainstorm all possible solutions
   Step 3: Evaluate pros/cons of each solution
   Step 4: Choose and implement best solution
   Step 5: Evaluate results and adjust

Common Thinking Errors to Watch For:
- Catastrophizing: Assuming worst-case scenarios
- All-or-nothing thinking: Seeing things as completely good or bad
- Overgeneralization: One bad event means everything is bad
- Mind reading: Assuming what others think about you
- Emotional reasoning: Believing feelings equal facts

Getting Started with Self-Help CBT:
1. Get a CBT workbook or use online resources
2. Practice thought records daily
3. Identify your core unhelpful thoughts
4. Challenge them systematically
5. Monitor mood changes
6. Be patient - real change takes time

When to Seek Professional Help:
- CBT becomes overwhelming
- Suicidal thoughts emerge
- Symptoms don\'t improve in 4-6 weeks
- You need personalized treatment planning

CBT is a skill - it improves with practice. Small thought shifts lead to significant emotional changes.'''
            },
            {
                'id': 'technique-002',
                'category': 'Coping Techniques',
                'title': 'Mindfulness and Meditation for Anxiety Relief',
                'summary': 'Practical mindfulness and meditation exercises to calm the mind.',
                'content': '''MINDFULNESS AND MEDITATION FOR ANXIETY RELIEF

What is Mindfulness?
Mindfulness is the practice of purposefully paying attention to the present moment without judgment. It reduces anxiety by anchoring your mind in the "here and now" rather than worry about future "what-ifs."

Key Benefits:
- Reduces anxiety and worry
- Improves emotional regulation
- Decreases rumination and overthinking
- Enhances focus and concentration
- Increases self-compassion
- Lowers stress hormones

Core Mindfulness Principle:
Observe thoughts like clouds passing in the sky - notice them without judgment, and let them pass.

Beginner Meditation Practices:

1. Basic Breath Awareness (5 minutes)
   - Find quiet space, sit comfortably
   - Close eyes or soft gaze downward
   - Notice natural breath (no need to change it)
   - When mind wanders (it will!), gently return focus to breath
   - Practice daily for best results

2. Body Scan Meditation (10 minutes)
   - Lie down comfortably on your back
   - Bring attention to toes, notice sensations
   - Slowly move awareness up through body
   - Notice tension, warmth, tingling without judgment
   - Breathe into areas of tension
   - Great before bed for relaxation

3. 5-4-3-2-1 Grounding Meditation (5 minutes)
   - Name 5 things you see
   - Name 4 things you feel
   - Name 3 things you hear
   - Name 2 things you smell
   - Name 1 thing you taste
   - Brings you fully into present moment

4. Loving-Kindness Meditation (10 minutes)
   - Sit comfortably
   - Silently repeat: "May I be happy, may I be healthy, may I be safe, may I live with ease"
   - Extend to loved one, neutral person, difficult person, all beings
   - Reduces anxiety and increases compassion

5. Walking Meditation
   - Walk slowly, indoors or nature
   - Feel each foot contact the ground
   - Notice surroundings with curiosity
   - Synchronize steps with breathing
   - Great alternative if sitting feels difficult

Mindfulness in Daily Life:

Mindful Eating:
- Slow down, notice flavors and textures
- Eat without screens
- Appreciate food preparation

Mindful Listening:
- Give full attention to conversations
- Don\'t plan your response while listening
- Notice body language and tone

Mindful Moments:
- Morning: Set intention for day
- Transitions: Pause between activities
- Evening: Reflect on moments of peace

Managing Wandering Minds:
- This is completely normal and expected
- The practice is returning focus each time mind wanders
- Even 5 minutes of practice helps reduce anxiety
- Progress isn\'t about perfection - it\'s about showing up

Getting Started:
- Apps: Insight Timer (free), Calm, Headspace
- Try 5 minutes daily to build habit
- Be consistent - benefits compound over time
- Combine with breathing exercises for faster anxiety relief

Research Shows:
- 8 weeks of mindfulness reduces anxiety by 25-35%
- Effective as medication for mild to moderate anxiety
- Improves attention and emotional regulation
- Decreases inflammatory markers in body

Remember: Meditation is not about emptying your mind. It\'s about observing your mind with kindness.'''
            },
            {
                'id': 'awareness-001',
                'category': 'Mental Health Awareness',
                'title': 'Breaking Mental Health Stigma: Facts vs. Myths',
                'summary': 'Challenge common misconceptions and build understanding around mental health.',
                'content': '''BREAKING MENTAL HEALTH STIGMA: FACTS VS. MYTHS

Understanding Stigma:
Stigma - negative beliefs and discrimination toward people with mental illness - prevents millions from seeking help. Here are key facts to counter common myths.

Myth #1: "Mental illness isn\'t real - it\'s just weakness"
FACT: Mental illness is as real as diabetes or heart disease. Brain chemistry, genetics, and life experiences create conditions like depression, anxiety, and bipolar disorder. Millions worldwide are affected.

Myth #2: "People with mental illness are dangerous"
FACT: People with mental health conditions are more likely to be victims of violence than perpetrators. Most are no more dangerous than the general population.

Myth #3: "You can just snap out of it / think positive"
FACT: Mental illness requires professional treatment. Willpower alone cannot cure clinical depression or anxiety - just as willpower can\'t cure cancer.

Myth #4: "Only certain types of people get mental illness"
FACT: Mental health conditions affect people of all ages, races, genders, and socioeconomic backgrounds. 1 in 5 adults experience mental illness annually.

Myth #5: "Seeking help is a sign of weakness"
FACT: Getting help is courageous and shows strength. Champions, leaders, and successful people regularly see therapists and manage mental health.

Myth #6: "Medication means you\'re not strong enough"
FACT: Medications for mental health are legitimate treatments that help balance brain chemistry. They\'re not "crutches" - they\'re medicine.

Myth #7: "Therapy means you\'re broken"
FACT: Therapy is like exercise for your brain. Everyone benefits from learning new skills and gaining perspective on challenges.

Myth #8: "You need to suffer before getting help"
FACT: Early intervention leads to better outcomes. Getting help at first signs of struggle prevents deterioration.

Reducing Stigma in Your Community:

Language Matters:
- Use "person with depression" not "depressed person"
- Say "experiencing suicide thoughts" not "suicidal"
- Avoid: "crazy," "psycho," "insane" as insults
- Recognize mental health conditions as medical conditions

Supporting Others:
- Listen without judgment
- Ask "How can I help?"
- Educate yourself about their condition
- Avoid toxic positivity ("just be positive!")
- Take suicide threats seriously

In Your Own Life:
- Don\'t minimize your own struggles
- Celebrate seeking help as growth
- Be compassionate with yourself during recovery
- Challenge negative self-stigma

Facts About Mental Health:
✓ 1 in 5 adults have mental illness
✓ 75% of suicide cases could be prevented with early intervention
✓ Mental health treatment works (70-80% success rates)
✓ Recovery is possible and people do get better
✓ Seeking help is increasingly normal and accepted

Building a Stigma-Free World:
Each conversation counts. By sharing accurate information and treating mental health as a normal part of wellness, you help reduce stigma and encourage others to seek needed support.

Your mental health is as important as your physical health. Let\'s normalize the conversation.'''
            },
            {
                'id': 'awareness-002',
                'category': 'Mental Health Awareness',
                'title': 'Understanding the Stress-Health Connection',
                'summary': 'Learn how chronic stress affects physical and mental health.',
                'content': '''UNDERSTANDING THE STRESS-HEALTH CONNECTION

What is Stress?
Stress is your body\'s response to demands or threats. The "fight-or-flight" response is adaptive in short bursts, but chronic stress damages both mental and physical health.

The Stress Response (Acute):
1. Threat detected → Brain activates alarm
2. Cortisol & adrenaline released → Body mobilizes
3. Heart rate increases, focus sharpens → Ready for action
4. Threat passes → Body returns to baseline
5. Recovery and adaptation occur

Acute Stress (Helpful):
- Short-term stressor
- Body recovers afterward
- Builds resilience when managed well
- Examples: deadline, presentation, exam

Chronic Stress (Harmful):
- Ongoing, unrelenting pressure
- Body stays activated for weeks/months
- Overwhelms your coping resources
- Examples: ongoing financial worry, relationship conflict, constant work overload

Physical Effects of Chronic Stress:

Cardiovascular:
- High blood pressure
- Heart disease
- Increased stroke risk

Immune System:
- Weakened immunity
- Frequent infections
- Slow wound healing
- Increased inflammation

Digestive:
- Stomach ulcers
- Irritable bowel syndrome
- Acid reflux
- Constipation or diarrhea

Other Physical Effects:
- Headaches and migraines
- Muscle tension and pain
- Sleep disturbances
- Weight changes
- Hormonal imbalances

Mental Health Effects:

Emotional:
- Anxiety and worry
- Depression
- Irritability and anger
- Overwhelm

Cognitive:
- Memory problems
- Difficulty concentrating
- Brain fog
- Racing thoughts

Behavioral:
- Overeating or undereating
- Substance abuse
- Sleep disruption
- Social withdrawal

The Stress Cycle:
Physical symptoms → More worry about symptoms → More stress → Worsening symptoms

Breaking the Cycle:

1. Stress Identification
   - Track stressors (journal method)
   - Distinguish between controllable and uncontrollable
   - Separate real threats from perceived threats

2. Stress Reduction Techniques
   - Physical: exercise, stretching, yoga
   - Mental: meditation, deep breathing, mindfulness
   - Social: talking, connecting, support groups
   - Behavioral: time management, saying no, delegation

3. Lifestyle Changes
   - Prioritize 7-9 hours sleep
   - Reduce caffeine and sugar
   - Establish boundaries
   - Schedule recovery time
   - Maintain social connections

4. Cognitive Approaches
   - Challenge catastrophic thinking
   - Focus on what you can control
   - Practice gratitude
   - Develop problem-solving skills

5. Professional Support
   - Therapy for chronic stress patterns
   - Medical evaluation if symptoms persist
   - Medication if needed
   - Coaching for stress management

Warning Signs of Chronic Stress:
- Persistent physical complaints without clear cause
- Ongoing anxiety or worry
- Sleep problems despite fatigue
- Frequent irritability
- Loss of interest in activities
- Difficulty making decisions
- Feeling overwhelmed regularly

The Good News:
Your body can heal. Once chronic stress is addressed:
- Blood pressure normalizes (weeks)
- Sleep improves (within days)
- Immune function strengthens (weeks)
- Mood stabilizes (weeks to months)
- Physical symptoms resolve

Stress Management is Self-Care:
Addressing chronic stress isn\'t selfish - it\'s essential maintenance for your physical and mental health. You deserve to feel calm and capable.'''
            },
            {
                'id': 'awareness-003',
                'category': 'Mental Health Awareness',
                'title': 'Building Emotional Resilience: Bouncing Back from Adversity',
                'summary': 'Develop skills to handle life challenges and grow stronger through difficulty.',
                'content': '''BUILDING EMOTIONAL RESILIENCE: BOUNCING BACK FROM ADVERSITY

What is Resilience?
Resilience is your ability to adapt and recover from difficulty, trauma, or stress. It\'s not about avoiding challenges - it\'s about handling them effectively and growing through them.

Key Resilience Characteristics:
- Flexibility in thinking and behavior
- Problem-solving abilities
- Emotional awareness and regulation
- Strong sense of purpose
- Positive relationships
- Self-compassion
- Realistic optimism
- Ability to seek support

Resilience is Trainable:
Research shows resilience can be developed and strengthened through practice, much like physical strength.

Building Blocks of Resilience:

1. Self-Awareness
   - Know your values and strengths
   - Understand your stress triggers
   - Recognize your coping patterns
   - Monitor emotional state regularly
   - Practice honest self-reflection

2. Emotional Regulation
   - Identify and name emotions
   - Understand your emotional patterns
   - Develop healthy coping strategies
   - Manage impulses effectively
   - Stay calm under pressure

3. Positive Relationships
   - Build trust with others
   - Share vulnerabilities safely
   - Ask for and receive support
   - Contribute to others\' lives
   - Maintain connection during tough times

4. Sense of Purpose
   - Identify meaningful values
   - Set goals aligned with values
   - Engage in meaningful activities
   - Help others
   - Create positive contribution

5. Problem-Solving Skills
   - Break big problems into manageable parts
   - Brainstorm multiple solutions
   - Evaluate options objectively
   - Take action
   - Learn from outcomes

6. Physical Health
   - Regular exercise
   - Adequate sleep
   - Nutritious eating
   - Stress management
   - Medical care when needed

7. Cognitive Flexibility
   - Reframe challenges as growth opportunities
   - Challenge catastrophic thinking
   - Find silver linings
   - Learn from mistakes
   - Adapt strategies as needed

Resilience in Action - Real Examples:

Scenario 1: Job Loss
Without resilience: Spiral into hopelessness, isolation, depression
With resilience: Allow emotional reaction, identify transferable skills, explore new opportunities, maintain social connections, use as chance to reassess goals

Scenario 2: Relationship Breakup
Without resilience: Ruminate, lose self-identity, become bitter
With resilience: Grieve appropriately, reconnect with friendships, rediscover interests, maintain perspective, grow emotionally

Scenario 3: Health Challenge
Without resilience: Denial, depression, giving up
With resilience: Accept reality, gather information, follow treatment, adapt lifestyle, find meaning in experience

Resilience-Building Practices:

Daily Habits:
- Journaling about challenges and learning
- Gratitude practice (notice small positives)
- Physical activity (mood and stress relief)
- Connection with supportive people
- Meaningful activities

Weekly Practices:
- Reflect on challenges faced and how you handled them
- Strengthen key relationships
- Engage in activities that bring joy
- Problem-solve one challenge
- Practice self-compassion

Monthly Reviews:
- Assess growth in specific resilience areas
- Celebrate small wins
- Adjust strategies if needed
- Set meaningful goals
- Express gratitude

Tools for Building Resilience:

Post-It Reminders:
- "This is temporary"
- "I\'ve overcome challenges before"
- "I have people who care about me"
- "I can handle this"

Resilience Questions During Difficulty:
- "What\'s one small step I can take?"
- "Who can I reach out to?"
- "What\'s within my control?"
- "What can I learn from this?"
- "How will I grow from this?"

Setbacks are Part of Growth:
- Everyone has resilience failures
- Recovery looks different for everyone
- Speed of recovery matters more than perfection
- Each challenge builds stronger resilience

Research Findings:
- Resilient people recover 30-40% faster from stress
- Resilience training reduces anxiety by 25%
- Strong relationships increase resilience significantly
- Purpose and meaning are foundational to resilience

Remember: Resilience isn\'t about being tough. It\'s about bending without breaking, learning from struggle, and emerging stronger and wiser.'''
            },
            {
                'id': 'resource-001',
                'category': 'Self-Help Resources',
                'title': 'Emotional Regulation Workbook: Managing Your Feelings',
                'summary': 'Practical exercises to understand, accept, and manage emotions effectively.',
                'content': '''EMOTIONAL REGULATION WORKBOOK: MANAGING YOUR FEELINGS

What is Emotional Regulation?
Emotional regulation is your ability to recognize, understand, and manage your emotions in healthy ways. It\'s not suppression - it\'s skillful handling of feelings.

Why Emotional Regulation Matters:
- Better decision-making (emotions less likely to control you)
- Improved relationships (respond instead of react)
- Mental health stability
- Better stress management
- Increased self-esteem
- Greater life satisfaction

The Emotion Wheel - Understanding Your Feelings:

Primary Emotions (6 core):
- Happy
- Sad
- Angry
- Afraid
- Surprised
- Disgusted

Secondary Emotions (layered feelings):
- Happy → joyful, proud, grateful, peaceful
- Sad → lonely, disappointed, ashamed, guilty
- Angry → frustrated, bitter, irritated, violated
- Afraid → anxious, worried, insecure, helpless
- Surprised → confused, amazed, curious, skeptical
- Disgusted → disapproving, repulsed, contemptuous

Exercise 1: Emotion Identification
This week, notice your emotions:
- What were you feeling?
- Was it a primary or secondary emotion?
- What triggered it?
- What sensations did you notice in your body?
- What action did you take?
- How did it resolve?

The TIPP Skills (Quick Regulation When Overwhelmed):

Temperature:
- Splash cold water on face
- Hold ice cube
- Take cold shower
- Triggers vagus nerve, calms nervous system

Intense Exercise:
- Run, jump, dance
- Do pushups
- Any vigorous activity for 1-2 minutes
- Burns stress hormones quickly

Paced Breathing:
- Slow breath (5 seconds in, 5 seconds out)
- Slows heart rate immediately
- Activates parasympathetic nervous system

Pair Awareness:
- Get aware of physical sensations
- Use senses deliberately
- Interrupts emotional spiral

Exercise 2: Building Your Emotion Toolkit
Create a personalized list of coping strategies:

For Anxiety:
- Progressive muscle relaxation
- Breathing exercises
- Grounding techniques
- Physical movement
- Reassurance from trusted person

For Anger:
- Physical release (exercise, punch pillow)
- Time-out/space from trigger
- Write uncensored letter (don\'t send)
- Progressive muscle relaxation
- Express needs calmly later

For Sadness:
- Reach out to supportive person
- Gentle self-care (warm drink, bath)
- Physical activity (mood improves)
- Journaling
- Creative expression (art, music)

For Loneliness:
- Connect with one person
- Volunteer or help others
- Join group activity
- Reach out, don\'t wait to be invited
- Engage in community

The 5-Minute Emotional Reset:

1. Pause - Stop what you\'re doing
2. Name - What emotion are you feeling?
3. Breathe - Take 5 slow, deep breaths
4. Move - Do 2 minutes of physical activity
5. Refocus - Return to task with clearer mind

Exercise 3: Values-Based Emotional Response
Often our reactions conflict with our values:

Example:
Situation: Friend cancels plans
Reaction: Angry outburst, accusatory text
Values: Respect, understanding, kindness
Aligned Response: Ask if everything\'s okay, express disappointment calmly, plan later

Your Challenge:
- Identify your top 3 values
- Think of recent emotional reactions that violated them
- Plan how you\'d respond aligned with your values next time

Emotional Expression Healthy Guidelines:

✓ HEALTHY EXPRESSION:
- Acknowledge feelings without judgment
- Express in words (talk, write, create)
- Take responsibility ("I feel..." not "You made me...")
- Seek support from trusted people
- Allow yourself appropriate time to process

✗ UNHEALTHY EXPRESSION:
- Explosive outbursts
- Blaming/attacking others
- Substance use to numb
- Self-harm
- Destructive behavior
- Rumination and catastrophizing

The Emotion Doesn\'t Control Your Behavior:
You can feel angry AND respond respectfully
You can feel scared AND take brave action
You can feel sad AND reach out for help
Feelings are valid; choices are yours

Exercise 4: Create Your Emotional Profile
Track one week:
- Dominant emotions each day
- Triggers for each emotion
- How you typically respond
- How well that response works
- One alternative response to try

Building Emotional Intelligence:
- Recognize emotions in others
- Understand emotional needs
- Respond with empathy
- Set healthy emotional boundaries
- Express emotions clearly

Remember: Emotional regulation isn\'t perfection. It\'s noticing feelings, understanding them, and choosing responses aligned with your values.'''
            },
            {
                'id': 'breathing-001',
                'category': 'Breathing & Relaxation',
                'title': '2-Minute Calming Breath',
                'summary': 'A quick breathing cycle to settle racing thoughts.',
                'content': 'Inhale 4 seconds, exhale 6 seconds. Repeat for 2 minutes. Keep shoulders relaxed.'
            },
            {
                'id': 'grounding-001',
                'category': 'Grounding Techniques',
                'title': '5-4-3-2-1 Grounding',
                'summary': 'Reconnect with your senses during overwhelm.',
                'content': 'Name 5 things you see, 4 you feel, 3 you hear, 2 you smell, 1 you taste.'
            },
            {
                'id': 'stress-001',
                'category': 'Stress & Anxiety Guides',
                'title': 'Stress Response Reset',
                'summary': 'A short guide for reducing stress load in the moment.',
                'content': 'Pause, unclench jaw and shoulders, take 5 slower breaths, and pick one next safe action.'
            },
            {
                'id': 'selfcare-001',
                'category': 'Self-Care Routines',
                'title': 'Low-Energy Self-Care Plan',
                'summary': 'Tiny self-care steps for hard days.',
                'content': 'Drink water, wash face, open a window, and send one message to a trusted person.'
            },
            {
                'id': 'hotline-001',
                'category': 'Emergency Hotlines',
                'title': 'Crisis Contacts',
                'summary': 'Immediate support contacts for urgent emotional distress.',
                'content': 'US: Call/Text 988. Emergency danger: call 911. Add local campus and country hotline numbers in settings.'
            },
        ]
    }


@app.route('/user/cache-meta/<user_id>', methods=['GET'])
def user_cache_meta(user_id):
    """Provide per-user metadata revision for client-side cache validation."""
    try:
        return jsonify(_build_user_cache_meta(user_id))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/personalized-plan/<user_id>')
def personalized_plan_page(user_id):
    """Render personalized wellness plan page."""
    return render_template('personalized_plan.html')


@app.route('/offline-resources')
def offline_resources_page():
    """Render offline resources library page."""
    return render_template('offline_resources.html')


@app.route('/journal/<user_id>')
def journal_page(user_id):
    """Render personal journal page."""
    return render_template('journal.html')


@app.route('/user/wellness-plan/<user_id>', methods=['GET', 'PUT'])
def user_wellness_plan(user_id):
    """Get or update personalized wellness plan for a user."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500

    plan_ref = db.collection('wellness_plans').document(user_id)

    if request.method == 'GET':
        try:
            doc = plan_ref.get()
            if doc.exists:
                return jsonify(doc.to_dict() or {})

            plan = _generate_wellness_plan(user_id)
            plan_ref.set(plan)
            return jsonify(plan)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    data = request.get_json() or {}
    allowed_fields = {
        'status', 'cadence', 'goals', 'reflections', 'copingExercises',
        'notes', 'preferences', 'isPaused'
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if 'status' in updates and updates['status'] not in ('active', 'paused'):
        return jsonify({'error': 'Invalid status. Use active or paused.'}), 400

    if not updates:
        return jsonify({'error': 'No valid fields provided'}), 400

    updates['updatedAt'] = datetime.now().isoformat()

    try:
        plan_ref.set(updates, merge=True)
        return jsonify({'success': True, 'updatedAt': updates['updatedAt']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/user/preferences/<user_id>', methods=['GET', 'PUT'])
def user_preferences(user_id):
    """Get or update simple user preferences (e.g. response_length)."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500

    prof_ref = db.collection('user_profiles').document(user_id)

    if request.method == 'GET':
        try:
            doc = prof_ref.get()
            if doc.exists:
                data = doc.to_dict() or {}
                prefs = data.get('preferences', {})
                # Backwards-compatible: allow top-level response_length
                if 'response_length' in data and 'response_length' not in prefs:
                    prefs['response_length'] = data.get('response_length')
                return jsonify(prefs)
            return jsonify({}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # PUT -> update allowed preference keys
    data = request.get_json() or {}
    allowed = {'response_length'}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'No valid preference provided'}), 400

    try:
        # Merge into preferences field
        prof_ref.set({'preferences': updates}, merge=True)
        return jsonify({'success': True, 'preferences': updates})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/user/wellness-plan/<user_id>/refresh', methods=['POST'])
def refresh_user_wellness_plan(user_id):
    """Regenerate recommendation content using latest emotional trends."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    try:
        new_plan = _generate_wellness_plan(user_id)
        old_doc = db.collection('wellness_plans').document(user_id).get()
        if old_doc.exists:
            old_plan = old_doc.to_dict() or {}
            # Preserve status and user-modified preferences.
            if old_plan.get('status') in ('active', 'paused'):
                new_plan['status'] = old_plan.get('status')
            if 'preferences' in old_plan:
                new_plan['preferences'] = old_plan.get('preferences')
        db.collection('wellness_plans').document(user_id).set(new_plan, merge=True)
        return jsonify({'success': True, 'plan': new_plan})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/offline-resources/data', methods=['GET'])
def offline_resources_data():
    """Return offline resource library payload (cacheable by client)."""
    try:
        payload = _default_offline_resources()
        if db:
            cfg_doc = db.collection('app_content').document('offline_resources').get()
            if cfg_doc.exists:
                cfg = cfg_doc.to_dict() or {}
                if cfg.get('items'):
                    payload = {
                        'version': cfg.get('version', payload['version']),
                        'title': cfg.get('title', payload['title']),
                        'items': cfg.get('items', payload['items'])
                    }
        return jsonify(payload)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/user/journal/<user_id>', methods=['GET', 'POST'])
def user_journal_entries(user_id):
    """Get or create private journal entries."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500

    if request.method == 'GET':
        try:
            entries = []
            for doc in db.collection('journal_entries').where('userId', '==', user_id).stream():
                item = doc.to_dict() or {}
                item['id'] = doc.id
                entries.append(item)
            entries.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
            return jsonify({'entries': entries})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    data = request.get_json() or {}
    content = (data.get('content') or '').strip()
    prompt = (data.get('prompt') or '').strip()
    mood = (data.get('mood') or '').strip()

    if not content:
        return jsonify({'error': 'Journal content is required'}), 400

    entry = {
        'userId': user_id,
        'prompt': prompt,
        'content': content,
        'mood': mood,
        'createdAt': datetime.now().isoformat(),
        'updatedAt': datetime.now().isoformat(),
    }
    try:
        doc_ref = db.collection('journal_entries').document()
        doc_ref.set(entry)
        entry['id'] = doc_ref.id
        return jsonify({'success': True, 'entry': entry}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/user/journal/<user_id>/<entry_id>', methods=['PUT', 'DELETE'])
def user_journal_entry_update(user_id, entry_id):
    """Update or delete a private journal entry."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500

    entry_ref = db.collection('journal_entries').document(entry_id)
    doc = entry_ref.get()
    if not doc.exists:
        return jsonify({'error': 'Entry not found'}), 404

    current = doc.to_dict() or {}
    if current.get('userId') != user_id:
        return jsonify({'error': 'Forbidden'}), 403

    if request.method == 'DELETE':
        try:
            entry_ref.delete()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    data = request.get_json() or {}
    updates = {}
    if 'content' in data:
        updates['content'] = (data.get('content') or '').strip()
    if 'prompt' in data:
        updates['prompt'] = (data.get('prompt') or '').strip()
    if 'mood' in data:
        updates['mood'] = (data.get('mood') or '').strip()

    if not updates.get('content'):
        return jsonify({'error': 'Content cannot be empty'}), 400

    updates['updatedAt'] = datetime.now().isoformat()
    try:
        entry_ref.set(updates, merge=True)
        return jsonify({'success': True, 'updatedAt': updates['updatedAt']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== INCOGNITO CHAT ROUTES ====================

@app.route('/incognito')
def incognito_page():
    """Render incognito (private) chat page."""
    return render_template('incognito.html')


@app.route('/incognito/cleanup', methods=['POST'])
def incognito_cleanup():
    """Delete all incognito-tagged conversations for a user session."""
    if not db:
        return jsonify({'success': True})
    try:
        data = request.json or {}
        user_id = data.get('user_id', '')
        session_id = data.get('session_id', '')
        if not user_id and not session_id:
            return jsonify({'success': True})

        query = db.collection('conversations').where('isIncognito', '==', True)
        if user_id:
            query = query.where('userId', '==', user_id)
        elif session_id:
            query = query.where('incognitoSession', '==', session_id)

        deleted = 0
        for conv_doc in query.stream():
            msgs_ref = db.collection('conversations').document(conv_doc.id).collection('messages')
            for msg_doc in msgs_ref.stream():
                msg_doc.reference.delete()
            conv_doc.reference.delete()
            deleted += 1

        # Also clean emotion_logs tagged as incognito for this user/session
        if user_id:
            for log_doc in db.collection('emotion_logs').where('userId', '==', user_id).where('isIncognito', '==', True).stream():
                log_doc.reference.delete()

        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        print(f'[incognito cleanup] Error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== USER PROFILE ROUTES ====================

@app.route('/profile')
def profile_page():
    """Render user profile page."""
    return render_template('profile.html')


@app.route('/user/profile/<user_id>', methods=['GET'])
def get_user_profile(user_id):
    """Return profile data stored in Firestore for a user."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    try:
        doc = db.collection('user_profiles').document(user_id).get()
        profile = doc.to_dict() if doc.exists else {}
        # Merge Firebase Auth data
        try:
            auth_user = firebase_auth.get_user(user_id)
            profile['displayName'] = profile.get('displayName') or auth_user.display_name or ''
            profile['email'] = auth_user.email or ''
            profile['photoURL'] = profile.get('photoURL') or auth_user.photo_url or ''
            profile['emailVerified'] = auth_user.email_verified
        except Exception:
            pass
        return jsonify(profile)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/user/profile/<user_id>', methods=['PUT'])
def update_user_profile(user_id):
    """Update profile fields stored in Firestore (displayName, birthday, photoURL)."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    data = request.get_json() or {}
    allowed = {'displayName', 'birthday', 'photoURL'}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400
    try:
        updates['updatedAt'] = datetime.now().isoformat()
        db.collection('user_profiles').document(user_id).set(updates, merge=True)

        # Also update Firebase Auth display name if provided
        if 'displayName' in updates:
            try:
                firebase_auth.update_user(user_id, display_name=updates['displayName'])
            except Exception as e:
                print(f'[profile] Auth display name update error: {e}')

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/user/data/<user_id>', methods=['GET'])
def get_user_data(user_id):
    """Return all personal data (conversations + messages) for a user."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    try:
        convs = []
        for conv_doc in db.collection('conversations').where('userId', '==', user_id).stream():
            conv = conv_doc.to_dict()
            conv['id'] = conv_doc.id
            # Fetch messages
            msgs = []
            for msg_doc in db.collection('conversations').document(conv_doc.id).collection('messages').order_by('timestamp').stream():
                msgs.append(msg_doc.to_dict())
            conv['messages'] = msgs
            convs.append(conv)
        # Sort by createdAt descending
        convs.sort(key=lambda c: c.get('createdAt', ''), reverse=True)
        return jsonify({'conversations': convs, 'totalConversations': len(convs)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/user/data/<user_id>/delete', methods=['DELETE'])
def delete_user_data(user_id):
    """Permanently delete ALL personal data for a user (conversations, messages, emotion_logs)."""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    try:
        deleted_convs = 0
        deleted_msgs = 0
        for conv_doc in db.collection('conversations').where('userId', '==', user_id).stream():
            msgs_ref = db.collection('conversations').document(conv_doc.id).collection('messages')
            for msg_doc in msgs_ref.stream():
                msg_doc.reference.delete()
                deleted_msgs += 1
            conv_doc.reference.delete()
            deleted_convs += 1
        # Delete emotion logs
        for log_doc in db.collection('emotion_logs').where('userId', '==', user_id).stream():
            log_doc.reference.delete()
        # Delete crisis alerts
        for alert_doc in db.collection('crisis_alerts').where('userId', '==', user_id).stream():
            alert_doc.reference.delete()
        # Clear in-memory history
        if user_id in conversation_history:
            conversation_history[user_id] = []
        return jsonify({'success': True, 'deletedConversations': deleted_convs, 'deletedMessages': deleted_msgs})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== ADMIN BACKUP ROUTES ====================

BACKUP_DOWNLOAD_PATH = os.getenv('BACKUP_DOWNLOAD_PATH', '')  # Optional local path override


def _collect_full_backup():
    """Collect all Firestore collections into a dict for backup."""
    if not db:
        return {}
    backup = {'exportedAt': datetime.now().isoformat(), 'collections': {}}
    collections_to_backup = ['conversations', 'emotion_logs', 'crisis_alerts', 'admins', 'user_profiles', 'backup_history']
    for col_name in collections_to_backup:
        try:
            docs = []
            for doc in db.collection(col_name).stream():
                d = doc.to_dict() or {}
                d['_docId'] = doc.id
                # Serialize any Firestore timestamps
                for k, v in list(d.items()):
                    if hasattr(v, 'isoformat'):
                        d[k] = v.isoformat()
                docs.append(d)
            backup['collections'][col_name] = docs
        except Exception as e:
            backup['collections'][col_name] = {'error': str(e)}
    return backup


def _record_backup_history(triggered_by='scheduler', backup_size=0):
    """Write a backup history entry to Firestore."""
    if not db:
        return
    try:
        db.collection('backup_history').document().set({
            'triggeredBy': triggered_by,
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'sizeBytes': backup_size,
            'status': 'success'
        })
    except Exception as e:
        print(f'[backup] Error recording history: {e}')


def _run_scheduled_backup():
    """Scheduled backup job — runs at 23:59 daily."""
    print('🕐 [Scheduled Backup] Running nightly backup...')
    try:
        data = _collect_full_backup()
        backup_json = json.dumps(data, indent=2, default=str)
        size = len(backup_json.encode('utf-8'))
        _record_backup_history(triggered_by='scheduler_23:59', backup_size=size)
        # If a local path is configured, write to disk
        if BACKUP_DOWNLOAD_PATH:
            import pathlib
            pathlib.Path(BACKUP_DOWNLOAD_PATH).mkdir(parents=True, exist_ok=True)
            fname = f"menti_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            fpath = os.path.join(BACKUP_DOWNLOAD_PATH, fname)
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(backup_json)
            print(f'✅ [Scheduled Backup] Saved to {fpath}')
        else:
            print(f'✅ [Scheduled Backup] Completed ({size} bytes). No local path configured — history recorded.')
    except Exception as e:
        print(f'❌ [Scheduled Backup] Error: {e}')
        if db:
            try:
                db.collection('backup_history').document().set({
                    'triggeredBy': 'scheduler_23:59',
                    'timestamp': datetime.now().isoformat(),
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'sizeBytes': 0,
                    'status': f'failed: {str(e)}'
                })
            except Exception:
                pass


# Start APScheduler — runs nightly backup at 23:59
_scheduler = BackgroundScheduler(timezone='UTC')
_scheduler.add_job(_run_scheduled_backup, 'cron', hour=23, minute=59, id='nightly_backup')
_scheduler.start()
atexit.register(lambda: _scheduler.shutdown(wait=False))


@app.route('/admin/backup/now', methods=['POST'])
@admin_required
def admin_backup_now():
    """Trigger an immediate backup and return JSON for browser download."""
    try:
        triggered_by = session.get('admin_username', 'admin')
        data = _collect_full_backup()
        backup_json = json.dumps(data, indent=2, default=str)
        size = len(backup_json.encode('utf-8'))
        _record_backup_history(triggered_by=triggered_by, backup_size=size)
        filename = f"menti_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            backup_json,
            mimetype='application/json',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(size)
            }
        )
    except Exception as e:
        print(f'[backup/now] Error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/backup/history')
@admin_required
def admin_backup_history():
    """Return backup history records."""
    if not db:
        return jsonify([])
    try:
        records = []
        for doc in db.collection('backup_history').stream():
            d = doc.to_dict()
            d['id'] = doc.id
            records.append(d)
        records.sort(key=lambda r: r.get('timestamp', ''), reverse=True)
        return jsonify(records[:100])  # latest 100
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== VOICE ROUTES (Speech-to-Text & Text-to-Speech) ====================

@app.route('/api/voice/transcribe', methods=['POST'])
def transcribe():
    """
    Transcribe audio to text using Vosk (offline, free, open-source)
    Expects: audio/wav file in request
    Returns: transcribed text
    """
    try:
        from voice_handler import transcribe_audio
        
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400
        
        audio_file = request.files['audio']
        audio_bytes = audio_file.read()
        
        result = transcribe_audio(audio_bytes)
        return jsonify(result)
    
    except Exception as e:
        print(f"❌ Transcription error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/voice/synthesize', methods=['POST'])
def synthesize():
    """
    Synthesize text to speech using open-source neural TTS voices.
    Expects: JSON with 'text' and optional 'voice_id' (0=default, 1=alternate if available)
    Returns: synthesized audio file
    """
    try:
        from voice_handler import synthesize_speech
        
        data = request.get_json(silent=True) or {}
        text = data.get('text', '').strip()
        voice_id = data.get('voice_id', 0)
        
        if not text:
            return jsonify({'error': 'Text is required'}), 400
        
        result = synthesize_speech(text, voice_id)
        
        if result['success']:
            audio_file = result['audio_file']
            mimetype = result.get('mimetype', 'audio/wav')
            try:
                with open(audio_file, 'rb') as f:
                    audio_data = f.read()
                try:
                    os.unlink(audio_file)
                except Exception:
                    pass
                return Response(audio_data, mimetype=mimetype)
            except Exception as e:
                return jsonify({'error': f'Could not read audio file: {str(e)}'}), 500
        else:
            return jsonify({'error': result.get('error', 'Synthesis failed')}), 500
    
    except Exception as e:
        print(f"❌ Synthesis error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/voice/voices', methods=['GET'])
def get_voices():
    """Get list of available voices and languages"""
    try:
        from voice_handler import get_available_voices
        voices_data = get_available_voices()
        return jsonify(voices_data)
    except Exception as e:
        print(f"❌ Error getting voices: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== RUN APPLICATION ====================

if __name__ == '__main__':
    print("🚀 Starting Menti Chatbot Server...")
    print(f"🤖 Groq: {'✅ Configured' if os.getenv('GROQ_API_KEY') else '❌ Missing'}")
    print(f"🔥 Firebase: {'✅ Connected' if db else '⚠️  Not connected'}")
    app.run(debug=True, host='0.0.0.0', port=5000)

