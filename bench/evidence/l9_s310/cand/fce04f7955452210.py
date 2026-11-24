import re

def parse_duration(s):
    if not s:
        return None
    m = re.match(r'(?P<hours>\d+)?h?(?P<minutes>\d+)?m?(?P<seconds>\d+)s?', s)
    if not m:
        return None
    hours, minutes, seconds = m.group('hours', 'minutes', 'seconds')
    hours = int(hours) if hours else 0
    minutes = int(minutes) if minutes else 0
    seconds = int(seconds) if seconds else 0
    return hours * 3600 + minutes * 60 + seconds