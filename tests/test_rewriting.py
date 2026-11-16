from app.pipeline.rewriting import check_rewrite


def test_good_rewrite_passes():
    ok, _, cleaned = check_rewrite('"intermediate SQL courses for data analysts."', "sql courses", max_words=25)
    assert ok and cleaned == "intermediate SQL courses for data analysts"


def test_rewrite_that_drops_key_terms_is_rejected():
    ok, reason, _ = check_rewrite("buy cheap crypto now", "python course", max_words=25)
    assert not ok and "key search terms" in reason


def test_too_long_rewrite_is_rejected():
    ok, reason, _ = check_rewrite("python " * 30, "python", max_words=25)
    assert not ok and "longer than" in reason


def test_empty_rewrite_is_rejected():
    ok, reason, _ = check_rewrite('  "" ', "python", max_words=25)
    assert not ok and "empty" in reason


def test_plural_counts_as_kept():
    ok, _, _ = check_rewrite("online course catalog for analysts", "courses", max_words=25)
    assert ok
