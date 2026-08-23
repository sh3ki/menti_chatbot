import os
import sys
import json
import io
import contextlib

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti

orig = menti.groq_chat_create
call_usage = []

def wrapped(**kwargs):
    resp = orig(**kwargs)
    usage = getattr(resp, 'usage', None) if resp is not None else None
    call_usage.append({
        'prompt_tokens': getattr(usage, 'prompt_tokens', None) if usage else None,
        'completion_tokens': getattr(usage, 'completion_tokens', None) if usage else None,
        'total_tokens': getattr(usage, 'total_tokens', None) if usage else None,
    })
    return resp

menti.groq_chat_create = wrapped

inputs = [
    ('input1', 'i want to cry'),
    ('input2', 'i dont want to be rude but i want to punch the face of my classmates bullying me. it angers me that they are targeting me.'),
    ('input3', 'please help me i think i am about to die. i have literally no one to talk to and i am going crazy i want to end myself'),
    ('input4', 'i need medical help'),
]

cases = [
    ('friendly', 'short'),
    ('supportive', 'short'),
    ('professional', 'short'),
    ('friendly', 'detailed'),
    ('supportive', 'detailed'),
    ('professional', 'detailed'),
]

client = menti.app.test_client()
results = []

for input_name, message in inputs:
    for mode, length in cases:
        call_usage.clear()
        user_id = f"matrix_{input_name}_{mode}_{length}"

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            r = client.post('/chat', json={
                'message': message,
                'user_id': user_id,
                'is_guest': True,
                'mode': mode,
                'response_length': length,
            })

        data = r.get_json(silent=True) or {}

        pt = sum(x['prompt_tokens'] for x in call_usage if isinstance(x.get('prompt_tokens'), int))
        ct = sum(x['completion_tokens'] for x in call_usage if isinstance(x.get('completion_tokens'), int))
        tt = sum(x['total_tokens'] for x in call_usage if isinstance(x.get('total_tokens'), int))

        results.append({
            'input': input_name,
            'message': message,
            'mode': mode,
            'response_length': length,
            'status': r.status_code,
            'emotion': data.get('emotion'),
            'reply': data.get('reply') or data.get('error') or '',
            'prompt_tokens': pt,
            'completion_tokens': ct,
            'total_tokens': tt,
            'api_calls': len(call_usage),
        })

output_path = '_matrix_4inputs_6cases.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f'WROTE {output_path} with {len(results)} rows')
