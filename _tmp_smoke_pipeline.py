import os, sys, json
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import app as menti

calls = []
orig = menti.groq_chat_create

def wrapped(**kwargs):
    calls.append({'max_tokens': kwargs.get('max_tokens'), 'temperature': kwargs.get('temperature')})
    return orig(**kwargs)

menti.groq_chat_create = wrapped
client = menti.app.test_client()
r = client.post('/chat', json={
    'message': 'I feel overwhelmed and tired lately, but I keep saying I am fine.',
    'user_id': 'smoke_user',
    'is_guest': True,
    'mode': 'supportive',
    'response_length': 'short'
})
print('STATUS', r.status_code)
try:
    data = r.get_json() or {}
except Exception:
    data = {}
print('EMOTION', data.get('emotion'))
print('REPLY_LEN', len((data.get('reply') or '')))
print('API_CALLS', len(calls))
print('HAS_REPLY', bool(data.get('reply')))
