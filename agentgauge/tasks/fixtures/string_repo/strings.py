def shout(text):
    return text.upper() + "!"


def title_case(text):
    # BUG: uppercases everything instead of capitalizing each word
    return text.upper()


def reverse_words(sentence):
    # BUG: reverses characters, not word order
    return sentence[::-1]


def count_vowels(text):
    return sum(1 for c in text.lower() if c in "aeiou")
