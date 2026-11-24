import re

def parse_duration(s):
    if not s:
        return None
    match = re.match(r'((\d+)h)?((\d+)m)?((\d+)s)?', s)
    if not match:
        return None
    hours, minutes, seconds = 0, 0, 0
    if match.group(2):
        hours = int(match.group(2))
    if match.group(4):
        minutes = int(match.group(4))
    if match.group(6):
        seconds = int(match.group(6))
    return hours * 3600 + minutes * 60 + seconds