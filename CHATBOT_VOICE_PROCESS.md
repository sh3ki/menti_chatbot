# Menti Chatbot Response and Voice Process

This document explains the text chatbot pipeline and how microphone input and spoken chatbot responses are integrated into it.

## 1. Main components

| Component | Responsibility |
| --- | --- |
| `templates/chat.html` | Chat interface, message submission, microphone recording, voice settings, and audio playback |
| `app.py` | Chat analysis/response routes and voice API routes |
| `voice_handler.py` | Audio conversion, Vosk speech-to-text, and OpenAI text-to-speech |
| OpenAI pipeline | Message analysis, safety/risk classification, and supportive response generation |
| Firestore | Conversation messages and crisis alerts for authenticated users/contexts |
| Browser `localStorage` | Voice-response enabled state and selected voice ID |

## 2. High-level flow

```text
User types or records a message
          |
          | recorded audio -> WAV -> POST /api/voice/transcribe
          v
Text placed in the chat input
          |
          v
POST /api/chat/analyze
          |
          | emotion, risk, masking, strategy, context
          v
POST /api/chat/respond
          |
          | supportive text reply + emotion + voice_tone
          v
Reply rendered in the chat
          |
          | if voice responses are enabled
          v
POST /api/voice/synthesize
          |
          v
MP3 returned to browser and played
```

## 3. Typed-message response flow

The primary UI entry point is `sendMessage()` in `templates/chat.html`.

1. The user submits text by pressing Enter or clicking Send.
2. The browser disables the input and Send button, renders the user message, and displays a typing indicator.
3. The browser sends the message and conversation context to:

   ```text
   POST /api/chat/analyze
   ```

   The request includes `message`, `user_id`, guest state, `conversation_id`, selected mode, and response length.

4. The analysis route adds the user message to in-memory conversation history and calls the analysis pipeline. The analysis determines, among other values:
   - Detected emotion.
   - Whether the user may be masking emotion.
   - Whether the message is high risk.
   - Risk type and severity when applicable.
   - The response strategy.

5. The browser sends the original message plus the returned analysis to:

   ```text
   POST /api/chat/respond
   ```

6. The response route generates a supportive reply using the validated analysis, selected conversation mode, and requested response length.
7. The route returns:

   ```json
   {
     "reply": "...",
     "emotion": "calm",
     "voice_tone": "soft, natural, warm, and supportive"
   }
   ```

8. The browser hides the typing indicator, renders the bot message, and starts voice playback if voice responses are not muted.
9. For a logged-in user, the message is stored in the selected conversation. New conversations are created after the first reply, then the first exchange is saved using `/chat` with `save_only: true`.

If analysis or generation is unavailable, the backend uses its configured offline reply rather than returning an empty response.

## 4. Microphone input: speech-to-text

The microphone button is `#voiceMicBtn` in `templates/chat.html`.

### Recording in the browser

1. The browser checks `navigator.mediaDevices.getUserMedia` support.
2. Clicking the microphone requests a mono microphone stream with noise suppression, echo cancellation, and automatic gain control.
3. Audio is collected with an `AudioContext` and `ScriptProcessorNode`.
4. When the user clicks the microphone again, the chunks are merged and encoded into a 16-bit PCM WAV blob.
5. The browser stops and releases the recording stream and sends the WAV file as multipart form data:

   ```text
   POST /api/voice/transcribe
   Form field: audio
   ```

### Server-side transcription

`app.py` passes the uploaded bytes to `voice_handler.transcribe_audio()`.

1. The handler opens the audio with `soundfile`.
2. It converts multi-channel audio to mono.
3. It linearly resamples audio to 16 kHz when necessary.
4. It converts the samples to PCM16 bytes.
5. It loads the cached Vosk model `models/vosk-model-small-en-us-0.15`.
6. It feeds audio chunks to `vosk.KaldiRecognizer` and combines partial/final results.
7. It returns JSON containing `success` and `text`.

The model is local/offline after it is available. If the model directory is missing, the handler downloads the configured small English Vosk model once into the models directory and extracts it.

### Returning the transcript to the chat

The browser appends the returned transcript to the chat input. It does not automatically submit it. The user can review or edit the text and then use the normal typed-message flow.

