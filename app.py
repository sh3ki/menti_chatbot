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

# Initialize Flask app
app = Flask(__name__, 
            static_folder='assets',
            static_url_path='/static')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
CORS(app)

# In-memory conversation storage (use Redis/database for production)
conversation_history = {}

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
if not groq_api_key:
    print("❌ ERROR: GROQ_API_KEY not found in environment variables!")
    print("Please add GROQ_API_KEY to your .env file")
else:
    groq_client = Groq(api_key=groq_api_key)
    print("✅ Groq client initialized successfully")

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
        
        # Step 2a: Detect emotion
        emotion = detect_emotion(user_message)
        print(f"😊 Detected emotion: {emotion}")

        # Step 2b: Detect emotional masking / avoidance
        is_masking = detect_emotional_masking(user_message)
        if is_masking:
            print(f"🎭 Emotional masking flag raised — will probe gently")

        # Step 2c: Detect crisis signals (self-harm, suicide, homicide, medical, abuse)
        is_crisis, crisis_type, crisis_severity = detect_crisis(user_message)
        if is_crisis:
            print(f"🚨 CRISIS DETECTED: {crisis_type} [{crisis_severity}]")
            log_crisis_alert(user_id, user_message, crisis_type, crisis_severity, emotion, mode, is_anonymous=is_guest)
        
        # Step 3: Generate supportive response with conversation context
        bot_reply = generate_supportive_response(user_message, emotion, user_id, is_masking=is_masking, mode=mode, is_crisis=is_crisis, crisis_type=crisis_type)
        
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

