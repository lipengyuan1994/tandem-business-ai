# M14 · voice (stretch)

| Field | Value |
|---|---|
| Release / phase | Stretch at the event (P6) / v1 (P8) |
| Tier / kind | A · **EVENT** |
| Depends on | platform, ai, conversations |
| Owns tables | `voice_sessions` (metadata only) |

## Purpose
Let staff (and later visitors) speak instead of type. Browser microphone audio → speech-to-text → a normal message → the normal AI flow. Optional text-to-speech for the assistant's answer. **Text is always the fallback**, and voice is the first item on the cut list.

## Design
- Browser: `MediaRecorder` (Opus/WebM) with push-to-talk. A visible recording indicator, a stop button, and permission-denied handling that falls back to text. Microphone use is allowed only on pages with voice (Permissions-Policy).
- Upload: `POST /api/v1/voice/transcriptions` (multipart, ≤ 60 s, ≤ 2 MB) → `ai.gateway.transcribe` (`stt` model class: Vultr `whisper-large-v3-turbo`, or a local Whisper server in strict-private mode) → returns the text **for the user to confirm or edit** before it is sent as a message.
- TTS: `POST /api/v1/voice/speech` `{text}` → `tts` class (Vultr `chatterbox`; **never `auk`**, which is evaluation-only) → audio stream. Optional and off by default.
- Retention: audio is processed in memory or in a temp file deleted immediately in a `finally` block. Never stored. `voice_sessions` keeps `duration_ms`, `provider`, `latency_ms`, `status`.
- Strict-private with no local STT → voice disabled with an explanation. **No silent fallback** to browser `SpeechRecognition` or any hosted service.

## Tests (must exist)
- `test_audio_temp_file_deleted_on_success_and_error`
- `test_voice_disabled_under_strict_private_without_local_stt`
- `test_transcription_requires_confirmation_before_message_created`
- Playwright with a fake media stream: permission denied → text input focused
