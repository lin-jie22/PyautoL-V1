def parse_duration(s):
    if not s:
        return None
    
    # 定义时间单位及其对应的秒数
    time_units = {'s': 1, 'm': 60, 'h': 3600}
    
    i = 0
    total_seconds = 0
    
    while i < len(s):
        if s[i].isdigit():
            # 读取连续的数字
            num_str = ""
            while i < len(s) and s[i].isdigit():
                num_str += s[i]
                i += 1
            value = int(num_str)
            
            # 尝试寻找下一个单位
            if i < len(s) and s[i].lower() in time_units:
                unit = s[i].lower()
                seconds = time_units[unit]
                total_seconds += value * seconds
                i += 1
            else:
                return None
        else:
            return None
    
    return total_seconds