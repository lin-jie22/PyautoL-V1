def parse_duration(s):
    if not s:
        return None
    try:
        s = s.lower()
        hours_part, rest_h = s.rsplit('h', 1)
        hours = int(hours_part) if hours_part else 0
        
        # Check remaining part for minutes and seconds
        minutes_part, rest_m = rest_h.rsplit('m', 1)
        minutes = int(minutes_part) if minutes_part else 0
        
        seconds_part, _ = rest_m.rsplit('s', 1)
        seconds = int(seconds_part) if seconds_part else 0
        
        return hours * 3600 + minutes * 60 + seconds
    except (ValueError, AttributeError):
        return None