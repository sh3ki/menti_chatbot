import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti
client = menti.app.test_client()
r = client.post('/chat', json={
    'message': 'i am sad. i dont know what to do anymore.',
    'user_id': 'test_balanced_tokens',
    'is_guest': True,
    'mode': 'friendly',
    'response_length': 'short'
})

data = r.get_json(silent=True) or {}
print('=' * 50)
print('STATUS:', r.status_code)
print('EMOTION:', data.get('emotion'))
print('REPLY:', data.get('reply') or data.get('error'))
print('=' * 50)
