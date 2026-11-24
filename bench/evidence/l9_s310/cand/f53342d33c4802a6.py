def parse_duration(s):
    if not s or not isinstance(s, str):
        return None
    
    try:
        s = s.lower()
        total_seconds = 0
        
        # 处理小时 (h)
        if 'h' in s:
            hours_part = s.split('h')
            if len(hours_part[0]) > 0:
                total_seconds += int(hours_part[0]) * 3600
            s = hours_part[1]
        
        # 处理分钟 (m)
        if 'm' in s:
            minutes_part = s.split('m')
            if len(minutes_part[0]) > 0:
                total_seconds += int(minutes_part[0]) * 60
            s = minutes_part[1]
        
        # 处理秒 (s) - 注意：s 也可能是单位，所以先检查内容
        if 's' in s and len(s.strip()) > 0:
            seconds_part = s.split('s')
            if len(seconds_part[0]) > 0:
                total_seconds += int(seconds_part[0])
        
        return total_seconds
    
    except (ValueError, IndexError):
        return None