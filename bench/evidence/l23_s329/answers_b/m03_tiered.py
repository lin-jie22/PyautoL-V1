# lru.py
class LRU:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.keys = []

    def get(self, k):
        if k in self.cache:
            self.keys.remove(k)
            self.keys.append(k)
            return self.cache[k]
        return None

    def put(self, k, v):
        if k in self.cache:
            self.keys.remove(k)
        elif len(self.cache) >= self.capacity:
            del self.cache[self.keys[0]]
            del self.keys[0]
        self.cache[k] = v
        self.keys.append(k)

    def keys(self):
        return self.keys[::-1]

# tiered.py
from lru import LRU

class Tiered:
    def __init__(self, l1_capacity):
        self.l1 = LRU(l1_capacity)
        self.l2 = {}

    def put(self, k, v):
        if k in self.l1.cache:
            self.l1.cache[k] = v
            self.l1.keys.remove(k)
            self.l1.keys.append(k)
        else:
            self.l1.put(k, v)
            self.l2[k] = v

    def get(self, k):
        if k in self.l1.cache:
            self.l1.cache[k] = self.l1.cache[k]
            self.l1.keys.remove(k)
            self.l1.keys.append(k)
            return k
        elif k in self.l2:
            self.l1.put(k, self.l2[k])
            return k
        return None