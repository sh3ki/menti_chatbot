import os, sys, json
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import app as menti
c = menti.app.test_client()
inputs = [
    "i want to cry",
    "i dont want to be rude but i want to punch the face of my classmates bullying me. it angers me that they are targeting me.",
    "please help me i think i am about to die. i have literally no one to talk to and i am going crazy i want to end myself",
    "i need medical help"
]
configs = [
    ('friendly','short'),
    ('supportive','short'),
    ('professional','short'),
    ('friendly','detailed'),
    ('supportive','detailed'),
    ('professional','detailed'),
]
results = []
count=0
for i,msg in enumerate(inputs, start=1):
    for mode,length in configs:
        count+=1
        tag = f"INPUT{i}-{mode}-{length}"
        print('\n---TEST START '+tag+'---')
        r = c.post('/chat', json={
            'message': msg,
            'user_id': f'test_user_{count}',
            'is_guest': True,
            'mode': mode,
            'response_length': length
        })
        body = r.get_json(silent=True)
        print('STATUS:', r.status_code)
        print('BODY:', json.dumps(body, ensure_ascii=False))
        print('---TEST END '+tag+'---')
        results.append((tag, r.status_code, body))

print('\n=== SUMMARY ===')
for tag, status, body in results:
    reply = body.get('reply') if isinstance(body, dict) else None
    print(f"{tag} | {status} | reply={repr(reply)[:160]}")
