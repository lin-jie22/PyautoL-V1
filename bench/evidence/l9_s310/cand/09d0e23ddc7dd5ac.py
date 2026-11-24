def parse_duration(s):
    if not s:
        return None
    
    # 定义时间单位及其对应的秒数
    time_units = {'s': 1, 'm': 60, 'h': 3600}
    
    # 遍历时间单位
    for unit, seconds in time_units.items():
        # 检查字符串是否以单位开头
        if s.endswith(unit):
            # 提取数值部分
            num = s[:-1]
            # 尝试转换为整数
            try:
                value = int(num)
                return value * seconds
            except ValueError:
                return None
    
    return None