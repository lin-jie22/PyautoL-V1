import re

def parse_duration(s):
    if not s:
        return None
    match = re.match(r'(?P<hours>\d+)h(?P<minutes>\d+)m|(?P<seconds>\d+)s', s)
    if not match:
        return None
    hours = int(match.group('hours')) * 3600 if match.group('hours') else 0
    minutes = int(match.group('minutes')) * 60 if match.group('minutes') else 0
    seconds = int(match.group('seconds')) if match.group('seconds') else 0
    return hours + minutes + seconds