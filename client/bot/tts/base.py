"""Pluggable TTS provider interface.

A provider turns text into a wav file on disk — that's the whole contract.
Backends (kokoro today, piper/xtts/whatever tomorrow) implement synthesize()
and register themselves; callers go through bot.tts.get_provider() and never
import a backend directly.

synthesize() is deliberately SYNC (all current engines are blocking); call
sites own their threading (to_thread / executor).
"""
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Registry of provider classes, keyed by provider name. Populated via
# @register_tts at class definition time — importing bot.tts does NOT import
# any backend (they register from their own lazily-imported modules).
PROVIDER_CLASSES = {}


def register_tts(cls):
    """Class decorator: add a TTSProvider subclass to the registry."""
    PROVIDER_CLASSES[cls.name] = cls
    return cls


class TTSProvider(ABC):
    """A text-to-speech backend.

    name: registry key (e.g. "kokoro"), matched against TTS_PROVIDER env.
    """

    name: str = ""

    @abstractmethod
    def synthesize(self, text: str, voice: str, output_wav: str) -> None:
        """Blocking: synthesize `text` with `voice`, write a wav file to
        `output_wav` (16-bit PCM, so Godot's AudioStreamWAV can load it).
        Raises on failure.
        """
        raise NotImplementedError