def detect_emotion(message):
    """
    Detect emotion from user message using Groq.
    Returns one of 10 emotion categories:
    happy, calm, sad, anxious, stressed, angry, confused, motivated, tired, numb
    """
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": """Classify the emotion in this message into ONE word only.
Choices: happy, calm, sad, anxious, stressed, angry, confused, motivated, tired, numb
Rules: pick the best match, never default to calm/numb unless clearly indicated, reply with ONE word."""
                },
                {
                    "role": "user",
                    "content": message
                }
            ],
            max_tokens=5,
            temperature=0.1
        )
        
        emotion = response.choices[0].message.content.strip().lower()
        
        # Validate emotion — must be exactly one of the 10
        valid_emotions = ['happy', 'calm', 'sad', 'anxious', 'stressed', 'angry', 'confused', 'motivated', 'tired', 'numb']
        if emotion not in valid_emotions:
            # Try partial match for minor model variance (e.g. 'calmness' -> 'calm')
            matched = next((e for e in valid_emotions if e in emotion), None)
            emotion = matched if matched else 'calm'
        
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

    # Phase 2 — AI check for subtle masking (only for short messages)
    if len(message.split()) <= 25:
        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": "Is this message emotional masking or avoidance? (e.g. 'I'm fine', 'never mind', downplaying real distress, deflecting). Answer YES or NO only."
                    },
                    {
                        "role": "user",
                        "content": message
                    }
                ],
                max_tokens=3,
                temperature=0.1
            )
            answer = response.choices[0].message.content.strip().upper()
            is_masking = answer.startswith('YES')
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

    # --- Phase 1: Regex patterns ---
    suicide_patterns = [
        r'\bkill myself\b', r'\bend my life\b', r'\bwant to die\b', r'\bwanna die\b',
        r'\bsuicide\b', r'\bsuicidal\b', r'\btake my (own )?life\b', r'\bnot want to (be here|live|exist)\b',
        r'\bdon\'?t want to live\b', r'\bno reason to live\b', r'\bbetter off dead\b',
        r'\bthinking of (ending|killing)\b', r'\bplan to kill\b', r"\bi('m| am) going to kill myself\b",
    ]
    self_harm_patterns = [
        r'\bcut(ting)? (myself|me)\b', r'\bhurt(ing)? (myself|me)\b', r'\bself.?harm\b',
        r'\bburning? (myself|my skin)\b', r'\bscratch(ing)? (myself|my skin)\b',
        r'\bblood(ing)?\b.{0,30}\bmyself\b', r'\bpunch(ing)? (myself|a wall|the wall)\b',
    ]
    homicide_patterns = [
        r'\bkill (someone|them|him|her|people)\b', r'\bmurder\b', r'\bwant to hurt (someone|them|him|her)\b',
        r'\bgoing to (hurt|attack|stab|shoot)\b', r'\bhomicid\b',
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
    ]

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

    # --- Phase 2: AI check for subtle messages (only if message < 60 words) ---
    word_count = len(text.split())
    if groq_client and word_count < 60:
        try:
            resp = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": (
                        "You are a crisis detection system. Analyze the message for crisis signals: "
                        "suicidal ideation, self-harm intent, homicidal thoughts, medical emergency, or active abuse. "
                        "Reply with EXACTLY one of: SUICIDE / SELF_HARM / HOMICIDE / MEDICAL / ABUSE / NONE. "
                        "Only flag explicit/clear intent — not vague emotional distress."
                    )},
                    {"role": "user", "content": message}
                ],
                max_tokens=5,
                temperature=0.0
            )
            answer = resp.choices[0].message.content.strip().upper().split()[0]
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


def generate_supportive_response(message, emotion, user_id, is_masking=False, mode='friendly', is_crisis=False, crisis_type=None):
    """
    Generate a response shaped entirely by the selected mode.
    Three distinct system prompts — mode drives everything.
    Uses Listen → Empathize → Guide within each mode's own style.
    """
    try:
        # Short emotion hints — injected into the mode prompts (token-efficient)
        emotion_hints = {
            'happy':     'User is joyful/positive. Celebrate with them warmly.',
            'calm':      'User is peaceful/stable. Honor it gently, no disruption.',
            'sad':       'User is hurting/grieving. Sit with them — do NOT rush to fix.',
            'anxious':   'User is worried/fearful. Ground them calmly, validate the fear.',
            'stressed':  'User is overwhelmed. Acknowledge the weight, offer small steps.',
            'angry':     'User is angry/frustrated. Validate it, explore what is underneath.',
            'confused':  'User is lost/uncertain. Be steady, help untangle one thing.',
            'motivated': 'User is hopeful/driven. Match energy, celebrate and direct it.',
            'tired':     'User is drained/exhausted. Lead with compassion, encourage rest.',
            'numb':      'User feels empty/disconnected. Be still, no pressure, just presence.',
        }

        # Masking note — compact, only injected when needed
        masking_note = ""
        if is_masking:
            masking_note = (
                "\nMASKING DETECTED: User seems to be brushing off real feelings "
                "(e.g. 'I'm fine', 'It's okay'). Don't accept it at face value. "
                "Gently acknowledge their words AND create a soft opening for them to share more. "
                "No pressure — just a warm invitation.\n"
            )

        emotion_hint = emotion_hints.get(emotion, '')
        active_emotion = 'masked' if is_masking else emotion

        # ================================================================
        # CRISIS OVERRIDE — fires for all modes when crisis detected
        # ================================================================
        crisis_type_labels = {
            'suicide':   'suicidal ideation',
            'self_harm': 'self-harm',
            'homicide':  'homicidal thoughts',
            'medical':   'medical emergency',
            'abuse':     'abuse/assault',
        }
        if is_crisis and crisis_type:
            crisis_label = crisis_type_labels.get(crisis_type, 'crisis situation')
            if mode == 'friendly':
                system_prompt = f"""You are Menti — a warm, caring best friend. The user has expressed {crisis_label}.
