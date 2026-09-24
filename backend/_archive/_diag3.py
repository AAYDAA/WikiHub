# -*- coding: utf-8 -*-
"""看金心所在 infobox 的实际结构。"""
from bs4 import BeautifulSoup
from src.wiki_scraper import WikiScraper

s = WikiScraper()
p = s.get_page_html('金心')
html = p['html']
soup = BeautifulSoup(html, "lxml")
infoboxes = soup.find_all(class_="infobox")
print("infobox 总数:", len(infoboxes))

# 找含 挑战#21 的 infobox
for i, t in enumerate(infoboxes):
    if '挑战#21' in t.get_text():
        print("含挑战#21 的 infobox 索引:", i)
        print("  class:", t.get("class"))
        # 打印其所有子元素的 class 列表
        from collections import Counter
        cc = Counter()
        for el in t.find_all(True):
            for c in (el.get("class") or []):
                cc[c] += 1
        print("  子元素 class 统计(前15):", cc.most_common(15))
        # 检查成就链接
        ach = t.find(string=lambda x: x and '成就' in str(x))
        print("  含[成就]文本节点:", bool(ach))
        break
else:
    print("没有任何 infobox 含 挑战#21!")
    # 找挑战#21 在哪个标签里
    i = html.find('挑战#21')
    seg = html[max(0, i-800):i]
    # 向上找最近的含 class 的标签
    import re
    tags = re.findall(r'<(table|div|span|p|li|ul)[^>]*class="[^"]*"', seg)
    print("挑战#21 前最近的带class标签:", tags[-5:] if tags else '无')
