# -*- coding: utf-8 -*-
"""
条目别名层(Entity Alias): 把玩家俗称/英文名/缩写映射到标准条目标题。

问题背景: 用户用「AZ / Lost / D6 / 金心」提问时, 语义检索会瞎匹配
(「AZ」被匹配到符文页, 阿撒泻勒页根本不被召回)。因为 wiki 条目有
「中文名 / 英文名 / 玩家俗称」三套名字, 但向量索引只知道中文标题。

解法: 离线扫全库, 提取每条目开头「中文名（English Name）」的英文名,
建立 {中文名, 英文名, 英文小写, 常见缩写} -> 标准标题 的映射。
检索时先做别名识别: 命中则「确定性置顶」该条目的文档(精确 title 过滤,
绕过语义检索), 与分类索引置顶同一套哲学——能确定就不靠语义。
"""
import re
from typing import Dict, List, Optional

from .config import settings

# 中文名（English）模式: 阿撒泻勒（Azazel） / 游魂（The Lost）
_EN_NAME_RE = re.compile(r"[（(]\s*([A-Za-z][A-Za-z0-9 .'\-]*?)\s*[)）]")

# 人工补充的常见俗称/缩写 (小写作为键)
_MANUAL_ALIAS = {
    "az": "阿撒泻勒",
    "lost": "游魂",
    "d6": "六面骰",
    "d4": "四骰子",
    "白卡": "白卡",  # 占位: 避免空映射
}

# 太泛的通用词, 也是条目标题(道具/心/房间/卡牌...), 出现在 query 里不该触发定向召回,
# 否则「初始道具」「心之容器」会被误导向「道具」「心」页, 反而挤掉正确答案。
# 注意: 具体的房间名(天使房/恶魔房/献祭房...)是真实机制条目, 用户常问, 必须保留可命中
_STOP_TITLES = {
    "道具", "心", "房间", "卡牌", "胶囊", "饰品", "敌人", "怪物", "头目",
}


class EntityAliasIndex:
    """条目别名索引。构建后常驻内存, 供检索期做「别名→标准标题」定向召回。"""

    def __init__(self):
        self._title_to_en: Dict[str, str] = {}   # 标准标题 -> 英文名
        self._alias_to_title: Dict[str, str] = {}  # 别名(小写) -> 标准标题
        self._all_titles: set = set()              # 全量条目标题(用于中文名子串匹配)

    def build(self, chunks: List[Dict]) -> None:
        """扫全库 chunks, 提取每标题首个「中文名（English）」的英文名。"""
        title_first = {}  # 标题 -> 该标题下第一个含英文名的文本
        for c in chunks:
            t = c.get("title", "")
            self._all_titles.add(t)
            if t in title_first:
                continue
            text = c.get("text", "")
            if t in text[: len(t) + 3] or t == c.get("section", "").split(" - ")[0]:
                title_first[t] = text

        for title, text in title_first.items():
            m = _EN_NAME_RE.search(text[:300])
            if m:
                en = m.group(1).strip().lower()
                if en:
                    self._title_to_en[title] = m.group(1).strip()
                    self._alias_to_title.setdefault(en, title)
                    # 中文名本身也可作为别名(标题即中文名)
                    self._alias_to_title.setdefault(title, title)

        for alias, title in _MANUAL_ALIAS.items():
            self._alias_to_title.setdefault(alias.lower(), title)
            self._alias_to_title.setdefault(title, title)

        print(f"[EntityAlias] 构建完成: {len(self._title_to_en)} 个条目含英文名, "
              f"{len(self._alias_to_title)} 个别名, {len(self._all_titles)} 个标题")

    def resolve_all(self, query: str) -> List[str]:
        """识别 query 提到的所有条目标题(可多个, 如「天使房和恶魔房」)。空列表=无。"""
        q_lower = query.lower()
        # 枚举意图: 跳过(让分类索引置顶处理"以撒有哪些角色", 而不是定向到"以撒"页)
        if any(p in q_lower for p in ("有哪些", "都有哪些", "一共有哪些", "列出", "多少种", "哪几种", "有几种", "一览", "分别有")):
            return []
        hits = set()
        # 1) 中文标题: 用原始 query 做子串匹配(标题里可能有英文大小写如 科技X/D6),
        #    长的优先且排除"被包含于更长标题"的(如 科技 ⊂ 科技X); 通用词不触发
        matched_titles = [
            t for t in self._all_titles
            if t and len(t) >= 2 and t not in _STOP_TITLES and t in query
        ]
        for t in sorted(matched_titles, key=len, reverse=True):
            # 若 t 是另一个更长命中标题的子串(如 科技 ⊂ 科技X), 跳过
            if any(t != u and t in u for u in matched_titles):
                continue
            hits.add(t)
        # 2) 别名(英文名/缩写) 用降序 query 词边界匹配
        for alias, title in self._alias_to_title.items():
            if not alias or title in hits:
                continue
            if alias in q_lower and self._is_word_hit(q_lower, alias):
                hits.add(title)
        # 按 query 中出现位置排序: 「天使房和恶魔房」→ 天使房在前(用户先提到的排前)
        return sorted(hits, key=lambda t: (query.find(t) if t in query else query.lower().find(self._alias_to_title.get(t, "")), -len(t)))

    def resolve(self, query: str) -> Optional[str]:
        """兼容旧接口: 返回最长命中标题; 无则 None。"""
        hits = self.resolve_all(query)
        return hits[0] if hits else None

    @staticmethod
    def _is_word_hit(query_lower: str, alias: str) -> bool:
        """英文别名要求词边界(前后非字母), 避免 the->then、lost 误匹配。"""
        if not alias[0].isalpha() or not alias[-1].isalpha():
            return True  # 纯中文别名直接子串匹配
        # 简单词边界: 前一位/后一位不能是字母
        i = query_lower.find(alias)
        before = query_lower[i - 1] if i > 0 else " "
        after = query_lower[i + len(alias)] if i + len(alias) < len(query_lower) else " "
        return (not before.isalpha()) and (not after.isalpha())