YOUR MOST IMPORTANT JOB RIGHT NOW: Make them feel heard and safe — then gently connect them to real help.
STEPS:
1. Acknowledge their pain with deep warmth — 1 short sentence. No minimizing.
2. Tell them you're really glad they shared this — they don't have to face it alone.
3. Gently encourage them to reach out to the 988 Suicide & Crisis Lifeline (call or text 988 in the US). For medical emergencies, suggest calling 911.
4. Ask one soft, caring question to keep them talking to you.
TONE: Gentle, warm, zero judgment. Short — 3-4 sentences. No lists. Like a caring friend who truly cares."""
            elif mode == 'supportive':
                system_prompt = f"""You are Menti — an empowering, supportive companion. The user has expressed {crisis_label}.
YOUR PRIORITY: Respond with strength and care — make them feel genuinely supported, then connect them to professional help.
STEPS:
1. Validate their courage in sharing — it takes strength. 1 sentence.
2. Affirm that they matter and this is serious — 1 sentence.
3. Encourage them to contact the 988 Suicide & Crisis Lifeline (call or text 988). For medical emergencies, recommend calling 911 immediately.
4. One empowering question to keep them engaged.
TONE: Warm, motivational, caring. 3-4 sentences. No lists. No clinical coldness."""
            else:  # professional
                system_prompt = f"""You are Menti — a calm, counselor-informed mental health companion. The user has expressed {crisis_label}.
CLINICAL PRIORITY: Acknowledge, validate, safety-plan, and connect to immediate resources.
STRUCTURE:
1. Reflect what they shared with clinical empathy — no minimizing, no dismissal.
2. Normalize reaching out while underscoring the seriousness.
3. Provide the 988 Suicide & Crisis Lifeline (call or text 988 in the US) as the immediate resource. For imminent danger or medical emergency, recommend calling 911.
4. If relevant, mention that professional support (therapist, counselor) can provide ongoing care.
5. Ask one open-ended question to maintain connection and assess their immediate safety.
TONE: Warm, measured, professional. 5-6 sentences. Structured paragraphs. No bullet lists."""

            response_max_tokens = 220

            messages = [{"role": "system", "content": system_prompt}]
            if user_id in conversation_history and conversation_history[user_id]:
                messages.extend(conversation_history[user_id][-4:])
            else:
                messages.append({"role": "user", "content": message})

            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=response_max_tokens,
                temperature=0.65
            )
            bot_reply = response.choices[0].message.content.strip()
            print(f"🚨 [CRISIS-{mode}] Response: {bot_reply[:80]}...")
            return bot_reply

        # ================================================================
        # THREE FULLY SEPARATE MODE PROMPTS — mode-first, lean, distinct
        # ================================================================

        if mode == 'friendly':
            system_prompt = f"""You are Menti — a warm, caring best friend who genuinely listens and makes people feel better just by being there.
Emotion detected: {active_emotion}. {emotion_hint}{masking_note}
APPROACH — follow this order every time:
1. LISTEN: Open with 1 sentence showing you truly heard them. Use their own words back. ("ugh, rejection really does sting...", "that sounds so exhausting...")
2. EMPATHIZE: 1-2 sentences of genuine warmth and comfort. Make them feel less alone. Be human, not clinical.
3. GUIDE: 1 gentle, casual suggestion or reframe — nothing overwhelming. Keep it light.
4. End with ONE soft, curious question — show you actually want to know more.
TONE RULES:
- Casual bestie texting style. Contractions. Real words. Zero jargon.
- Warm and cozy — like a hug through a message.
- NO bullet points. NO lists. NO therapy-speak.
- Total length: 3-4 sentences max. Short but full of heart.
- If masking: acknowledge their words warmly, then gently leave a door open.
Safety: If self-harm/suicide mentioned, respond with care and share 988 crisis line."""

            response_max_tokens = 110

        elif mode == 'supportive':
            system_prompt = f"""You are Menti — an encouraging, empowering companion who makes people feel capable and understood.
