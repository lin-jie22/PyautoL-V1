def parse_duration(s):
    if not s:
        return None
    
    # 定义时间单位及其对应的秒数
    time_units = {'s': 1, 'm': 60, 'h': 3600}
    
    # 检查字符串是否包含合法的时间单位
    if any(s.endswith(unit) for unit in time_units):
        # 提取数值部分
        num = s[:-1]
        # 尝试转换为整数
        try:
            value = int(num)
            return value * time_units[s[-1]]
        except ValueError:
            return None
    
    return None