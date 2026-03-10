# services/language_detection/detector.py
#
# Detects whether the user is speaking English, Hindi or Tamil.
# We do this in two steps:
#   1. Unicode script check — if there are Devanagari chars it's Hindi,
#      Tamil unicode block → Tamil. This is basically instant and very reliable.
#   2. If the text looks like Latin (could be English or romanised Hindi),
#      we fall back to langdetect.
#
# Why not just use langdetect for everything? Because it sometimes misclassifies
# short Hindi utterances as other languages. The unicode check is 100% accurate
# for properly encoded Indic text.

from typing import Tuple

SUPPORTED_LANGS = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil"
}

# Unicode codepoint ranges for each script
# https://unicode.org/charts/
DEVANAGARI_RANGE = set(range(0x0900, 0x0980))   # Hindi uses Devanagari
TAMIL_RANGE      = set(range(0x0B80, 0x0C00))   # Tamil script


def _check_by_script(text: str) -> str:
    """
    Quick pass over the characters to see if Devanagari or Tamil script
    makes up more than 20% of the text. If so, we're confident about the language.
    """
    if not text:
        return "en"

    total = len(text.strip())
    if total == 0:
        return "en"

    hindi_chars = sum(1 for ch in text if ord(ch) in DEVANAGARI_RANGE)
    tamil_chars = sum(1 for ch in text if ord(ch) in TAMIL_RANGE)

    # 20% threshold handles mixed text (e.g. "I want मुझे appointment")
    if hindi_chars / total > 0.20:
        return "hi"
    if tamil_chars / total > 0.20:
        return "ta"

    return "en"  # default assumption


def detect_language(text: str) -> Tuple[str, float]:
    """
    Returns (language_code, confidence_0_to_1).

    Confidence is approximate — we return 0.95 for script-based detection
    because that's basically certain, and ~0.7-0.9 for langdetect results.
    """
    if not text or not text.strip():
        return "en", 1.0

    # Script check first — if we get a non-English result we're done
    script_lang = _check_by_script(text)
    if script_lang != "en":
        return script_lang, 0.95

    # Try langdetect for Latin script text
    # It's not always installed so we wrap this in a try
    try:
        from langdetect import detect_langs
        guesses = detect_langs(text)
        if guesses:
            best = guesses[0]
            code = best.lang
            prob = float(best.prob)

            # langdetect sometimes returns "hi" for romanised Hindi
            if code.startswith("hi"):
                return "hi", prob
            if code.startswith("ta"):
                return "ta", prob
            # anything else we treat as English
            return "en", prob
    except Exception:
        pass   # langdetect not installed or failed — that's fine

    return "en", 0.8


def get_language_name(code: str) -> str:
    return SUPPORTED_LANGS.get(code, "English")


def get_tts_language_code(lang: str) -> str:
    """Maps our short codes to BCP-47 tags used by TTS/STT APIs."""
    return {"en": "en-US", "hi": "hi-IN", "ta": "ta-IN"}.get(lang, "en-US")


def get_language_instruction(lang: str) -> str:
    """
    Short instruction appended to LLM prompts to make it reply in the right language.
    """
    instructions = {
        "en": "Respond in English.",
        "hi": "हिंदी में जवाब दें।",
        "ta": "தமிழில் பதில் சொல்லுங்கள்."
    }
    return instructions.get(lang, "Respond in English.")
