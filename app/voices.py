from __future__ import annotations

from dataclasses import dataclass, asdict
import time

import requests

from app.config import settings


VOICE_CACHE_SECONDS = 300
_VOICE_CACHE: tuple[float, list["VoiceOption"]] = (0.0, [])


@dataclass(frozen=True)
class VoiceOption:
    model: str
    name: str
    gender: str
    tone: str
    recommended: bool = False
    preview_url: str = ""
    category: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def voice_options(force_refresh: bool = False) -> list[dict[str, object]]:
    return [voice.to_dict() for voice in _load_elevenlabs_voices(force_refresh=force_refresh)]


def default_voice_model() -> str:
    voices = _load_elevenlabs_voices()
    configured = (settings.elevenlabs_voice_id or "").strip()
    if configured and any(voice.model == configured for voice in voices):
        return configured
    recommended = next((voice for voice in voices if voice.recommended), None)
    if recommended:
        return recommended.model
    if voices:
        return voices[0].model
    return configured


def get_voice(model: str | None) -> VoiceOption | None:
    if not model:
        return None
    normalized = model.strip()
    return next((voice for voice in _load_elevenlabs_voices() if voice.model == normalized), None)


def resolve_voice_model(model: str | None) -> str:
    requested = (model or "").strip()
    if requested and get_voice(requested):
        return requested
    return default_voice_model()


def voice_label(model: str | None) -> str:
    voice = get_voice(resolve_voice_model(model))
    return voice.name if voice else "ElevenLabs voice"


def _load_elevenlabs_voices(force_refresh: bool = False) -> list[VoiceOption]:
    if not settings.elevenlabs_api_key:
        return []

    cache_time, cache_voices = _VOICE_CACHE
    if not force_refresh and cache_voices and time.time() - cache_time < VOICE_CACHE_SECONDS:
        return cache_voices

    voices = _fetch_elevenlabs_voices()
    _set_voice_cache(voices)
    return voices


def _set_voice_cache(voices: list[VoiceOption]) -> None:
    global _VOICE_CACHE
    _VOICE_CACHE = (time.time(), voices)


def _fetch_elevenlabs_voices() -> list[VoiceOption]:
    headers = {"xi-api-key": settings.elevenlabs_api_key or ""}
    voices: list[VoiceOption] = []
    next_page_token: str | None = None

    while True:
        params: dict[str, object] = {"page_size": 100}
        if next_page_token:
            params["next_page_token"] = next_page_token

        response = requests.get(
            "https://api.elevenlabs.io/v2/voices",
            headers=headers,
            params=params,
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        voices.extend(_voice_from_payload(item) for item in payload.get("voices", []))

        next_page_token = payload.get("next_page_token")
        if not payload.get("has_more") or not next_page_token:
            break

    configured = (settings.elevenlabs_voice_id or "").strip()
    if configured:
        voices = [
            VoiceOption(**{**voice.to_dict(), "recommended": voice.model == configured})
            for voice in voices
        ]
    elif voices:
        recommended_index = _recommended_voice_index(voices)
        recommended = voices[recommended_index]
        voices[recommended_index] = VoiceOption(**{**recommended.to_dict(), "recommended": True})

    return voices


def _recommended_voice_index(voices: list[VoiceOption]) -> int:
    best_index = 0
    best_score = -1
    positive = {
        "natural": 5,
        "realistic": 5,
        "confident": 4,
        "energetic": 4,
        "engaging": 4,
        "podcast": 3,
        "warm": 3,
        "upbeat": 3,
        "educator": 2,
        "explainer": 2,
        "professional": 2,
    }
    negative = {"romantic": 6, "bot": 4, "hyped": 2, "viral": 2}

    for index, voice in enumerate(voices):
        text = f"{voice.name} {voice.gender} {voice.tone} {voice.category}".lower()
        score = sum(weight for word, weight in positive.items() if word in text)
        score -= sum(weight for word, weight in negative.items() if word in text)
        if "male" in text:
            score += 1
        if score > best_score:
            best_score = score
            best_index = index

    return best_index


def _voice_from_payload(item: dict) -> VoiceOption:
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    category = str(item.get("category") or "custom").replace("_", " ").title()
    gender = _label_value(labels, ("gender", "age", "accent")) or category
    tone = _voice_tone(item, labels)

    return VoiceOption(
        model=str(item.get("voice_id") or "").strip(),
        name=str(item.get("name") or "Saved voice").strip(),
        gender=gender,
        tone=tone,
        preview_url=str(item.get("preview_url") or ""),
        category=category,
    )


def _voice_tone(item: dict, labels: dict) -> str:
    label_parts = [
        value
        for key, value in labels.items()
        if key not in {"gender", "age", "accent"} and isinstance(value, str) and value.strip()
    ]
    if label_parts:
        return ", ".join(label_parts[:3]).title()

    description = " ".join(str(item.get("description") or "").split())
    if description:
        return description[:110]

    return "Natural, realistic, energetic"


def _label_value(labels: dict, keys: tuple[str, ...]) -> str:
    values = [str(labels.get(key) or "").strip().title() for key in keys if str(labels.get(key) or "").strip()]
    return " / ".join(values)
