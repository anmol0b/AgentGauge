"""Pagination helpers. Pages are 1-indexed."""


def page(items, page_number, per_page):
    start = (page_number - 1) * per_page
    return items[start:start + per_page]


def page_count(items, per_page):
    # BUG: integer division drops the final partial page.
    return len(items) // per_page
