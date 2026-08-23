"""
Test Groq model chat capability with your GROQ_API_KEY and GROQ_MODEL.
Run:
    python tools/test_groq_model.py

It sends a single-user message (many Groq text-classifier models require exactly one user message).
Prints full SDK response and attempts to extract text if available.
"""
import os
import json
import traceback
from dotenv import load_dotenv

load_dotenv()
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
GROQ_MODEL = os.getenv('GROQ_MODEL')

if not GROQ_API_KEY or not GROQ_MODEL:
    print('Please set GROQ_API_KEY and GROQ_MODEL in your .env file')
    raise SystemExit(1)

print('Using model:', GROQ_MODEL)

try:
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
except Exception as e:
    print('Failed to import or init Groq SDK:', e)
    traceback.print_exc()
    raise SystemExit(1)

# Single user message payload (classification-friendly)
messages = [{"role": "user", "content": "Hello Menti — please respond briefly so I can test if this model can chat."}]

try:
    print('\nSending chat-completions.create request (single user message)...')
    resp = client.chat.completions.create(model=GROQ_MODEL, messages=messages, max_tokens=128, temperature=0.2)
    print('\nRaw response repr:\n', repr(resp))

    # Try common attributes
    try:
        choices = getattr(resp, 'choices', None)
        if choices:
            print('\nChoices found:')
            for i, c in enumerate(choices):
                print(f'--- choice {i} ---')
                try:
                    msg = getattr(c, 'message', None)
                    if msg and hasattr(msg, 'content'):
                        print('message.content:', msg.content)
                    else:
                        print('choice attributes:', {k: str(getattr(c,k)) for k in dir(c) if not k.startswith('_') and not callable(getattr(c,k))})
                except Exception as e:
                    print('Failed to inspect choice:', e)
        else:
            print('No choices attribute on response. Printing __dict__ if available:')
            rd = getattr(resp, '__dict__', None)
            if rd:
                print(json.dumps({k: str(v) for k, v in rd.items()}, indent=2))
            else:
                print('No readable fields found; full repr above.')
    except Exception as e:
        print('Error extracting message text from response:', e)
        traceback.print_exc()

except Exception as e:
    print('\nRequest failed with exception:')
    print(e)
    traceback.print_exc()

print('\nDone.')
