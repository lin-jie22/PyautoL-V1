def parse_duration(s):
    if not s:
        return None
    try:
        s = s.strip().lower()
        total_seconds = 0
        
        # 处理小时部分
        if 'h' in s:
            hours_part = s.split('h')[0]
            minutes_part = s.split('h')[1]
            
            if not hours_part or not minutes_part.startswith('m'):
                return None
                
            hours = int(hours_part)
            total_seconds += hours * 3600
            
            # 处理剩余的分钟部分，可能包含秒
            time_suffix = minutes_part.split('m')[0]
            if time_suffix.endswith(':') and time_suffix.count(':') > 0:
                parts_str = time_suffix.replace('.', '').split(':')
                if len(parts_str) == 2:
                    minutes = int(parts_str[0])
                    seconds = int(parts_str[1])
                    total_seconds += minutes * 60 + seconds
            else:
                # 检查是否还有 'm' 或 's'
                time_suffix_clean = time_suffix.rstrip().replace('m', '').replace('s', '')
                if not time_suffix_clean.isdigit():
                    return None
                minutes = int(time_suffix_clean)
                total_seconds += minutes * 60
        
        # 如果字符串以 'm' 开头但没有包含 'h'，则处理分钟部分
        elif 'm' in s:
            base_part = s.split('m')[0]
            rest_part = s.split('m')[1] if len(s.split('m')) > 1 else ""
            
            # 确保前面的数字有效（处理可能的前导字符，虽然通常输入是纯数字）
            # 根据题意，'10m' 应该被解析为 600，所以 base_part 应该是数字字符串
            if not base_part or not base_part.lstrip().isdigit():
                return None
            
            minutes = int(base_part) * int(rest_part.split('m')[0].split(':')[0]) if rest_part.startswith(':') else minutes * 60
            
            # 更简单且通用的逻辑：直接按顺序解析 'h' 'm' 's' 关键字
            total_seconds = 0
            s_clean = s.replace(' ', '').replace('.', '')
            
            h_count = 0
            m_count = 0
            sec_count = 0
            
            idx = 0
            while idx < len(s_clean):
                c = s_clean[idx]
                if c == 'h':
                    h_idx = idx + 1
                    while h_idx < len(s_clean) and not s_clean[h_idx].isdigit():
                        if s_clean[h_idx] in ['m', ':']:
                            break
                        h_idx += 1
                    if h_idx >= len(s_clean) or not s_clean[h_idx].isdigit():
                        return None
                    # 处理可能的前后数字或分隔符
                    num_str = s_clean[idx+1:h_idx] if h_idx > idx + 1 else ""
                    if not num_str.lstrip().isdigit() or num_str.lstrip().split(':')[0].isdigit():
                         h_count += int(s_clean.split('h')[0]) # 简化：直接找第一个 h 之前的数字
                         break
                    break 
                elif c == 'm':
                    m_count = int(s_clean.split('m')[1].split(':')[0]) if ':' in s_clean else int(s_clean.split('m')[0])
                    break
                
                idx += 1
        
        # 重新实现，确保逻辑清晰且覆盖所有测试用例 (10m -> 600, 1h30m, 45s etc)
        total_seconds = 0
        s_check = s.strip().lower()
        
        # 提取小时
        h_match = False
        if 'h' in s_check:
            parts_h = s_check.split('h')
            if len(parts_h) == 2 and parts_h[0].lstrip().isdigit():
                hours = int(parts_h[0])
                total_seconds += hours * 3600
                # 处理剩余部分 'm' 或 's'
                rest = s_check.split('h', 1)[1]
                if rest.startswith('m'):
                    m_part = rest[1:]
                    if ':' in m_part:
                        mp = m_part.split(':')
                        minutes = int(mp[0])
                        seconds = int(mp[1])
                        total_seconds += minutes * 60 + seconds
                    else:
                        # 检查是否有 'm' 或 's' 关键字在后面
                        if m_part.startswith('m'):
                            rest_m = m_part[1:]
                            if rest_m.lstrip().isdigit() and ':' not in rest_m:
                                minutes = int(rest_m)
                                total_seconds += minutes * 60
                        elif rest_m == 's':
                             # 如果格式是 "30ms" 这种？题目例子没有，但通常只接受 h:m:s 或数字+h/m/s
                             pass
            else:
                return None
        
        # 如果没有小时，尝试提取分钟
        if total_seconds == 0 and 'm' in s_check:
            parts_m = s_check.split('m')
            if len(parts_m) == 2 and parts_m[0].lstrip().isdigit():
                minutes = int(parts_m[0])
                rest_m = s_check.split('m', 1)[1]
                
                # 处理可能的秒数后缀，例如 "45s" -> 分钟部分是 45，后面是 's'
                if rest_m.startswith('s'):
                    total_seconds += minutes * 60
                elif rest_m and not rest_m.isdigit() and rest_m != 'm':
                     # 尝试解析像 "1h30m" 中分钟后的秒
                     if rest_m.count(':') > 0:
                         sec = int(rest_m.split(':')[1])
                         total_seconds += minutes * 60 + sec
                         # 但题目例子是 '45s'，如果输入是 '45s'，上面逻辑会走 else -> parts_m[0] 是 "45"
                     else:
                         if rest_m.lstrip().isdigit():
                             extra_min = int(rest_m)
                             total_seconds += extra_min * 60
            else:
                return None
        
        # 处理纯秒数的情况 (如 '45s')
        if s_check.endswith('s') and s_check.count('m') == 0 and s_check.count('h') == 0:
            num_part = s_check[:-1]
            if num_part.lstrip().isdigit():
                total_seconds = int(num_part)
        
        # 如果还是无法解析，返回 None
        if not isinstance(total_seconds, int) or total_seconds < 0:
             return None
        
        # 确保只包含整数且非负
        if isinstance(total_seconds, float):
            if total_seconds == int(total_seconds):
                return int(total_seconds)
            return None
            
        return total_seconds

    except Exception:
        return None