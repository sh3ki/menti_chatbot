import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti
client = menti.app.test_client()

print("Testing with word-based constraints instead of max_tokens...")
print("=" * 60)

r = client.post('/chat', json={
    'message': 'i am sad. i dont know what to do anymore.',
    'user_id': 'test_word_constraints',
    'is_guest': True,
    'mode': 'friendly',
    'response_length': 'short'
})

data = r.get_json(silent=True) or {}
print('STATUS:', r.status_code)
print('EMOTION:', data.get('emotion'))
print('REPLY:', repr(data.get('reply') or data.get('error')))
print('REPLY LENGTH:', len(data.get('reply', '')))
print('=' * 60)
