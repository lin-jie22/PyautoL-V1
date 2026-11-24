def parse_duration(s):
    if not s:
        return None
    try:
        s = s.lower()
        total_seconds = 0
        
        # Check for hours
        if 'h' in s:
            hours_part = s.split('h')
            hours_str = hours_part[0]
            remaining_part = hours_part[1] if len(hours_part) > 1 else ""
            
            hours = int(hours_str)
            total_seconds += hours * 3600
            
            # Process remaining part (could be 'm' and/or 's')
            if remaining_part:
                if 'm' in remaining_part:
                    m_str, s_str = remaining_part.split('m')
                    m_count = int(m_str) if m_str else 0
                    total_seconds += m_count * 60
                    
                    if s_str and 's' in s_str or s_str.endswith('s'):
                        # Check if there are seconds after minutes
                        s_parts = s_str.split('s')
                        sec_str = s_parts[-1]
                        seconds = int(sec_str) if sec_str else 0
                        total_seconds += seconds
                elif 's' in remaining_part:
                    # Only seconds, no minutes
                    s_count = int(remaining_part.replace('s', ''))
                    total_seconds += s_count
                else:
                    # Invalid format after hours without m or s
                    return None
        
        # Check for minutes without hours
        elif 'm' in s:
            m_str = s.split('m')[0]
            if not m_str.strip():
                # Try to parse as seconds if only one part and no leading number? 
                # Actually, if split gives empty string at start, it means 'm' is at start.
                # But the original logic was flawed here. Let's re-parse carefully.
                pass
            
            parts = s.split('m')
            minutes_str = parts[0]
            remaining = parts[1] if len(parts) > 1 else ""
            
            minutes = int(minutes_str) if minutes_str.strip() else 0
            
            # If there is no 'm' at the very beginning but it's inside, split handles it? 
            # Wait, if input is "30m", parts will be ['30', '']. We need to handle this.
            
            if remaining:
                if 's' in remaining or remaining.endswith('s'):
                    # Check if there are seconds part
                    s_count = int(remaining.replace('m', '').split('s')[0].strip()) if 's' in remaining else 0
                    if '.' not in remaining and 'm' not in remaining[1:]:
                        total_seconds += minutes * 60 + int(remaining.lstrip('ms').strip())
                    else:
                        s_part = remaining.split('s')
                        if len(s_part) > 1:
                            seconds = int(s_part[-1])
                            total_seconds += seconds
                else:
                     # Only minutes present
                    total_seconds += minutes * 60
        
        # Check for seconds only
        elif 's' in s and 'm' not in s and 'h' not in s:
            try:
                secs = int(s.split('s')[0])
                return secs
            except ValueError:
                pass
                
        return total_seconds

    except ValueError:
        return None