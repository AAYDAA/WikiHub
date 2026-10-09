import json
import time
from pathlib import Path
from typing import List, Dict, Optional

import requests

from .config import settings
from .games import data_file


# 分类名必须与 wiki 上真实存在的分类一致, 否则会「静默抓到 0 页」
# (历史坑: 曾写「角色」「怪物」「Boss」, 但这三个在灰机 wiki 上均无对应分类,
#  而人物实际挂在「人物」分类下 → 人物数据 0 召回, 人物类问答全军覆没。
#  现已由 report_coverage() 在抓取后显式校验, 抓到 0 页会告警。)
DEFAULT_CATEGORIES = [
    "道具",
    "人物",  # 原为「角色」, 但 wiki 上无此分类
    "卡牌",
    "胶囊",
    "饰品",
    "房间",
]


def report_coverage(pages: List[Dict], categories: List[str]) -> List[str]:
    """抓取后按分类统计页数, 抓到 0 页的分类会告警。

    存在理由: 分类名写错时(如把「人物」写成「角色」), MediaWiki 返回的是
    空列表而不是报错, 数据会「静默缺失」, 下游完全无感知 —— 这正是本项目
    曾经人物数据全丢却无人发现的原因。这里做显式校验, 让缺失当场暴露。
    """
    from collections import Counter

    counter = Counter(p.get("category", "?") for p in pages)
    print("\n[Wiki] === 覆盖率校验 ===")
    empty = []
    for cat in categories:
        n = counter.get(cat, 0)
        if n == 0:
            print(f"  [!] {cat}: 0 页   <-- 分类名可能不存在于 wiki, 请核对")
            empty.append(cat)
        else:
            print(f"  [OK] {cat}: {n} 页")
    for c, n in counter.items():
        if c not in categories:
            print(f"  [--] {c} (不在抓取清单): {n} 页")

    if empty:
        print(f"[Wiki][警告] 以下分类抓到 0 页, 数据可能静默缺失: {empty}")
    else:
        print("[Wiki][通过] 所有分类均有数据")
    return empty


class WikiScraper:
    def __init__(self, api_url: Optional[str] = None, base_url: Optional[str] = None):
        self.api_url = api_url or settings.wiki_api_url
        self.base_url = base_url or settings.wiki_base_url
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "WikiHub/1.0 (personal project)",
            "Accept-Encoding": "gzip, deflate",
        })

    def _get(self, params: Dict) -> Dict:
        resp = self.session.get(self.api_url, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def get_category_pages(self, category: str, limit: int = 500) -> List[Dict]:
        pages = []
        cmcontinue = None

        while True:
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": f"分类:{category}",
                "cmlimit": "50",
                "cmtype": "page",
                "format": "json",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue

            data = self._get(params)
            members = data.get("query", {}).get("categorymembers", [])
            pages.extend(members)

            if "continue" not in data or len(pages) >= limit:
                break

            cmcontinue = data["continue"]["cmcontinue"]
            time.sleep(0.5)

        return pages

    def get_page_html(self, page_title: str) -> Optional[Dict]:
        params = {
            "action": "parse",
            "page": page_title,
            "format": "json",
            "prop": "text|sections|displaytitle",
            "redirects": "1",  # 跟随重定向: 否则只拿到"重定向到 XX"的骨架页(如 金心→实体/5/10#金心)
        }
        try:
            data = self._get(params)
        except Exception:
            return None

        parse_data = data.get("parse", {})
        if not parse_data:
            return None

        title = parse_data.get("displaytitle", page_title)
        html = parse_data.get("text", {}).get("*", "")
        encoded_title = page_title.replace(" ", "_")
        url = self.base_url + encoded_title

        return {
            "title": title,
            "raw_title": page_title,
            "html": html,
            "url": url,
        }

    def scrape_category(
        self,
        category: str,
        limit: int = 200,
        delay: float = 0.3,
    ) -> List[Dict]:
        print(f"[Wiki] 正在获取分类 '{category}' 的页面列表...")
        page_list = self.get_category_pages(category, limit=limit)
        print(f"[Wiki] 分类 '{category}' 找到 {len(page_list)} 个页面")

        results = []
        for i, page in enumerate(page_list):
            title = page["title"]
            print(f"  [{i+1}/{len(page_list)}] 抓取: {title}")

            page_data = self.get_page_html(title)
            if page_data:
                page_data["category"] = category
                results.append(page_data)

            time.sleep(delay)

        return results

    def scrape_all(
        self,
        categories: Optional[List[str]] = None,
        per_category_limit: int = 200,
        output_file: Optional[str] = None,
    ) -> List[Dict]:
        categories = categories or DEFAULT_CATEGORIES
        all_pages = {}

        for cat in categories:
            try:
                pages = self.scrape_category(cat, limit=per_category_limit)
                for p in pages:
                    if p["raw_title"] not in all_pages:
                        all_pages[p["raw_title"]] = p
            except Exception as e:
                print(f"[Wiki] 分类 '{cat}' 抓取失败: {e}")

        result_list = list(all_pages.values())
        print(f"\n[Wiki] 抓取完成，共 {len(result_list)} 个唯一页面")

        # 覆盖率校验: 分类名写错会静默抓 0 页, 这里显式暴露(见 report_coverage)
        report_coverage(result_list, categories)

        if output_file:
            out_path = Path(output_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result_list, f, ensure_ascii=False, indent=2)
            print(f"[Wiki] 数据已保存到 {out_path}")

        return result_list


if __name__ == "__main__":
    scraper = WikiScraper()
    # 经 data_file 拼路径（→ data/<game>/raw/），不要在别处硬编码 data/ 下的相对路径
    output = data_file(settings.wiki_name, "wiki_all.json")
    scraper.scrape_all(
        categories=DEFAULT_CATEGORIES,
        per_category_limit=500,
        output_file=str(output),
    )
