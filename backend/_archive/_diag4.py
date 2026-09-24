# -*- coding: utf-8 -*-
"""测 _keep_infobox 的选择器为什么失效。"""
from bs4 import BeautifulSoup
from src.wiki_scraper import WikiScraper

s = WikiScraper()
p = s.get_page_html('金心')
soup = BeautifulSoup(p['html'], "lxml")
infoboxes = soup.find_all(class_="infobox")
t = infoboxes[6]

print("_keep_infobox:", __import__('src.data_processor', fromlist=['_keep_infobox'])._keep_infobox(t))
print("select(.achievement-box):", bool(t.select(".achievement-box")))
print("select(.bg-primary):", bool(t.select(".bg-primary")))
print("select([class*='achievement']):", bool(t.select("[class*='achievement']")))
print("select('.achievement-box, .bg-primary'):", bool(t.select(".achievement-box, .bg-primary")))

# 手动检查
found = t.find(class_="achievement-box")
print("find(class_='achievement-box'):", found is not None)
