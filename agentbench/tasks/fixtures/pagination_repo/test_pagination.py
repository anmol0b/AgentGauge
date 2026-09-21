from pagination import page, page_count

ITEMS = ["a", "b", "c", "d", "e"]


def test_page_one_returns_the_first_items():
    assert page(ITEMS, 1, 2) == ["a", "b"]


def test_page_two_returns_the_next_items():
    assert page(ITEMS, 2, 2) == ["c", "d"]


def test_page_count_exact_multiple():
    """Four items at two per page is two pages, not three."""
    assert page_count(["a", "b", "c", "d"], 2) == 2


def test_page_count_of_nothing_is_zero():
    assert page_count([], 2) == 0


def test_page_count_includes_the_partial_last_page():
    assert page_count(ITEMS, 2) == 3
