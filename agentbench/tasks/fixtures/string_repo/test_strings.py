from strings import count_vowels, reverse_words, shout, title_case


def test_shout():
    assert shout("hi") == "HI!"


def test_title_case():
    assert title_case("hello world") == "Hello World"


def test_reverse_words():
    assert reverse_words("one two three") == "three two one"


def test_count_vowels():
    assert count_vowels("benchmark") == 2
