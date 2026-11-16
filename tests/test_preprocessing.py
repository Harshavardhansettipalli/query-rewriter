from pathlib import Path

import pytest

from app.pipeline.preprocessing import (
    QueryValidationError,
    SpellingDetector,
    build_vocabulary,
    make_dedup_key,
    normalize_query,
    validate_query,
)

VOCAB = Path(__file__).resolve().parent.parent / "app" / "data" / "domain_vocabulary.txt"


@pytest.fixture(scope="module")
def detector():
    return SpellingDetector(VOCAB)


def test_normalize_whitespace_repeats_and_punctuation():
    text, notes = normalize_query("  python   python  jobs???\u200b ")
    assert text == "python jobs?"
    assert "Removed a repeated word." in notes
    assert "Collapsed repeated punctuation." in notes


def test_normalize_keeps_repeated_numbers():
    text, _ = normalize_query("top 10 10 tips")
    assert text == "top 10 10 tips"


@pytest.mark.parametrize("value, message", [
    ("", "Enter a search query."),
    ("a", "at least 2 characters"),
    ("???", "at least one letter or number"),
    ("x" * 301, "up to 300 characters"),
])
def test_validation_errors(value, message):
    with pytest.raises(QueryValidationError, match=message):
        validate_query(value, min_length=2, max_length=300)


def test_dedup_key_ignores_case_and_punctuation():
    assert make_dedup_key("Python  Jobs!") == make_dedup_key("python jobs")


def test_detects_misspelling(detector):
    found = detector.detect("pyhton developr jobs")
    assert "python" in found["pyhton"]
    assert "developer" in found["developr"]
    assert "jobs" not in found


def test_skips_acronyms_numbers_symbols_and_domain_words(detector):
    found = detector.detect("AWS i7 node.js c++ kubernetes pyspark user@mail.com")
    assert found == {}


def test_profile_vocabulary_is_respected(detector):
    vocab = build_vocabulary(["Snowflake dbtcloud"], None, {}, [])
    assert "dbtcloud" not in detector.detect("dbtcloud setup", vocab)


def test_apply_preserves_case():
    assert SpellingDetector.apply("Pyhton and PYHTON", {"pyhton": "python"}) == "Python and PYTHON"


def test_edit_distance_counts_swaps_as_one():
    from app.pipeline.preprocessing import edit_distance
    assert edit_distance("pyhton", "python") == 1
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3


def test_ranking_prefers_matching_last_letter(detector):
    assert detector.detect("developr")["developr"][0] == "developer"
    assert detector.detect("pyhton")["pyhton"][0] == "python"


def test_short_abbreviations_are_not_flagged(detector):
    assert detector.detect("img pdf hrs cfo") == {}
