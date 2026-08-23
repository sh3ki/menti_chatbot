import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti

# Patch to debug
orig_gen = menti.generate_supportive_response
def debug_gen(**kwargs):
    result = orig_gen(**kwargs)
    analysis = kwargs.get('analysis') or {}
    emotion_reason = analysis.get('emotion_reason', '')
    risk_reason = analysis.get('risk_reason', '')
    emotion = kwargs.get('emotion', 'calm')
    risk_type = analysis.get('risk_type', 'none')
    follow_up_needed = bool(analysis.get('follow_up_needed', False))
    follow_up_question = (analysis.get('follow_up_question') or '').strip()
    
    has_clear_emotion = bool(emotion_reason and emotion != 'calm')
    has_specific_reason = (
        (risk_reason and len(risk_reason) > 10 and risk_type != 'none') or  
        (emotion_reason and len(emotion_reason) > 15 and 'indicate' not in emotion_reason.lower())  
    )
    needs_clarification = (
        follow_up_needed 
        and follow_up_question 
        and not (has_clear_emotion and has_specific_reason)
    )
    
    print(f"\n[DEBUG] emotion_reason={emotion_reason[:40]}")
    print(f"[DEBUG] risk_reason={risk_reason[:40]}")
    print(f"[DEBUG] emotion={emotion}")
    print(f"[DEBUG] has_clear_emotion={has_clear_emotion}, has_specific_reason={has_specific_reason}")
    print(f"[DEBUG] needs_clarification={needs_clarification}")
    return result

menti.generate_supportive_response = debug_gen

client = menti.app.test_client()
print("\nTEST 2: Unclear context - should ask follow-up")
r = client.post('/chat', json={
    'message': 'i am not doing well',
    'user_id': 'test_debug',
    'is_guest': True,
    'mode': 'supportive',
    'response_length': 'short'
})
data = r.get_json(silent=True) or {}
print(f"Reply: {data.get('reply')}")
