"""Voice support — OpenAI Whisper transcription + TTS via stdlib HTTP."""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


def transcribe_audio(audio_path: Path, timeout: int = 90) -> str:
    """Transcribe an audio file via OpenAI Whisper API.

    Returns the transcribed text string.
    Raises RuntimeError if OPENAI_API_KEY is missing or the API call fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set — voice transcription unavailable."
        )

    audio_bytes = audio_path.read_bytes()
    filename = audio_path.name

    # Detect MIME type from extension
    ext = audio_path.suffix.lower()
    mime = {
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".mp4": "audio/mp4",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
    }.get(ext, "audio/ogg")

    # Build multipart/form-data body manually (stdlib only)
    boundary = "----ChorgiV1WhisperBoundary9f2c"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"whisper-1\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            text = data.get("text", "").strip()
            log.info("Whisper: transcribed %d chars", len(text))
            if not text:
                raise RuntimeError("Whisper returned empty transcription")
            return text
    except urllib.error.HTTPError as exc:
        err_body = exc.read()
        try:
            err = json.loads(err_body).get("error", {}).get("message", str(err_body))
        except Exception:
            err = str(err_body)
        log.error("Whisper API HTTP %d: %s", exc.code, err)
        raise RuntimeError(f"Whisper API error (HTTP {exc.code}): {err}")
    except RuntimeError:
        raise
    except Exception as exc:
        log.error("Whisper API error: %s", exc)
        raise RuntimeError(f"Whisper API error: {exc}")


def tts_generate(text: str, voice: str = "alloy", timeout: int = 30) -> str:
    """Generate speech via OpenAI TTS API.

    Returns path to generated .ogg file in /tmp.
    Raises RuntimeError if OPENAI_API_KEY is missing or the API call fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set — TTS unavailable.")

    body = json.dumps({
        "model": "tts-1",
        "input": text,
        "voice": voice,
        "response_format": "opus",
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    out_path = f"/tmp/chorgi_v1_tts_{int(time.time())}.ogg"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            Path(out_path).write_bytes(resp.read())
    except urllib.error.HTTPError as exc:
        err_body = exc.read()
        try:
            err = json.loads(err_body).get("error", {}).get("message", str(err_body))
        except Exception:
            err = str(err_body)
        log.error("TTS API HTTP %d: %s", exc.code, err)
        raise RuntimeError(f"TTS API error (HTTP {exc.code}): {err}")
    except RuntimeError:
        raise
    except Exception as exc:
        log.error("TTS API error: %s", exc)
        raise RuntimeError(f"TTS API error: {exc}")

    log.info("TTS: generated %s (%d bytes)", out_path, Path(out_path).stat().st_size)
    return out_path
