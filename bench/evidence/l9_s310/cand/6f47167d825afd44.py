def parse_duration(s):
    if not s:
        return None
    
    # 定义时间单位及其对应的秒数，按优先级排序（小时 > 分钟 > 秒）
    time_units = [('h', 3600), ('m', 60), ('s', 1)]
    
    total_seconds = 0
    remaining_s = s
    
    for unit, seconds in time_units:
        if remaining_s.endswith(unit):
            # 提取数值部分
            try:
                num_str = remaining_s[:-len(unit)].strip()
                value = int(num_str)
                total_seconds += value * seconds
                # 移除已处理的部分，继续检查后续单位
                remaining_s = remaining_s[:remaining_s.find(unit)]
            except ValueError:
                return None
    
    return total_seconds