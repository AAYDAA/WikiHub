# -*- coding: utf-8 -*-
"""清理 title=心 的旧块(无解锁条件版本) 并重新抓取入库(含解锁条件)。"""
import json
from src.on_demand import fetch_and_index

p = 'data/isaac_chunks.json'
chunks = json.load(open(p, encoding='utf-8'))
before = len(chunks)
# 清理旧的 title=心(重定向目标页) 块 —— 旧版本不含解锁条件
chunks = [c for c in chunks if c.get('title') != '心']
json.dump(chunks, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('清理 title=心 后: %d -> %d' % (before, len(chunks)))

res = fetch_and_index('金心')
print('抓取结果 ok=%s title=%s n_chunks=%d' % (res['ok'], res.get('title'), res['n_chunks']))

chunks = json.load(open(p, encoding='utf-8'))
print('chunks 总数:', len(chunks))
# 验证解锁条件在库里
hits = [c for c in chunks if '挑战#21' in c.get('text', '')]
print('含[挑战#21] 块数:', len(hits))
for c in hits[:2]:
    print('  [%s] %s' % (c['title'], c['section']))
