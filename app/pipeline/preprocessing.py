"""Stage 1 - Python preprocessing.

Deterministic, fast, free. Runs before any AI call:
  * input validation (empty, too short, too long, no letters/digits)
  * normalisation (unicode, invisible characters, whitespace, repeated words/punctuation)
  * spelling *detection* with an offline dictionary (the AI later confirms fixes
    in context, so domain words are not "corrected" blindly)
  * a duplicate key used to detect repeated submissions
"""
from __future__ import annotations

import re
import string
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from spellchecker import SpellChecker

STOPWORDS = frozenset(
    """a about above after again all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for
    from further had has have having he her here hers him his how i if in into is it its
    just me more most my myself no nor not now of off on once only or other our ours out
    over own same she should so some such than that the their them then there these they
    this those through to too under until up very was we were what when where which while
    who whom why will with would you your yours""".split()
)

_WHITESPACE = re.compile(r"\s+")
_REPEATED_PUNCT = re.compile(r"([!?.,;:])\1+")
_WORD = re.compile(r"[A-Za-z]+")
_PLAIN_WORD = re.compile(r"[A-Za-z]+")
_SKIP_CHARS = re.compile(r"[@/\\.:_#\-+&']")
_STRIP_CHARS = string.punctuation + "“”‘’«»"


class QueryValidationError(ValueError):
    """Raised for queries that should never reach the AI. Message is user-facing."""


@dataclass
class PreprocessResult:
    original: str
    normalized: str
    dedup_key: str
    word_count: int
    notes: list[str] = field(default_factory=list)
    # word -> ranked candidate corrections (first is the dictionary's best guess)
    misspellings: dict[str, list[str]] = field(default_factory=dict)


# ----------------------------------------------------------------- normalisation
def normalize_query(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    text = unicodedata.normalize("NFKC", text)

    visible = "".join(ch if (ch.isprintable() or ch.isspace()) else " " for ch in text)
    if visible != text:
        notes.append("Removed invisible characters.")
    text = _WHITESPACE.sub(" ", visible).strip()

    collapsed = _REPEATED_PUNCT.sub(r"\1", text)
    if collapsed != text:
        notes.append("Collapsed repeated punctuation.")
    text = collapsed

    words, kept = text.split(" "), []
    for word in words:
        if kept and word.lower() == kept[-1].lower() and any(c.isalpha() for c in word):
            continue
        kept.append(word)
    if len(kept) < len(words):
        notes.append("Removed a repeated word.")
    return " ".join(kept), notes


def make_dedup_key(text: str) -> str:
    """Case- and punctuation-insensitive key: 'Python  Jobs!' == 'python jobs'."""
    lowered = text.casefold()
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
    return _WHITESPACE.sub(" ", cleaned).strip()[:400]


def content_words(text: str) -> list[str]:
    """Meaningful lowercase words (no stopwords, no 1-2 letter tokens)."""
    return [w for w in (m.lower() for m in _WORD.findall(text)) if len(w) > 2 and w not in STOPWORDS]


def validate_query(normalized: str, *, min_length: int, max_length: int) -> None:
    if not normalized:
        raise QueryValidationError("Enter a search query.")
    if len(normalized) < min_length:
        raise QueryValidationError(f"Search queries need at least {min_length} characters.")
    if len(normalized) > max_length:
        raise QueryValidationError(
            f"Search queries can be up to {max_length} characters. Yours has {len(normalized)}."
        )
    if not any(ch.isalnum() for ch in normalized):
        raise QueryValidationError("Search queries need at least one letter or number.")


# ---------------------------------------------------------------------- spelling
def match_case(template: str, word: str) -> str:
    if template.isupper() and len(template) > 1:
        return word.upper()
    if template[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: a swap of two neighbouring letters
    ('pyhton' -> 'python') counts as one edit, like a typo does."""
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


class SpellingDetector:
    """Offline dictionary-based detector. Shared across requests and read-only
    after construction, so it's safe to use from multiple threads."""

    MAX_WORDS_CHECKED = 30
    MAX_WORD_LENGTH = 20

    def __init__(self, vocabulary_file: Path | None = None):
        self._spell = SpellChecker(distance=2)
        if vocabulary_file and vocabulary_file.exists():
            words = [
                line.strip().lower()
                for line in vocabulary_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
            self._spell.word_frequency.load_words(words)

    def _should_skip(self, token: str) -> bool:
        return (
            len(token) < 4  # 3-letter tokens are usually abbreviations: img, pdf, hrs, cfo
            or len(token) > self.MAX_WORD_LENGTH
            or not token.isascii()            # other languages: leave to the AI
            or any(ch.isdigit() for ch in token)  # versions, model numbers: "i7", "v2"
            or (token.isupper() and len(token) > 1)  # acronyms: "AWS", "SQL"
            or bool(_SKIP_CHARS.search(token))   # emails, urls, hashtags, "c++", "node.js"
            or not _PLAIN_WORD.fullmatch(token)
        )

    def detect(self, text: str, extra_vocabulary: set[str] | None = None) -> dict[str, list[str]]:
        extra = extra_vocabulary or set()
        found: dict[str, list[str]] = {}
        for raw in text.split()[: self.MAX_WORDS_CHECKED]:
            token = raw.strip(_STRIP_CHARS)
            if not token or self._should_skip(token):
                continue
            word = token.lower()
            if word in found or word in extra or self._spell.known([word]):
                continue
            candidates = [c for c in (self._spell.candidates(word) or set()) if c != word]
            found[word] = sorted(candidates, key=lambda c: self._rank(word, c))[:3]
        return found

    def _rank(self, word: str, candidate: str) -> tuple:
        """Closest first; then keep the first/last letters (people rarely mistype
        those, so 'developr' -> 'developer', not the more common 'develop');
        then the more common word."""
        return (
            edit_distance(word, candidate),
            candidate[-1] != word[-1],
            candidate[0] != word[0],
            -self._spell.word_usage_frequency(candidate),
        )

    @staticmethod
    def apply(text: str, corrections: dict[str, str]) -> str:
        """Replace whole words only, preserving the user's capitalisation."""
        if not corrections:
            return text
        lookup = {k.lower(): v for k, v in corrections.items()}

        def _replace(match: re.Match) -> str:
            word = match.group(0)
            fix = lookup.get(word.lower())
            return match_case(word, fix) if fix else word

        return _WORD.sub(_replace, text)


def build_vocabulary(interests: list[str], role: str | None, preferences: dict[str, str],
                     activity_texts: list[str]) -> set[str]:
    """Words from the user's own profile/activity are never flagged as misspelled
    (e.g. 'pyspark' for a data engineer)."""
    sources = [*interests, role or "", *preferences.values(), *activity_texts]
    return {w.lower() for text in sources for w in _WORD.findall(text) if len(w) > 2}


def preprocess(raw: str, *, min_length: int, max_length: int, detector: SpellingDetector,
               vocabulary: set[str]) -> PreprocessResult:
    normalized, notes = normalize_query(raw or "")
    validate_query(normalized, min_length=min_length, max_length=max_length)
    return PreprocessResult(
        original=raw,
        normalized=normalized,
        dedup_key=make_dedup_key(normalized),
        word_count=len(normalized.split()),
        notes=notes,
        misspellings=detector.detect(normalized, vocabulary),
    )
