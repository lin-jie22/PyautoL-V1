class Limiter:
    def __init__(self, limit, window):
        self.limit = limit
        self.window = window
        self.records = {}

    def hit(self, key, now):
        if key not in self.records:
            self.records[key] = {}
        if now - self.window not in self.records[key]:
            self.records[key][now - self.window] = 0
        count = self.records[key][now - self.window] + 1
        if count <= self.limit:
            self.records[key][now] = count
            return True
        return False

    def count(self, key, now):
        if key not in self.records or now - self.window not in self.records[key]:
            return 0
        return self.records[key][now - self.window]