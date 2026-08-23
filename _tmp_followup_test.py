import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti
client = menti.app.test_client()

tests = [
    ('clear_context', 'i am feeling very sad and lonely because my best friend moved away', 'short', 'CLEAR CONTEXT - should NOT follow-up'),
    ('unclear_context', 'i am not doing well', 'short', 'UNCLEAR CONTEXT - MAY follow-up if needed'),
    ('crisis', 'i want to kill myself', 'short', 'CRISIS - should NEVER follow-up'),
    ('clear_detailed', 'i am stressed about my exams. i have studied hard but i am afraid i will fail', 'detailed', 'CLEAR DETAILED - should NOT follow-up'),
]

for test_id, msg, length, desc in tests:
    print(f"\n{'='*70}")
    print(f"TEST: {desc}")
    print(f"{'='*70}")
    r = client.post('/chat', json={
        'message': msg,
        'user_id': f'test_{test_id}',
        'is_guest': True,
        'mode': 'supportive',
        'response_length': length,
    })
    data = r.get_json(silent=True) or {}
    reply = data.get('reply', '').strip()
    print(f"Message: {msg}")
    print(f"\nReply:\n{reply}")
    
    # Check if follow-up question is in reply (question mark indicates follow-up)
    has_question = '?' in reply
    print(f"\nHas follow-up question: {has_question}")
