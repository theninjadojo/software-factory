"""Voices for videos. Piper speaks inside the sealed video container (offline, the default); ElevenLabs is called from here, because
the container has no network, and its sound is handed to the container as WAV files.

ElevenLabs gets only the narration text of a video (already cleaned by videos.py) and the configured voice and model ids. The key is
read from its own file (videos.elevenlabs_key_file, saved on the Credentials page) when a video is made, never logged or shown. What
comes back is untrusted: raw 16-bit PCM is accepted only up to a size limit and is wrapped into a WAV header here."""
import io
import json
import re
import urllib.error
import urllib.request
import wave
from pathlib import Path

API = "https://api.elevenlabs.io"
RATE = 22050                                         # pcm_22050: 16-bit mono at 22.05 kHz
MAX_PCM = RATE * 2 * 120                             # two minutes of speech for one scene is far more than a scene needs
VOICE_ID = re.compile(r"[A-Za-z0-9]{8,40}")
MODEL_ID = re.compile(r"[a-z0-9_]{3,60}")
ENGINES = ("piper", "elevenlabs")


class VoiceError(Exception):
    """Speech could not be made; the message is fixed text (never the provider's reply), safe to log."""


def read_key(path) -> str:
    try:
        return Path(path).read_text().strip() if path else ""
    except OSError:
        return ""


def _post(url: str, key: str, body: dict, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/pcm",
                                          "User-Agent": "shikumi-video"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_PCM + 1)


def wav(pcm: bytes, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def elevenlabs(key: str, voice_id: str, model: str, text: str, post=_post, timeout: int = 60) -> bytes:
    """One WAV of `text` spoken by an ElevenLabs voice."""
    if not key:
        raise VoiceError("no ElevenLabs key is saved (Settings > Credentials)")
    if not VOICE_ID.fullmatch(voice_id or "") or not MODEL_ID.fullmatch(model or ""):
        raise VoiceError("the ElevenLabs voice or model id is not valid")
    if not text or len(text) > 1000:
        raise VoiceError("nothing to say, or too much for one scene")
    try:
        pcm = post(f"{API}/v1/text-to-speech/{voice_id}?output_format=pcm_22050", key, {"text": text, "model_id": model}, timeout)
    except urllib.error.HTTPError as e:
        raise VoiceError({401: "ElevenLabs refused the key", 404: "ElevenLabs has no such voice",
                          429: "ElevenLabs rate limit or quota reached"}.get(e.code, f"ElevenLabs answered {e.code}")) from None
    except (OSError, ValueError):
        raise VoiceError("could not reach ElevenLabs") from None
    if not isinstance(pcm, bytes) or not 2 <= len(pcm) <= MAX_PCM or len(pcm) % 2:
        raise VoiceError("ElevenLabs sent back something that is not speech")
    return wav(pcm)