## 5. Chatbot response: text-to-speech

When `sendMessage()` receives a response, it calls `speakBotResponse(reply, voice_tone)` unless the user disabled voice responses.

### Voice settings

- Voice-response enabled/disabled is stored as `menti_voice_response` in `localStorage`.
- The selected voice is stored as `menti_selectedVoiceId`.
- On page load, the browser requests `GET /api/voice/voices`.
- The current two choices are:
  - ID `0`: Female UI choice mapped to OpenAI `marin`.
  - ID `1`: Male UI choice mapped to OpenAI `echo`.

### Synthesis flow

1. The browser sends the reply text, selected `voice_id`, conversation mode, and `voice_tone` to:

   ```text
   POST /api/voice/synthesize
   ```

2. `app.py` validates that text exists and chooses a default delivery tone when one was not supplied.
3. `voice_handler.synthesize_speech()` creates an OpenAI client using `OPENAI_API_KEY`.
4. It calls the configured speech model, defaulting to `gpt-4o-mini-tts`, with the selected `marin` or `echo` voice.
5. The tone is passed as delivery instructions. Tone is based on response/mode/crisis context; it is separate from the detected user emotion.
6. The generated MP3 is first written to a temporary file.
7. Flask reads the file, returns it as `audio/mpeg`, and deletes the temporary file.
8. The browser creates an object URL, constructs an `Audio` object, and plays it. Any previous response audio is paused before the new one starts.

The speech endpoint requires the OpenAI SDK and `OPENAI_API_KEY`. Speech synthesis failure is logged in the browser and does not prevent the text reply from appearing.

## 6. Chat and voice API reference

| Endpoint | Purpose | Response |
| --- | --- | --- |
| `POST /api/chat/analyze` | Analyze message context, emotion, risk, masking, and strategy | JSON analysis object |
| `POST /api/chat/respond` | Generate final supportive response from analysis | JSON reply, emotion, voice tone |
| `POST /chat` | Legacy/combined chat route and save-only persistence path | JSON reply/status |
| `POST /api/voice/transcribe` | Convert uploaded WAV audio to text with Vosk | JSON `{success, text}` |
| `POST /api/voice/synthesize` | Convert reply text to MP3 with OpenAI Speech API | `audio/mpeg` bytes |
| `GET /api/voice/voices` | Return available UI voice options and languages | JSON voice list |

## 7. Persistence and conversation context

The split UI flow uses `/api/chat/analyze` and `/api/chat/respond`. The backend keeps a bounded in-memory history for response context. When a conversation ID is available, the response route also stores the exchange through the conversation persistence helpers.

Guest and authenticated behavior is carried in `is_guest` and `user_id`. The browser also maintains conversation sidebar state through the conversation endpoints in `app.py`.

For a high-risk message, the analysis/response flow logs a crisis alert using the configured alert persistence path. The response still returns a supportive message, but the selected crisis strategy and delivery tone are applied.

## 8. Error and fallback behavior

- Empty typed messages are ignored by the browser and rejected by the API.
- Microphone permission errors stop recording and show a user-facing message.
- Unsupported browsers disable the microphone button.
- No detected speech returns `No speech detected`; it is not inserted into the input.
- Vosk errors return `{success: false, error: ...}`.
- OpenAI analysis/generation failures use the configured offline reply path where supported.
- OpenAI TTS failures do not remove the already-rendered text response.
- Missing `OPENAI_API_KEY` causes speech synthesis to fail clearly; it does not affect local Vosk transcription.

## 9. Configuration and dependencies

Important configuration values:

- `OPENAI_API_KEY`: required for response generation and OpenAI speech synthesis.
- `OPENAI_TTS_MODEL`: optional; defaults to `gpt-4o-mini-tts`.
- `VOSK_MODELS_DIR`: optional model root; defaults to the project `models` directory.

Relevant Python packages include the OpenAI SDK, Vosk, NumPy, SoundFile, and Requests. The browser must support microphone access, Web Audio APIs, and HTML audio playback for the complete voice experience.

## Source files

- `app.py`
- `voice_handler.py`
- `templates/chat.html`
- `requirements.txt`
