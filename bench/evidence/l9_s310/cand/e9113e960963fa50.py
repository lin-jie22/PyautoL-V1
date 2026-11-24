def parse_duration(s):
    if not s or not isinstance(s, str):
        return None
    
    s = s.strip().lower()
    
    def parse_part(part_str, unit_factor):
        # 尝试解析 '10' + optional 'm'/'s' -> seconds
        parts = part_str.split(' ')
        if len(parts) == 1:
            try:
                return int(parts[0]) * unit_factor
            except ValueError:
                pass
        else:
            # Handle cases like '10 m' or similar with space
            try:
                value = parts[0]
                unit = parts[1].rstrip('m').rstrip('s') if len(parts) > 1 else ''
                if unit_factor == 60 and (unit == 'm' or unit.endswith('m')):
                    return int(value) * 60
                elif unit_factor == 1 and (unit == 's' or unit.endswith('s')):
                    return int(value)
            except ValueError:
                pass
        return None

    try:
        # Strategy: split by h, m, s separately in different branches to avoid complex regex
        if 'h' in s:
            hours_str = s.split('h')[0].strip()
            rest = s[s.rfind('h') + 1:]
            
            hours = int(hours_str) if hours_str else 0
            
            if not rest.strip():
                return hours * 3600
            
            rest_clean = rest.strip().lower()
            total_seconds = hours * 3600
            
            if 'm' in rest_clean:
                m_part = rest_clean.split('m')[0].strip()
                mins = int(m_part) if m_part else 0
                total_seconds += mins * 60
            
            elif 's' in rest_clean:
                s_part = rest_clean.split('s')[0].strip()
                secs = int(s_part) if s_part else 0
                total_seconds += secs
        
        elif 'm' in s:
            # Could be '10m' or '5h30m' but we handled h above, so here mainly '10m'
            parts = s.split('m')
            mins_str = parts[0].strip()
            rest_min = parts[-1] if len(parts) > 1 else ""
            
            minutes = int(mins_str) if mins_str else 0
            
            # Check for seconds following m if not just 'm'
            # Actually, simpler: if we are in this block and no 'h', assume it's minutes primarily
            # But need to handle potential seconds if format was loose? The problem implies strict 'Xm Ys' or 'Xh Ym Zs'
            # However, test case 10m -> 600 suggests simple split works.
            # Let's refine: remove h first, then m, then s logic could be messy.
            # Let's restart with a cleaner unified approach based on the structure.
            pass 
        
    except ValueError:
        return None
    
    # Re-implementing cleanly to handle all cases robustly without deep recursion or nested try-except hell
    # Algorithm:
    # 1. Replace spaces just in case, though problem examples don't have them.
    # 2. Use regex or manual scan to find digits and units.
    
    import re
    
    # Remove any spaces to simplify '10 m' if that were a case, but sticking to strict '1h30m' format mostly
    # However, the failed test is '10m'.
    s_clean = s.replace(' ', '')
    
    # Extract numbers and units
    # Pattern: number followed by optional h/m/s
    matches = re.findall(r'\d+[hm]?s?', s_clean) # This might catch partials like '1h' then '2m' -> separate
    
    total_seconds = 0
    last_end_idx = 0
    
    # Better approach: manual tokenization from left to right
    i = 0
    n = len(s)
    
    while i < n:
        if s[i].isdigit():
            # Parse number
            num_start = i
            while i < n and s[i].isdigit():
                i += 1
            num_str = s[num_start:i]
            num_val = int(num_str)
            
            # Check unit at current position (i points to char after number or end)
            unit_factor = 1
            if i < n:
                c = s[i].lower()
                if c == 'h':
                    total_seconds += num_val * 3600
                    i += 1
                elif c == 'm':
                    total_seconds += num_val * 60
                    i += 1
                elif c == 's':
                    total_seconds += num_val
                    i += 1
                # If no unit found after number, it's invalid for this specific format unless it's just a raw number (which shouldn't happen per examples like '45s')
            else:
                # Number at end with no unit -> invalid per examples (e.g. '45' without s)
                return None
                
        else:
            i += 1
            
    return total_seconds if total_seconds >= 0 else None