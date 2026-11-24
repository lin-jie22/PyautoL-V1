def parse_duration(s):
    if not s:
        return None
    
    # 定义时间单位及其对应的秒数
    time_units = {'s': 1, 'm': 60, 'h': 3600}
    
    # 检查字符串是否包含合法的时间单位
    for unit in time_units:
        if unit in s:
            # 提取数值部分
            num = s.split(unit)[0]
            # 检查数值部分是否合法
            if num.isdigit():
                # 尝试转换为整数
                value = int(num)
                return value * time_units[unit]
    
    return None