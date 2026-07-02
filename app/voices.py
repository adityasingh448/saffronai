from __future__ import annotations

from dataclasses import dataclass, asdict

from app.config import settings


@dataclass(frozen=True)
class VoiceOption:
    model: str
    name: str
    gender: str
    tone: str
    recommended: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


VOICE_OPTIONS: tuple[VoiceOption, ...] = (
    VoiceOption(
        model="aura-2-arcas-en",
        name="Arcas",
        gender="Male",
        tone="Natural, smooth, clear, comfortable",
    ),
    VoiceOption(
        model="aura-2-orpheus-en",
        name="Orpheus",
        gender="Male",
        tone="Professional, clear, confident, trustworthy",
        recommended=True,
    ),
    VoiceOption(
        model="aura-2-orion-en",
        name="Orion",
        gender="Male",
        tone="Approachable, calm, polite",
    ),
    VoiceOption(
        model="aura-2-apollo-en",
        name="Apollo",
        gender="Male",
        tone="Confident, comfortable, casual",
    ),
    VoiceOption(
        model="aura-2-mars-en",
        name="Mars",
        gender="Male",
        tone="Smooth, patient, baritone",
    ),
    VoiceOption(
        model="aura-2-pluto-en",
        name="Pluto",
        gender="Male",
        tone="Calm, empathetic, baritone",
    ),
    VoiceOption(
        model="aura-2-zeus-en",
        name="Zeus",
        gender="Male",
        tone="Deep, smooth, trustworthy",
    ),
    VoiceOption(
        model="aura-2-vesta-en",
        name="Vesta",
        gender="Female",
        tone="Natural, expressive, patient, empathetic",
    ),
    VoiceOption(
        model="aura-2-hera-en",
        name="Hera",
        gender="Female",
        tone="Smooth, warm, professional",
    ),
    VoiceOption(
        model="aura-2-thalia-en",
        name="Thalia",
        gender="Female",
        tone="Clear, confident, energetic",
    ),
)


def voice_options() -> list[dict[str, object]]:
    return [voice.to_dict() for voice in VOICE_OPTIONS]


def default_voice_model() -> str:
    configured = settings.deepgram_model
    return configured if get_voice(configured) else "aura-2-arcas-en"


def get_voice(model: str | None) -> VoiceOption | None:
    if not model:
        return None
    normalized = model.strip()
    return next((voice for voice in VOICE_OPTIONS if voice.model == normalized), None)


def resolve_voice_model(model: str | None) -> str:
    return (get_voice(model) or get_voice(default_voice_model()) or VOICE_OPTIONS[0]).model


def voice_label(model: str | None) -> str:
    voice = get_voice(resolve_voice_model(model))
    return voice.name if voice else VOICE_OPTIONS[0].name
