"""Stable UI-facing language and option catalog."""

LANGUAGES = {
    "Arabic": "arb_Arab",
    "Burmese": "mya_Mymr",
    "Chinese": "zho_Hans",
    "Czech": "ces_Latn",
    "Danish": "dan_Latn",
    "Dutch": "nld_Latn",
    "English": "eng_Latn",
    "Finnish": "fin_Latn",
    "French": "fra_Latn",
    "German": "deu_Latn",
    "Greek": "ell_Grek",
    "Hebrew": "heb_Hebr",
    "Hindi": "hin_Deva",
    "Hungarian": "hun_Latn",
    "Indonesian": "ind_Latn",
    "Italian": "ita_Latn",
    "Japanese": "jpn_Jpan",
    "Khmer": "khm_Khmr",
    "Korean": "kor_Hang",
    "Lao": "lao_Laoo",
    "Malay": "zsm_Latn",
    "Norwegian": "nob_Latn",
    "Polish": "pol_Latn",
    "Portuguese": "por_Latn",
    "Romanian": "ron_Latn",
    "Russian": "rus_Cyrl",
    "Spanish": "spa_Latn",
    "Swahili": "swh_Latn",
    "Swedish": "swe_Latn",
    "Tagalog": "tgl_Latn",
    "Thai": "tha_Thai",
    "Turkish": "tur_Latn",
    "Vietnamese": "vie_Latn",
}

# The engines use these stable English keys.  The interface, however, should
# speak the user's language without invalidating projects created previously.
LANGUAGE_LABELS = {
    "Arabic": "Árabe", "Burmese": "Birmanês", "Chinese": "Chinês",
    "Czech": "Tcheco", "Danish": "Dinamarquês", "Dutch": "Holandês",
    "English": "Inglês", "Finnish": "Finlandês", "French": "Francês",
    "German": "Alemão", "Greek": "Grego", "Hebrew": "Hebraico",
    "Hindi": "Hindi", "Hungarian": "Húngaro", "Indonesian": "Indonésio",
    "Italian": "Italiano", "Japanese": "Japonês", "Khmer": "Khmer",
    "Korean": "Coreano", "Lao": "Laosiano", "Malay": "Malaio",
    "Norwegian": "Norueguês", "Polish": "Polonês", "Portuguese": "Português",
    "Romanian": "Romeno", "Russian": "Russo", "Spanish": "Espanhol",
    "Swahili": "Suaíli", "Swedish": "Sueco", "Tagalog": "Tagalo",
    "Thai": "Tailandês", "Turkish": "Turco", "Vietnamese": "Vietnamita",
}

LANGUAGE_CHOICES = [(LANGUAGE_LABELS[name], name) for name in LANGUAGES]

SOURCE_LANGUAGES = [
    "Auto", "Portuguese", "English", "Spanish", "French", "German",
    "Italian", "Japanese", "Korean", "Chinese", "Russian", "Arabic",
    "Hindi", "Dutch", "Polish", "Turkish", "Swedish", "Czech",
    "Romanian", "Hungarian",
]

SOURCE_LANGUAGE_LABELS = {"Auto": "Detectar automaticamente", **{
    name: LANGUAGE_LABELS.get(name, name) for name in SOURCE_LANGUAGES if name != "Auto"
}}
SOURCE_LANGUAGE_CHOICES = [(SOURCE_LANGUAGE_LABELS[name], name) for name in SOURCE_LANGUAGES]

SOURCE_LANGUAGE_CODES = {
    "Auto": None,
    "Portuguese": "pt",
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese": "zh",
    "Russian": "ru",
    "Arabic": "ar",
    "Hindi": "hi",
    "Dutch": "nl",
    "Polish": "pl",
    "Turkish": "tr",
    "Swedish": "sv",
    "Czech": "cs",
    "Romanian": "ro",
    "Hungarian": "hu",
}

DEFAULT_TARGETS = ["English", "Spanish"]
WHISPER_MODELS = ["large-v3", "medium", "small", "base"]
MP3_BITRATES = ["320k", "256k", "192k", "128k"]


def iso_code(nllb_code: str) -> str:
    """Return the ISO-639-1 code used by VoxCPM and filenames."""
    mapping = {
        "arb": "ar", "mya": "my", "zho": "zh", "ces": "cs",
        "dan": "da", "nld": "nl", "eng": "en", "fin": "fi",
        "fra": "fr", "deu": "de", "ell": "el", "heb": "he",
        "hin": "hi", "hun": "hu", "ind": "id", "ita": "it",
        "jpn": "ja", "khm": "km", "kor": "ko", "lao": "lo",
        "zsm": "ms", "nob": "no", "pol": "pl", "por": "pt",
        "ron": "ro", "rus": "ru", "spa": "es", "swh": "sw",
        "swe": "sv", "tgl": "tl", "tha": "th", "tur": "tr",
        "vie": "vi",
    }
    prefix = (nllb_code or "").split("_")[0].lower()
    return mapping.get(prefix, prefix[:2])


def language_label(language: str) -> str:
    """Return the Portuguese label while accepting existing project keys."""
    return LANGUAGE_LABELS.get(language, language)
