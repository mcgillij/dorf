"""TTS provider registry.

get_provider() is the ONLY entry point callers should use — backends are
imported lazily there so importing bot.tts stays cheap (torch-weight
modules load only when a provider is actually used).
"""
import logging

from bot.constants import TTS_PROVIDER
from bot.tts.base import PROVIDER_CLASSES, TTSProvider

logger = logging.getLogger(__name__)

_INSTANCES = {}


def get_provider(name: str = "") -> TTSProvider:
    """Return a (cached) provider instance by registry name.

    Defaults to TTS_PROVIDER (env, "kokoro"). Swap engines by installing a
    new provider module and setting TTS_PROVIDER — call sites never change.
    """
    selected = name or TTS_PROVIDER
    if selected in _INSTANCES:
        return _INSTANCES[selected]
    if not PROVIDER_CLASSES:
        # Lazily import built-in backends; each registers on import.
        from bot.tts import kokoro as _kokoro  # noqa: F401

    if selected not in PROVIDER_CLASSES:
        raise KeyError(
            f"Unknown TTS provider '{selected}'. Available: {sorted(PROVIDER_CLASSES)}"
        )
    _INSTANCES[selected] = PROVIDER_CLASSES[selected]()
    logger.info("tts.provider_selected name=%s", selected)
    return _INSTANCES[selected]
