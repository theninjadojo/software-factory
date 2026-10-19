"""Voices for videos. Piper speaks inside the sealed video container (offline, the default); ElevenLabs is called from here, because
the container has no network, and its sound is handed to the container as WAV files.

ElevenLabs gets only the narration text of a video (already cleaned by videos.py) and the configured voice and model ids. The key is
read from its own file (videos.elevenlabs_key_file, saved on the Credentials page) when a video is made, never logged or shown. What
comes back is untrusted: raw 16-bit PCM is accepted only up to a size limit and is wrapped into a WAV header here.

Background music (videos.music) is one ElevenLabs Music track per video, asked for with a fixed prompt per mood (never text from a
ticket or a model), instrumental only. What comes back is accepted only as an MP3 within a size limit; the container plays it."""
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
MAX_MUSIC = 20_000_000
MOODS = {                                            # videos.music: off, or the mood of the background track
    "calm": "Light, warm, unobtrusive instrumental background music for a short product walkthrough video. Soft electric piano and "
            "gentle plucked synths over a relaxed, steady beat around 100 BPM, positive and calm, no vocals, no big drops, even "
            "energy throughout so a voice-over sits on top.",
    "bright": "Bright, upbeat, friendly instrumental background music for a short product demo video. Clean guitar, light "
              "percussion and soft synth pads around 115 BPM, optimistic, no vocals, no big drops, even energy throughout so a "
              "voice-over sits on top.",
    "focused": "Minimal, focused instrumental background music for a short technical walkthrough. Soft ambient pads, a gentle "
               "pulsing synth and a light beat around 90 BPM, modern and understated, no vocals, no melody that competes with a "
               "voice-over, even energy throughout.",
}


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


def _post_mp3(url: str, key: str, body: dict, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg",
                                          "User-Agent": "shikumi-video"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_MUSIC + 1)


def mp3_ok(data) -> bool:
    """True when `data` starts like an MP3 file (an ID3 tag or an MPEG audio frame) and is within the size limit."""
    return (isinstance(data, bytes) and 1000 <= len(data) <= MAX_MUSIC
            and (data[:3] == b"ID3" or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)))


PERMISSIONS = {"text_to_speech": "Text to Speech", "music_generation": "Music", "voices_read": "Voices (read)", "user_read": "User (read)"}


def missing_permission(e: urllib.error.HTTPError) -> str | None:
    """When ElevenLabs refused because the key lacks a permission (a restricted key works, but only for what it was given), what to
    turn on for it as ElevenLabs' key settings name it ("Music"); '' when its answer does not say which. None when the key itself was
    refused. Only the status field and a permission id of its answer are read."""
    try:
        detail = json.loads(e.read(4096) or b"{}").get("detail")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(detail, dict) or detail.get("status") != "missing_permissions":
        return None
    got = re.search(r"permission ([a-z_]{1,40})", str(detail.get("message") or ""))
    return PERMISSIONS.get(got[1], got[1]) if got else ""


def lacks(what: str, permission: str) -> str:
    """The message for a key that may not do `what` ("make music")."""
    turn_on = f"turn on {permission or 'that permission'} for this key"
    return f"the ElevenLabs key may not {what}; {turn_on} in ElevenLabs (Developers, API keys)"


def music(key: str, mood: str, seconds: float, post=_post_mp3, timeout: int = 180) -> bytes:
    """One instrumental background track (MP3) in `mood` (a key of MOODS), about `seconds` long (5 to 300; the video loops it)."""
    if not key:
        raise VoiceError("no ElevenLabs key is saved (Settings > Credentials)")
    if mood not in MOODS:
        raise VoiceError("no such music mood")
    ms = int(max(5, min(300, seconds)) * 1000)
    try:
        data = post(f"{API}/v1/music?output_format=mp3_44100_128", key,
                    {"prompt": MOODS[mood], "music_length_ms": ms, "force_instrumental": True}, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 401 and (perm := missing_permission(e)) is not None:
            raise VoiceError(lacks("make music", perm or "Music")) from None
        raise VoiceError({401: "ElevenLabs refused the key", 429: "ElevenLabs rate limit or quota reached"}.get(
            e.code, f"ElevenLabs answered {e.code}")) from None
    except (OSError, ValueError):
        raise VoiceError("could not reach ElevenLabs") from None
    if not mp3_ok(data):
        raise VoiceError("ElevenLabs sent back something that is not music")
    return data


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
        if e.code == 401 and (perm := missing_permission(e)) is not None:
            raise VoiceError(lacks("speak", perm or "Text to Speech")) from None
        raise VoiceError({401: "ElevenLabs refused the key", 404: "ElevenLabs has no such voice",
                          429: "ElevenLabs rate limit or quota reached"}.get(e.code, f"ElevenLabs answered {e.code}")) from None
    except (OSError, ValueError):
        raise VoiceError("could not reach ElevenLabs") from None
    if not isinstance(pcm, bytes) or not 2 <= len(pcm) <= MAX_PCM or len(pcm) % 2:
        raise VoiceError("ElevenLabs sent back something that is not speech")
    return wav(pcm)
