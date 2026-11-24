def parse_duration(s):
    if not s:
        return None
    try:
        s = s.lower()
        # Handle seconds only
        parts_s = s.split('s')
        if len(parts_s) == 2 and parts_s[0].isdigit():
            return int(parts_s[0])
        
        # Handle hours only (no minutes or seconds attached like '1h')
        parts_h = s.split('h')
        if len(parts_h) == 2:
            # Check if there's anything after 'h' that isn't 'm' or 's'
            suffix = parts_h[1]
            if suffix.lstrip('-').isdigit():
                return int(parts_h[0]) * 3600
        
        # Handle minutes only (no seconds attached like '10m')
        parts_m = s.split('m')
        if len(parts_m) == 2 and not any(part in parts_m[1] for part in ['h', 's', 'min']):
            return int(parts_m[0]) * 60
            
        # Handle hours and minutes: XhYm (e.g., '1h30m')
        parts_hm = s.split('h')
        if len(parts_hm) >= 2 and 'm' in parts_hm[1]:
            hours_part = parts_hm[0]
            min_hours_part = parts_hm[1].split('m')[0]
            total_seconds = int(hours_part) * 3600 + int(min_hours_part) * 60
            return total_seconds
            
        # Handle hours, minutes, and seconds: XhYmZs (e.g., '1h2m3s')
        parts_hms = s.split('h')
        if len(parts_hms) >= 2 and 'm' in parts_hms[1]:
            h_part = parts_hms[0]
            rest = parts_hms[1]
            
            m_parts = rest.split('m')
            if len(m_parts) >= 2:
                m_part = int(m_parts[0])
                rest_after_m = m_parts[1]
                
                s_parts = rest_after_m.split('s')
                if len(s_parts) == 2:
                    seconds_part = int(s_parts[0])
                    return h_part * 3600 + m_part * 60 + seconds_part
                    
    except ValueError:
        pass
    
    return None