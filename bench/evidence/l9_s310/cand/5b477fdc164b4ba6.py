import re

def parse_duration(s):
    if not s:
        return None
    match = re.match(r'(\d+)?\s*h:?(\d+)?\s*m:?(\d+)?\s*s?|(\d+)', s)
    if not match:
        return None
    hours, minutes, seconds = 0, 0, 0
    if match.group(1):
        hours = int(match.group(1))
    if match.group(2):
        minutes = int(match.group(2))
    if match.group(3):
        seconds = int(match.group(3))
    return hours * 3600 + minutes * 60 + seconds