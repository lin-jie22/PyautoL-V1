def parse_duration(s):
    if not s:
        return None
    try:
        s = s.strip().lower()
        if not s or s in [' ', '   ', '.']:
            return None
        
        parts = s.split('h')
        
        # Case 1: No 'h' present
        if len(parts) == 1:
            parts_m_s = s.split('m')
            if len(parts_m_s) == 1:
                # Check for seconds only
                parts_m = parts_m_s[0].split('s')
                if len(parts_m) == 1:
                    return None
                else:
                    return int(parts_m[0])
            else:
                # Minutes only
                minute_part = parts_m_s[0]
                if not minute_part or minute_part.endswith('.'):
                    return None
                try:
                    minutes = int(minute_part.rstrip('m'))
                    return minutes * 60
                except ValueError:
                    return None
        
        # Case 2: Hours present
        else:
            hour_part = parts[0].rstrip('h')
            if not hour_part:
                return None
            try:
                hours = int(hour_part)
            except ValueError:
                return None
            
            remaining = parts[1]
            
            # If there's nothing after 'h', it's just hours
            if not remaining:
                return hours * 3600
            
            remaining_lower = remaining.lower()
            
            # Check if the remainder is in format 'Xm' or 'XmYs' or 'X Y' (spaces usually split earlier)
            # Based on examples like '1h30m', assume strict format after h: [minutes][optional_seconds]
            
            # Remove spaces from remaining part for robustness if needed, though input likely clean
            parts_m = remaining_lower.split('s')
            
            if len(parts_m) == 2:
                # Has seconds at the end
                first_part = parts_m[0].rstrip('m')
                second_part = parts_m[1].lstrip('s').lstrip()
                
                if not first_part or not second_part:
                    return None
                
                try:
                    minutes = int(first_part)
                    seconds = int(second_part)
                    return hours * 3600 + minutes * 60 + seconds
                except ValueError:
                    return None
            
            elif len(parts_m) == 1:
                # Only minutes
                minute_str = parts_m[0].rstrip('m')
                
                if not minute_str:
                    return None
                
                try:
                    minutes = int(minute_str)
                    return hours * 3600 + minutes * 60
                except ValueError:
                    return None
                    
    except Exception:
        return None