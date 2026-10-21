"""A tiny memoizing cache used by the report builders."""


class Memo:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0
        self._store = {}

    def _key(self, args, kwargs):
        # BUG: only the first positional argument is part of the key, so two
        # calls that differ in any later argument collide.
        return args[0] if args else None

    def __call__(self, *args, **kwargs):
        key = self._key(args, kwargs)
        if key not in self._store:
            self.calls += 1
            self._store[key] = self.fn(*args, **kwargs)
        return self._store[key]

    def clear(self):
        self._store.clear()
