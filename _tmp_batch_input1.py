import os, sys, json, io, contextlib
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import app as menti

orig = menti.groq_chat_create
current_usage = []

def wrapped(**kwargs):
    resp = orig(**kwargs)
    usage = getattr(resp, 'usage', None) if resp is not None else None
    current_usage.append({
        'pt': getattr(usage, 'prompt_tokens', None) if usage else None,
        'ct': getattr(usage, 'completion_tokens', None) if usage else None,
        'tt': getattr(usage, 'total_tokens', None) if usage else None,
    })
    return resp

menti.groq_chat_create = wrapped

cases = [
    ('friendly','short'),('supportive','short'),('professional','short'),
    ('friendly','detailed'),('supportive','detailed'),('professional','detailed')
]
msg = 'i want to cry'
client = menti.app.test_client()
results = []
for mode, length in cases:
    current_usage.clear()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        r = client.post('/chat', json={
            'message': msg,
            'user_id': f'batch_input1_{mode}_{length}',
            'is_guest': True,
            'mode': mode,
            'response_length': length,
        })
    data = r.get_json(silent=True) or {}
    pt = sum(x['pt'] for x in current_usage if isinstance(x.get('pt'), int))
    ct = sum(x['ct'] for x in current_usage if isinstance(x.get('ct'), int))
    tt = sum(x['tt'] for x in current_usage if isinstance(x.get('tt'), int))
    results.append({
        'input':'input1','mode':mode,'length':length,'status':r.status_code,
        'reply': data.get('reply') or data.get('error') or '',
        'emotion': data.get('emotion'),
        'prompt_tokens': pt,'completion_tokens': ct,'total_tokens': tt,
    })

with open('_matrix_input1.json','w',encoding='utf-8') as f:
    json.dump(results,f,ensure_ascii=False,indent=2)
print('WROTE _matrix_input1.json')
