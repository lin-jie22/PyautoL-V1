def parse_duration(s):
    if not s or not isinstance(s, str):
        return None
    try:
        s = s.strip().lower()
        if not s:
            return None
        
        total_seconds = 0
        
        # Check for hours (h)
        parts_h = s.split('h')
        if len(parts_h) > 1:
            hours = int(parts_h[0])
            remaining = parts_h[1].strip()
            total_seconds += hours * 3600
            
            if remaining:
                # Check for minutes in the remaining part
                if 'm' in remaining:
                    # Remove anything after 'm' to avoid parsing extra characters
                    remaining_minutes_str = remaining.split('m')[0]
                    minutes = int(remaining_minutes_str) if remaining_minutes_str else 0
                    total_seconds += minutes * 60
        
        else:
            # Check for minutes (m)
            parts_m = s.split('m')
            if len(parts_m) > 1:
                mins_part = parts_m[0]
                minutes = int(mins_part) if mins_part.isdigit() else None
                if minutes is not None:
                    total_seconds += minutes * 60
                
                # Check if there is a seconds part after 'm' (like 10m5s or just m followed by s)
                if len(parts_m) > 1 and parts_m[1]:
                    remaining_after_m = parts_m[1].strip()
                    if not remaining_after_m.startswith('h'): # Avoid re-checking hours
                        if remaining_after_m.endswith('s') or 's' in remaining_after_m:
                            secs_str = remaining_after_m.split('s')[0]
                            total_seconds += int(secs_str) if secs_str.isdigit() else None
                
                # Check for seconds (s) even without minutes
                parts_s = s.split('s')
                if len(parts_s) > 1:
                    secs_part = parts_s[0]
                    if 'm' not in parts_s[0]: # Ensure it's not captured as part of minute check above incorrectly
                         seconds = int(secs_part) if secs_part.isdigit() else None
                         total_seconds += seconds
        
        return total_seconds
        
    except (ValueError, IndexError):
        return None