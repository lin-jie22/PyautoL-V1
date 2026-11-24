import re

def parse_duration(s):
    if not s:
        return None
    match = re.match(r'(\d+)(h|m|s)', s)
    if not match:
        return None
    num, unit = match.groups()
    if unit == 'h':
        return int(num) * 3600
    elif unit == 'm':
        return int(num) * 60
    elif unit == 's':
        return int(num)