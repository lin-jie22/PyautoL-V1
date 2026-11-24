def parse_duration(s):
    if not s:
        return None
    
    s = s.strip()
    if 'h' in s:
        hours, mins = s.split('h')
        if 'm' in mins:
            mins = mins.strip('m')
            return int(hours) * 3600 + int(mins)
        else:
            return int(hours) * 3600
    elif 'm' in s:
        mins, secs = s.split('m')
        if 's' in secs:
            secs = secs.strip('s')
            return int(mins) * 60 + int(secs)
        else:
            return int(mins) * 60
    elif 's' in s:
        return int(s.strip('s'))
    else:
        return None