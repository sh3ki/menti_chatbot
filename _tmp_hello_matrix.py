import os
import sys
import json

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as menti

original = menti.groq_chat_create
client = menti.app.test_client()

cases = [
    ("friendly short", "friendly", "short"),
    ("supportive short", "supportive", "short"),
    ("professional short", "professional", "short"),
    ("friendly detailed", "friendly", "detailed"),
    ("supportive detailed", "supportive", "detailed"),
    ("professional detailed", "professional", "detailed"),
]

msg = "hello"

print("=== 6-CASE MATRIX (INPUT: hello) ===")
print("input:", repr(msg))

for label, mode, length in cases:
    usage_rows = []

    def wrapped_groq_chat_create(**kwargs):
        resp = original(**kwargs)
        usage = getattr(resp, "usage", None) if resp is not None else None
        usage_rows.append({
            "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
            "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
            "had_response": resp is not None,
        })
        return resp

    menti.groq_chat_create = wrapped_groq_chat_create

    payload = {
        "message": msg,
        "user_id": f"hello_{mode}_{length}",
        "is_guest": True,
        "mode": mode,
        "response_length": length,
    }

    res = client.post('/chat', json=payload)
    body = res.get_json(silent=True) or {}

    p = sum((r["prompt_tokens"] or 0) for r in usage_rows)
    c = sum((r["completion_tokens"] or 0) for r in usage_rows)
    t = sum((r["total_tokens"] or 0) for r in usage_rows)

    print("\n---", label, "---")
    print("status:", res.status_code)
    print("emotion:", body.get("emotion"))
    print("reply_repr:", repr(body.get("reply")))
    print("tokens:", json.dumps({"input": p, "output": c, "total": t}))

menti.groq_chat_create = original
print("\n=== END ===")
