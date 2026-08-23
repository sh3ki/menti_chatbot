import os, sys, json
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti

client = menti.app.test_client()
user_id = 'new_conv_prof_short_test_001'
message = "i am sad. i don't know what to do anymore."

resp = client.post('/chat', json={
    'message': message,
    'user_id': user_id,
    'is_guest': True,
    'mode': 'professional',
    'response_length': 'short'
})

data = resp.get_json(silent=True) or {}
print('STATUS:', resp.status_code)
print('EMOTION:', data.get('emotion'))
print('REPLY:', data.get('reply') or data.get('error'))
print('TIMESTAMP:', data.get('timestamp'))