Emotion detected: {active_emotion}. {emotion_hint}{masking_note}
APPROACH — follow this order every time:
1. LISTEN: 1 sentence acknowledging exactly what they shared — show you heard every word.
2. EMPATHIZE: 1 sentence of strong, genuine validation. Make them feel seen and NOT alone.
3. GUIDE: 1 sentence affirming their strength + ONE clear, hopeful, actionable suggestion.
4. End with 1 uplifting question that builds their confidence and invites reflection.
TONE RULES:
- Warm, motivational, like a supportive coach who believes in them completely.
- NO bullet points. NO lists. Flowing sentences only.
- Total length: 3-4 sentences. Meaningful but concise.
- If masking: validate warmly, then gently invite them to share what's really going on.
Safety: If self-harm/suicide mentioned, respond with deep care and share 988 crisis line."""

            response_max_tokens = 130

        else:  # professional
            system_prompt = f"""You are Menti — a calm, knowledgeable mental health companion who speaks in a counselor-informed style.
Emotion detected: {active_emotion}. {emotion_hint}{masking_note}
APPROACH — follow this structure strictly every time:
1. LISTEN: Acknowledge what they've shared with precise, empathetic language.
2. EMPATHIZE: Validate their feelings and normalize their experience without minimizing.
3. GUIDE: Reframe or provide brief psychoeducation, then name ONE specific, evidence-based coping strategy (e.g. box breathing, cognitive reframing, behavioral activation, grounding 5-4-3-2-1).
4. If warranted, gently recommend professional support.
5. End with a reflective, open-ended question that promotes self-awareness.
TONE RULES:
- Formal but warm. Measured and calm. No slang or casualness.
- NO bullet points. NO lists. Structured flowing paragraphs.
- Total length: 5-6 sentences. Thorough but not overwhelming.
- If masking: acknowledge professionally then invite deeper reflection.
Safety: If self-harm/suicide mentioned, respond with clinical care and provide 988 Suicide & Crisis Lifeline."""

            response_max_tokens = 250

        # Build messages: system prompt + trimmed conversation history
        messages = [{"role": "system", "content": system_prompt}]

        if user_id in conversation_history and conversation_history[user_id]:
            # Use only last 8 messages (4 exchanges) to minimize tokens
            trimmed = conversation_history[user_id][-8:]
            messages.extend(trimmed)
            print(f"📝 History: {len(trimmed)} msgs | Mode: {mode} | max_tokens: {response_max_tokens}")
        else:
            messages.append({"role": "user", "content": message})

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=response_max_tokens,
            temperature=0.75
        )

        bot_reply = response.choices[0].message.content.strip()
        print(f"✅ [{mode}] Response: {bot_reply[:80]}...")
        return bot_reply

    except Exception as e:
        print(f"Error generating response: {e}")
        return "I'm here for you. Could you tell me more about what's on your mind?"


def generate_smart_title(user_message):
    """
    Generate a smart, concise title for a conversation based on the user's first message
    Uses Groq to create an intelligent summary (3-6 words)
    """
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": """You are a title generator for mental health conversations. Create a short, clear, and empathetic title (3-6 words) that captures the essence of the user's concern or feeling.

Rules:
- 3-6 words maximum
- Capture the main topic or emotion
- Be empathetic and understanding
- Use clear, simple language
- NO quotes, NO punctuation at the end
- Start with capital letter

Examples:
User: "I've been feeling really anxious lately about work and can't sleep"
Title: Anxiety About Work and Sleep

User: "My relationship ended and I don't know how to move on"
Title: Coping With Relationship Ending

User: "I feel so alone even when I'm with people"
Title: Feeling Isolated Around Others

User: "How do I deal with stress from school?"
Title: Managing School Stress

Generate ONLY the title, nothing else."""
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            max_tokens=20,
            temperature=0.7
        )
        
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
        'version': '2026-03-13',
        'title': 'Offline Mental Health Resource Library',
        'items': [
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
            }
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

