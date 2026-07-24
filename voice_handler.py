"""Voice handling module.

STT: Vosk (offline, open-source)
TTS: Piper (offline neural voices, open-source ONNX)
"""

import io
import json
import os
import tempfile
import threading
import wave
import zipfile
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

_BASE_DIR = Path(__file__).resolve().parent

# -------------------- Vosk STT --------------------
_vosk_model = None
_vosk_lock = threading.Lock()
_DEFAULT_VOSK_MODEL = "vosk-model-small-en-us-0.15"
_VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

# -------------------- Piper TTS --------------------
_piper_lock = threading.Lock()
_piper_cache = {}

_PIPER_VOICE_OPTIONS = [
    {
        "id": 0,
        "name": "Female (Neural)",
        "description": "Piper en_US lessac medium",
        "key": "female",
    },
    {
        "id": 1,
        "name": "Male (Neural)",
        "description": "Piper en_US ryan medium",
        "key": "male",
    },
]

_PIPER_MODELS = {
    "female": {
        "onnx": "en_US-lessac-medium.onnx",
        "json": "en_US-lessac-medium.onnx.json",
        "base_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium",
    },
    "male": {
        "onnx": "en_US-ryan-medium.onnx",
        "json": "en_US-ryan-medium.onnx.json",
        "base_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium",
    },
}


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


def _download_file(url, target_path):
    """Download a file to target_path atomically."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".part")

    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with open(tmp_path, "wb") as out_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    out_file.write(chunk)

    tmp_path.replace(target_path)


def _ensure_piper_voice_files(voice_key):
    """Ensure Piper ONNX and config files exist locally for selected voice."""
    model_info = _PIPER_MODELS[voice_key]
    model_root = Path(os.getenv("PIPER_MODELS_DIR", _BASE_DIR / "models" / "piper"))
    voice_dir = model_root / voice_key

    onnx_path = voice_dir / model_info["onnx"]
    json_path = voice_dir / model_info["json"]

    if not onnx_path.exists():
        onnx_url = f"{model_info['base_url']}/{model_info['onnx']}"
        print(f"[Voice] Downloading Piper model: {onnx_url}")
        _download_file(onnx_url, onnx_path)

    if not json_path.exists():
        json_url = f"{model_info['base_url']}/{model_info['json']}"
        print(f"[Voice] Downloading Piper config: {json_url}")
        _download_file(json_url, json_path)

    return onnx_path


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


def _get_piper_voice(voice_key):
    """Load and cache Piper voice model."""
    if voice_key in _piper_cache:
        return _piper_cache[voice_key]

    with _piper_lock:
        if voice_key in _piper_cache:
            return _piper_cache[voice_key]

        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise RuntimeError("Piper TTS is not installed. Run: pip install piper-tts") from exc

        model_path = _ensure_piper_voice_files(voice_key)
        voice = PiperVoice.load(str(model_path))
        _piper_cache[voice_key] = voice
        return voice

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

def synthesize_speech(text, voice_id=0):
    """
    Synthesize speech using Piper TTS (free, open-source neural voices)
    
    Args:
        text: Text to speak
        voice_id: Voice index (0=default male, 1=female if available)
    
    Returns:
        dict with 'audio_file' path and 'success' keys
    """
    try:
        selected = _PIPER_VOICE_OPTIONS[0]
        for voice in _PIPER_VOICE_OPTIONS:
            if voice["id"] == int(voice_id):
                selected = voice
                break

        piper_voice = _get_piper_voice(selected["key"])

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name

        with wave.open(tmp_path, "wb") as wav_out:
            piper_voice.synthesize_wav(text=text, wav_file=wav_out)

        return {
            'success': True,
            'audio_file': tmp_path,
            'mimetype': 'audio/wav'
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_available_voices():
    """Get list of available voices"""
    try:
        # Warm-up both voices so UI can trust availability.
        _get_piper_voice("female")
        _get_piper_voice("male")

        return {
            'voices': [
                {
                    'id': voice['id'],
                    'name': voice['name'],
                    'description': voice['description']
                }
                for voice in _PIPER_VOICE_OPTIONS
            ],
            'languages': ['en']
        }
    except Exception as e:
        print(f"Error getting voices: {e}")
        return {
            'voices': [],
            'languages': [],
            'error': str(e)
        }

