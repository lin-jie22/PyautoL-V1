def parse_duration(s):
    if not s:
        return None
    try:
        s = s.lower()
        if 'h' in s:
            hours_part, remainder = s.split('h', 1)
            hours = int(hours_part)
            if '/' in remainder or '-' in remainder:
                raise ValueError("Invalid format")
            elif remainder.strip():
                if '/' in remainder:
                    mins_str, secs_str = remainder.split('/')
                    minutes = int(mins_str)
                    seconds = int(secs_str)
                    return hours * 3600 + minutes * 60 + seconds
                elif '-' in remainder:
                    mins_part, secs_part = remainder.split('-')
                    minutes = int(mins_part.replace('m', '').strip())
                    seconds = int(secs_part.replace('s', '').strip())
                    return hours * 3600 + minutes * 60 + seconds
                else:
                    rest = remainder.strip()
                    if 'm' in rest:
                        m_str = rest.split('m')[0]
                        minutes = int(m_str)
                        return hours * 3600 + minutes * 60
                    elif 's' in rest:
                        s_str = rest.split('s')[0]
                        seconds = int(s_str)
                        return hours * 3600 + seconds
            else:
                return hours * 3600
        else:
            if '/' in s:
                mins_str, secs_str = s.split('/')
                minutes = int(mins_str)
                seconds = int(secs_str)
                return minutes * 60 + seconds
            elif '-' in s:
                mins_part, secs_part = s.split('-')
                minutes = int(mins_part.replace('m', '').strip())
                seconds = int(secs_part.replace('s', '').strip())
                return minutes * 60 + seconds
            else:
                if 'm' in s:
                    m_str = s.split('m')[0]
                    return int(m_str) * 60
                elif 's' in s:
                    s_str = s.split('s')[0]
                    return int(s_str)
    except (ValueError, IndexError):
        return None