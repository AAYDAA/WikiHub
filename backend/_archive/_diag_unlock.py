# -*- coding: utf-8 -*-
"""验证修复: 金心解锁条件是否被提取; 若通过则清旧块并重新入库。"""
import json
from src.wiki_scraper import WikiScraper
from src.data_processor import extract_sections, split_chunks, _prefix_text

s = WikiScraper()
p = s.get_page_html('金心')
print('抓取标题:', p['title'], '| HTML长度:', len(p['html']))

secs = extract_sections(p['html'], p['title'], p['url'], '')
all_text = ''.join(c['text'] for c in secs)
print('含[挑战#21]:', '挑战#21' in all_text)
print('含[超超超超超大层]:', '超超超超超大层' in all_text)
print('含[金边环绕]:', '金边环绕' in all_text)

gold = [c for c in secs if '金心' in c['section']]
print('金心小节块数:', len(gold))
for c in gold[:3]:
    print('  [%s] len=%d' % (c['section'], len(c['text'])))
    print('    ', c['text'][:150].replace(chr(10), ' '))
