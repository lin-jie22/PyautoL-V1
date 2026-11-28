def parse_pairs(s):
    pairs = {}
    key = ''
    value = ''
    in_quote = False
    quote_char = ''
    for c in s + ';':  # 加上一个分号确保最后一个条目能被处理
        if c == '=' and not in_quote:
            key = key.strip()
            if key:
                pairs[key] = value.strip() if not in_quote else value
                key = ''
                value = ''
        elif c == '\'' and not in_quote:
            in_quote = True
            quote_char = c
        elif c == quote_char and in_quote:
            in_quote = False
        elif c == ';' and not in_quote:
            key = key.strip()
            if key:
                pairs[key] = value.strip() if not in_quote else value
                key = ''
                value = ''
        else:
            value += c
    return pairs