"""Voice handling module.

STT: Vosk (offline, open-source)
TTS: OpenAI Speech API
"""

import io
import json
import os
import tempfile
import threading
import zipfile
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

_BASE_DIR = Path(__file__).resolve().parent

# -------------------- Vosk STT --------------------
_vosk_model = None
_vosk_lock = threading.Lock()
_DEFAULT_VOSK_MODEL = "vosk-model-small-en-us-0.15"
_VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

def _ensure_vosk_model_dir():
    """Ensure the small English Vosk model is present; download once if missing."""
    model_root = Path(os.getenv("VOSK_MODELS_DIR", _BASE_DIR / "models"))
    model_dir = model_root / _DEFAULT_VOSK_MODEL

    if model_dir.exists():
        return model_dir

    model_root.mkdir(parents=True, exist_ok=True)
    zip_path = model_root / f"{_DEFAULT_VOSK_MODEL}.zip"

    print(f"[Voice] Downloading Vosk model to {zip_path}...")
    with requests.get(_VOSK_MODEL_URL, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(zip_path, "wb") as zip_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    zip_file.write(chunk)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(model_root)

    try:
        zip_path.unlink(missing_ok=True)
    except Exception:
        pass

    if not model_dir.exists():
        raise RuntimeError("Vosk model download completed but model directory was not found.")

    return model_dir


def _get_vosk_model():
    """Load and cache Vosk model."""
    global _vosk_model

    if _vosk_model is not None:
        return _vosk_model

    with _vosk_lock:
        if _vosk_model is not None:
            return _vosk_model

        try:
            import vosk
        except ImportError as exc:
            raise RuntimeError("Vosk is not installed. Run: pip install vosk") from exc

        model_dir = _ensure_vosk_model_dir()
        _vosk_model = vosk.Model(str(model_dir))
        return _vosk_model


def _to_pcm16_mono_16k(audio_bytes):
    """Convert input audio bytes to mono 16kHz PCM16 for Vosk."""
    with sf.SoundFile(io.BytesIO(audio_bytes)) as in_file:
        audio = in_file.read(dtype="float32", always_2d=True)
        sample_rate = in_file.samplerate

    mono = np.mean(audio, axis=1)

    if sample_rate != 16000:
        # Lightweight linear resampling without extra heavy dependencies.
        source_times = np.linspace(0, len(mono) - 1, num=len(mono), dtype=np.float64)
        target_len = int(len(mono) * 16000 / sample_rate)
        target_times = np.linspace(0, len(mono) - 1, num=max(target_len, 1), dtype=np.float64)
        mono = np.interp(target_times, source_times, mono)

    mono = np.clip(mono, -1.0, 1.0)
    pcm16 = (mono * 32767.0).astype(np.int16).tobytes()
    return pcm16


def transcribe_audio(audio_bytes):
    """
    Transcribe audio bytes using Vosk (offline, free, open-source)
    
    Args:
        audio_bytes: WAV audio data
    
    Returns:
        dict with 'text' and 'success' keys
    """
    try:
        import vosk

        model = _get_vosk_model()
        pcm16 = _to_pcm16_mono_16k(audio_bytes)

        recognizer = vosk.KaldiRecognizer(model, 16000)
        text_parts = []

        chunk_size = 4000
        for i in range(0, len(pcm16), chunk_size):
            chunk = pcm16[i:i + chunk_size]
            if recognizer.AcceptWaveform(chunk):
                partial = json.loads(recognizer.Result())
                partial_text = partial.get("text", "").strip()
                if partial_text:
                    text_parts.append(partial_text)

        final = json.loads(recognizer.FinalResult())
        final_text = final.get("text", "").strip()
        if final_text:
            text_parts.append(final_text)

        text_result = " ".join(text_parts).strip()
        return {
            "success": True,
            "text": text_result or "No speech detected",
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def synthesize_speech(text, voice_id=0, voice_tone='soft, natural, warm, and supportive'):
    """
    Synthesize speech using OpenAI's expressive speech model.
    
    Args:
        text: Text to speak
        voice_id: Voice index (0=female, 1=male)
        voice_tone: How the reply should be delivered, independent of user mood.
    
    Returns:
        dict with 'audio_file' path and 'success' keys
    """
    try:
        if not OpenAI or not os.getenv('OPENAI_API_KEY'):
            raise RuntimeError('OPENAI_API_KEY is not configured for speech synthesis')

        # Keep the existing two-option UI, but map it to OpenAI voices.
        openai_voice = 'marin' if int(voice_id) == 0 else 'echo'
        client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        audio = client.audio.speech.create(
            model=os.getenv('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts'),
            voice=openai_voice,
            input=text[:4096],
            instructions=(
                f'Read this mental-health support message with {voice_tone}. '
                'Use natural pauses, gentle emphasis, and a human conversational delivery. '
                'Do not sound like an advertisement, announcer, or robot.'
            ),
            response_format='mp3',
        )

        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            tmp_path = tmp.name
        audio.stream_to_file(tmp_path)

        return {
            'success': True,
            'audio_file': tmp_path,
            'mimetype': 'audio/mpeg'
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_available_voices():
    """Return the OpenAI voices exposed by the existing two-choice UI."""
    return {
        "voices": [
            {"id": 0, "name": "Female", "description": "OpenAI Marin voice"},
            {"id": 1, "name": "Male", "description": "OpenAI Echo voice"},
        ],
        "languages": ["en"],
    }
