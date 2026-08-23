import os
import sys
import json

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti

orig = menti.groq_chat_create
current_usage = []

def wrapped_groq_chat_create(**kwargs):
    resp = orig(**kwargs)
    usage = getattr(resp, 'usage', None) if resp is not None else None
    item = {
        'model': kwargs.get('model'),
        'max_tokens': kwargs.get('max_tokens'),
        'usage_prompt_tokens': None,
        'usage_completion_tokens': None,
        'usage_total_tokens': None,
    }
    if usage is not None:
        item['usage_prompt_tokens'] = getattr(usage, 'prompt_tokens', None)
        item['usage_completion_tokens'] = getattr(usage, 'completion_tokens', None)
        item['usage_total_tokens'] = getattr(usage, 'total_tokens', None)
    current_usage.append(item)
    return resp

menti.groq_chat_create = wrapped_groq_chat_create

cases = [
    ('friendly', 'short'),
    ('supportive', 'short'),
    ('professional', 'short'),
    ('friendly', 'detailed'),
    ('supportive', 'detailed'),
    ('professional', 'detailed'),
]

inputs = [
    ('input1', 'i want to cry'),
    ('input2', 'i dont want to be rude but i want to punch the face of my classmates bullying me. it angers me that they are targeting me.'),
    ('input3', 'please help me i think i am about to die. i have literally no one to talk to and i am going crazy i want to end myself'),
    ('input4', 'i need medical help'),
]

client = menti.app.test_client()
results = []

for input_name, message in inputs:
    for mode, length in cases:
        current_usage.clear()
        user_id = f"matrix_{input_name}_{mode}_{length}"
        r = client.post('/chat', json={
            'message': message,
            'user_id': user_id,
            'is_guest': True,
            'mode': mode,
            'response_length': length,
        })
        data = r.get_json(silent=True) or {}

        prompt_sum = 0
        completion_sum = 0
        total_sum = 0
        calls = 0
        for u in current_usage:
            pt = u.get('usage_prompt_tokens')
            ct = u.get('usage_completion_tokens')
            tt = u.get('usage_total_tokens')
            if isinstance(pt, int):
                prompt_sum += pt
            if isinstance(ct, int):
                completion_sum += ct
            if isinstance(tt, int):
                total_sum += tt
            calls += 1

        results.append({
            'input': input_name,
            'mode': mode,
            'length': length,
            'status': r.status_code,
            'reply': data.get('reply') or data.get('error') or '',
            'emotion': data.get('emotion'),
            'token_prompt_sum': prompt_sum,
            'token_completion_sum': completion_sum,
            'token_total_sum': total_sum,
            'groq_call_count': calls,
            'raw': data,
        })

print(json.dumps(results, ensure_ascii=True, indent=2))
