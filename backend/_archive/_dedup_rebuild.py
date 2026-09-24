# -*- coding: utf-8 -*-
"""去重 chunks.json(按 title+section+text 完全重复) 后重建索引。"""
import json
import sys

from src.config import settings
from src.build_index import build_index_from_chunks

p = settings.data_dir / f"{settings.wiki_name}_chunks.json"
chunks = json.load(open(p, encoding="utf-8"))
before = len(chunks)

# 去重: 保持首次出现顺序
seen = set()
dedup = []
for c in chunks:
    key = (c.get("title"), c.get("section"), c.get("text"))
    if key in seen:
        continue
    seen.add(key)
    dedup.append(c)

print(f"去重: {before} -> {len(dedup)} (移除 {before - len(dedup)} 个重复块)")

with open(p, "w", encoding="utf-8") as f:
    json.dump(dedup, f, ensure_ascii=False, indent=2)
print("chunks.json 已更新")

# 重建索引
build_index_from_chunks(dedup)
print("索引重建完成!")
