import pytest

from cart import Cart


def test_add_single_item():
    cart = Cart()
    cart.add("apple", 1.5)
    assert cart.total() == 1.5


def test_total_counts_quantity():
    cart = Cart()
    cart.add("apple", 1.5, quantity=4)
    assert cart.total() == 6.0


def test_discount_subtracts():
    cart = Cart()
    cart.add("book", 20.0)
    assert cart.apply_discount(0.25) == pytest.approx(15.0)
