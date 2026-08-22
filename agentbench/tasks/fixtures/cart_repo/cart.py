class Cart:
    def __init__(self):
        self.items = []  # list of (name, unit_price, quantity)

    def add(self, name, unit_price, quantity=1):
        self.items.append((name, unit_price, quantity))

    def total(self):
        # BUG: ignores quantity
        return sum(price for _, price, _ in self.items)

    def apply_discount(self, rate):
        # BUG: adds instead of subtracts the discount
        return self.total() * (1 + rate)
