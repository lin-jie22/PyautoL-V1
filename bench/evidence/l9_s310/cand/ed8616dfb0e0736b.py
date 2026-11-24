import re

def parse_duration(s):
    if not s:
        return None
    match = re.match(r'^(\d+)?\s*(h|hr|hours|小时)?\s*(\d+)?\s*(m|min|minutes|分钟)?\s*(\d+)?\s*s?$', s)
    if not match:
        return None
    hours, mins, secs = 0, 0, 0
    if match.group(1):
        hours = int(match.group(1))
    if match.group(3):
        mins = int(match.group(3))
    if match.group(5):
        secs = int(match.group(5))
    return hours * 3600 + mins * 60 + secs