"""
Treasury & Trade POC -- Voice input and output. Ported verbatim from
AI-Agent-Project's scripts/voice.py (same "prefer boring and reliable over
fancy and fragile" reasoning that project already documents) -- ask a
question out loud instead of typing it, and hear the chat answer spoken
back.

Two independent pieces, both fully offline (no new API calls, no extra
signup, nothing about your voice leaves your machine):

  - Speech-to-text (transcribe_audio): turns a recorded voice question
    into text, using faster-whisper -- a CPU-friendly, offline
    reimplementation of OpenAI's Whisper model. Only the resulting text
    (the same thing a typed question would produce) ever reaches the
    Claude API -- your actual voice recording never does.

  - Text-to-speech (speak_text): turns the chat's text answer into a
    spoken .wav file, using pyttsx3, which uses your operating system's
    own built-in voices (SAPI5 on Windows) rather than downloading a
    separate neural TTS model -- fewer native dependencies means fewer
    places for a Windows install to go wrong.

The first time you use voice input, faster-whisper downloads its model
(~140MB for the default "base" size) from Hugging Face -- one-time,
requires internet. After that it's cached locally on your machine and
works offline.

This module is deliberately independent of Streamlit -- app.py wraps
load_whisper_model() in @st.cache_resource, the same way it already
caches the DuckDB warehouse, so the (slow, few-second) model load only
happens once per app session, not on every question.
"""

import io
from pathlib import Path

from faster_whisper import WhisperModel
import pyttsx3

# "base" balances speed and accuracy well on a CPU-only laptop: "tiny" is
# faster but noticeably less accurate on real speech; anything bigger than
# "base" gets slow without a GPU. int8 quantization keeps memory/CPU use
# down further, at a small, usually-unnoticeable accuracy cost.
WHISPER_MODEL_SIZE = "base"


def load_whisper_model() -> WhisperModel:
    """Load (and, the first time, download) the speech-to-text model.
    Callers should cache the return value themselves -- see app.py's
    get_whisper_model() -- since this takes a few seconds and shouldn't
    run again on every question."""
    return WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")


def transcribe_audio(model: WhisperModel, audio_bytes: bytes) -> str:
    """Transcribe a recorded question (WAV audio bytes -- e.g. straight
    from Streamlit's st.audio_input) to text."""
    segments, _info = model.transcribe(io.BytesIO(audio_bytes))
    return " ".join(segment.text.strip() for segment in segments).strip()


def speak_text(text: str, out_path: Path) -> Path:
    """Synthesize `text` to a .wav file at out_path using the operating
    system's own text-to-speech voices. A fresh pyttsx3 engine is created
    per call -- reusing a single engine across repeated save_to_file()
    calls in the same process is known to be unreliable on some
    platforms/driver versions, and a fresh engine per answer is cheap."""
    engine = pyttsx3.init()
    engine.save_to_file(text, str(out_path))
    engine.runAndWait()
    engine.stop()
    return out_path
