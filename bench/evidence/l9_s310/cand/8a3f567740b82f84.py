import re

def parse_duration(s):
    if not s:
        return None
    m = re.match(r'^(\d+)(h|m|s)$', s)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit == 'h':
            return num * 3600
        elif unit == 'm':
            return num * 60
        elif unit == 's':
            return num
    return None