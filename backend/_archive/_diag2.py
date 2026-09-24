# -*- coding: utf-8 -*-
"""直接测试 _keep_infobox 和 _strip_noise。"""
from bs4 import BeautifulSoup
from src.data_processor import _keep_infobox, _strip_noise, extract_sections, SKIP_CLASSES
from src.wiki_scraper import WikiScraper

s = WikiScraper()
p = s.get_page_html('金心')
html = p['html']

soup = BeautifulSoup(html, "lxml")
infoboxes = soup.find_all(class_="infobox")
print("infobox 数量:", len(infoboxes))
for i, t in enumerate(infoboxes[:5]):
    keep = _keep_infobox(t)
    has_ach = bool(t.select(".achievement-box"))
    has_bg = bool(t.select(".bg-primary"))
    print("  infobox#%d keep=%s achievement-box=%s bg-primary=%s 含挑战#21=%s" % (
        i, keep, has_ach, has_bg, '挑战#21' in t.get_text()))

# 用 _strip_noise 后再看是否还在
soup2 = BeautifulSoup(html, "lxml")
_strip_noise(soup2)
print("\n_strip_noise 后 含[挑战#21]:", '挑战#21' in soup2.get_text())
