"""
Test Gemini (Google Generative) model connectivity for `GEMINI_MODEL` using `GEMINI_API_KEY`.

Usage:
  1. Add GEMINI_API_KEY and (optionally) GEMINI_MODEL to your .env
     GEMINI_API_KEY=...
     GEMINI_MODEL=gemini-2.5-flash-lite
  2. Run: python tools/test_gemini_model.py

The script will try in order:
 - google.generativeai client (if installed)
 - REST POST to common Google generative endpoints with Bearer auth
 - REST POST with ?key=API_KEY param

It prints raw responses and any errors so you can confirm whether the model accepts chat/generation requests.
"""
import os
import json
import traceback
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite')

if not GEMINI_API_KEY:
    print('Please set GEMINI_API_KEY in your .env and re-run this script.')
    raise SystemExit(1)

print('Using model:', GEMINI_MODEL)

# Try google.generativeai client if available
try:
    import google.generativeai as genai
    print('\nAttempting via google.generativeai client...')
    try:
        # Some client versions use genai.configure; others accept api_key param on call
        try:
            genai.configure(api_key=GEMINI_API_KEY)
        except Exception:
            pass
        prompt = 'Hello Menti — please respond briefly so I can test connectivity.'
        resp = genai.generate_text(model=GEMINI_MODEL, prompt=prompt)
        print('\nClient response object:')
        try:
            print(json.dumps(resp, default=str, indent=2))
        except Exception:
            print(repr(resp))
        print('\nExtracted text:')
        try:
            # Different client versions return different structures
            text = resp.text if hasattr(resp, 'text') else (resp.get('candidates', [{}])[0].get('content') if isinstance(resp, dict) else None)
            print(text)
        except Exception:
            print('Could not extract text cleanly. See raw response above.')
        raise SystemExit(0)
    except Exception as e:
        print('google.generativeai client attempt failed:', e)
        traceback.print_exc()
except Exception:
    print('google.generativeai client not installed or failed to import — falling back to REST attempts')

# REST fallback attempts
import requests

endpoints = [
    f'https://generativelanguage.googleapis.com/v1/models/{GEMINI_MODEL}:generateText',
    f'https://generativelanguage.googleapis.com/v1beta2/models/{GEMINI_MODEL}:generateText',
    f'https://generativelanguage.googleapis.com/v1/models/{GEMINI_MODEL}:generate',
]

payload_variants = [
    # Newer "generateText" shape
    lambda prompt: {"prompt": {"text": prompt}},
    # Alternate shape
    lambda prompt: {"input": prompt},
    # v1beta2 style
    lambda prompt: {"instances": [{"input": prompt}]},
]

headers_bearer = {'Authorization': f'Bearer {GEMINI_API_KEY}', 'Content-Type': 'application/json'}

prompt = 'Hello Menti — please respond briefly so I can test connectivity.'

for url in endpoints:
    for make_payload in payload_variants:
        payload = make_payload(prompt)
        print(f'\nTrying REST POST {url} with Bearer auth and payload keys: {list(payload.keys())}...')
        try:
            r = requests.post(url, headers=headers_bearer, json=payload, timeout=15)
            print('Status:', r.status_code)
            try:
                print(json.dumps(r.json(), indent=2))
            except Exception:
                print(r.text[:2000])
            if 200 <= r.status_code < 300:
                print('\nSuccess via Bearer POST')
                raise SystemExit(0)
        except Exception as e:
            print('Request failed:', e)

# Try with API key as query param
for url in endpoints:
    full = url + f'?key={GEMINI_API_KEY}'
    for make_payload in payload_variants:
        payload = make_payload(prompt)
        print(f'\nTrying REST POST {full} with key param...')
        try:
            r = requests.post(full, json=payload, timeout=15)
            print('Status:', r.status_code)
            try:
                print(json.dumps(r.json(), indent=2))
            except Exception:
                print(r.text[:2000])
            if 200 <= r.status_code < 300:
                print('\nSuccess via key param POST')
                raise SystemExit(0)
        except Exception as e:
            print('Request failed:', e)

print('\nAll attempts finished. If none succeeded, verify your key and model name in the Google Cloud console or Generative AI console.')
