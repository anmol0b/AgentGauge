from memo import Memo


def test_caches_repeated_calls():
    memo = Memo(lambda a: a * 2)
    assert memo(3) == 6
    assert memo(3) == 6
    assert memo.calls == 1


def test_distinguishes_later_arguments():
    memo = Memo(lambda a, b: a + b)
    assert memo(1, 2) == 3
    assert memo(1, 5) == 6


def test_distinguishes_keyword_arguments():
    memo = Memo(lambda a, scale=1: a * scale)
    assert memo(2, scale=1) == 2
    assert memo(2, scale=10) == 20


def test_clear_empties_the_cache():
    memo = Memo(lambda a: a * 2)
    memo(3)
    memo.clear()
    memo(3)
    assert memo.calls == 2
