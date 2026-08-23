"""
List Groq models available to your API key.
Run: python tools/list_groq_models.py

This script tries the `groq` Python client first, then falls back to a direct HTTP request
against common Groq model endpoints. It prints any errors it encounters.
"""
import os
import json
import traceback
from dotenv import load_dotenv

load_dotenv()
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
if not GROQ_API_KEY:
    print('GROQ_API_KEY not set in environment. Copy it into .env and try again.')
    raise SystemExit(1)

# Try groq SDK first
try:
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    print('Attempting to list models via Groq SDK...')
    try:
        models = None
        if hasattr(client, 'models') and hasattr(client.models, 'list'):
            models = client.models.list()
        elif hasattr(client, 'list_models'):
            models = client.list_models()
        else:
            models = None

        if models is not None:
            print('Models (via SDK):')
            # Try to JSON-serialize; fall back to iterating items for readability
            try:
                print(json.dumps(models, indent=2))
            except TypeError:
                try:
                    # If models is iterable, print each item
                    for i, m in enumerate(models):
                            print(f"--- Model #{i+1} ---")
                            try:
                                print("repr:", repr(m))
                            except Exception:
                                pass
                            try:
                                print("type:", type(m))
                            except Exception:
                                pass

                            # Try to call a to_dict() method if present
                            if hasattr(m, 'to_dict'):
                                try:
                                    d = m.to_dict()
                                    print("to_dict():")
                                    print(json.dumps(d, indent=2, default=str))
                                except Exception as e:
                                    print("to_dict() failed:", e)

                            # Try __dict__
                            try:
                                md = getattr(m, '__dict__', None)
                                if md:
                                    print("__dict__:")
                                    print(json.dumps({k: str(v) for k, v in md.items()}, indent=2))
                            except Exception as e:
                                print("__dict__ access failed:", e)

                            # Print non-private dir() entries
                            try:
                                public = [x for x in dir(m) if not x.startswith('_')]
                                print("dir():", public)
                            except Exception:
                                pass

                            # Check some common attribute names
                            for key in ('id', 'name', 'model', 'model_id', 'modelName', 'object'):
                                if hasattr(m, key):
                                    try:
                                        print(f"{key}:", getattr(m, key))
                                    except Exception as e:
                                        print(f"{key} access failed:", e)
                            # Fallback print
                            try:
                                print('fallback str():', str(m))
                            except Exception:
                                pass
                except TypeError:
                    # Last resort: print repr
                    print(repr(models))
            raise SystemExit(0)
        else:
            print('Groq SDK present but no obvious listing method found. Falling back to HTTP.')
    except Exception as e:
        print('Groq SDK listing failed:', e)
        traceback.print_exc()
except Exception as e:
    print('Groq SDK not available or failed to initialize:', e)

# Fallback to HTTP GET against common endpoints
import requests
headers = {'Authorization': f'Bearer {GROQ_API_KEY}'}
endpoints = [
    'https://api.groq.ai/v1/models',
    'https://api.groq.com/v1/models',
    'https://api.groq.ai/models',
]
for url in endpoints:
    try:
        print(f'Trying HTTP GET {url} ...')
        r = requests.get(url, headers=headers, timeout=15)
        print('Status:', r.status_code)
        try:
            print(json.dumps(r.json(), indent=2))
        except Exception:
            print(r.text[:1000])
    except Exception as e:
        print(f'HTTP request to {url} failed: {e}')

print('\nIf none of the above work, check your Groq console and documentation for the correct list-models endpoint or model names.')
