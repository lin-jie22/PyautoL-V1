def parse_duration(s):
    if not s:
        return None
    try:
        s = s.lower()
        total_seconds = 0
        
        # 提取小时
        hour_match = re.search(r'(\d+)h', s)
        if hour_match:
            total_seconds += int(hour_match.group(1)) * 3600
            s = s.replace(hour_match.group(), '', 1).strip()
            
        # 提取分钟
        minute_match = re.search(r'(\d+)m', s)
        if minute_match:
            total_seconds += int(minute_match.group(1)) * 60
            s = s.replace(minute_match.group(), '', 1).strip()
            
        # 提取秒
        second_match = re.search(r'(\d+)s', s)
        if second_match:
            total_seconds += int(second_match.group(1))
        
        return total_seconds
    except (ValueError, AttributeError):
        return None