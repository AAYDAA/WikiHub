# -*- coding: utf-8 -*-
"""Wiki 地址探测：给定一个 wiki 地址，判断它是不是可用的 MediaWiki 站点，
并尽力取出「游戏名」与「站点图标」，供首页「添加 Wiki」使用。

为什么这样设计（避免后来人改错）：
  1. 「是不是合法 wiki」用 api.php 的**事实探测**，不让 LLM 猜。
     事实判断更快、更准、不烧 token；LLM 只做语义活——
     把 sitename「以撒的结合中文维基」剥成「以撒的结合」，并判断是不是游戏主题。
  2. **只走 api.php**，不抓 HTML。实测灰机 wiki 的 HTML/favicon 路径不可达（HTTP 000），
     而 api.php 通；这与项目现有 wiki_scraper 的做法一致，UA 也沿用同一个。
  3. **SSRF 防护是必需的**：本接口让后端去请求用户给的地址，若不设防，
     可被用来探测内网（如 172.19.x.x 办公网）甚至云元数据 169.254.169.254。
     故做了四道闸：https-only + 域名白名单 + DNS 解析后校验 IP 非私有/保留段 + 禁止跟随跳转。
     注意「DNS 解析后校验 IP」不能省——否则域名白名单可被 DNS 解析绕过。
"""
import ipaddress
import json
import os
import re
import socket
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from .config import settings

# 沿用 wiki_scraper 的 UA：实测默认 UA（curl/python-requests）会被 Cloudflare 拦，
# 换成这个 UA 就能过；改这里之前请先确认目标站还能访问。
USER_AGENT = "WikiHub/1.0 (personal project)"

# 域名白名单：默认只放灰机 wiki。要加别的站点（如 fandom.com），
# 在 backend/.env 里加一行 ALLOWED_WIKI_DOMAINS=huijiwiki.com,fandom.com 即可，无需改代码。
_DEFAULT_ALLOWED = "huijiwiki.com"

# 单个响应体最多读取的字节数，防止被用来拉大文件 / DoS
MAX_BODY_BYTES = 256 * 1024
# 图标探测的上限更小（图标本来就小）
MAX_ICON_BYTES = 512 * 1024
TIMEOUT = 15

# MediaWiki 站点没设自定义 logo 时会返回这个占位图，不能当图标用
_PLACEHOLDER_ICON_HINTS = ("change-your-logo",)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"})

_LLM = None


class ProbeError(Exception):
    """探测失败。message 直接面向用户展示，所以写成人话。"""


def allowed_domains() -> List[str]:
    raw = os.getenv("ALLOWED_WIKI_DOMAINS", _DEFAULT_ALLOWED) or _DEFAULT_ALLOWED
    return [d.strip().lower().lstrip(".") for d in raw.split(",") if d.strip()]


def _host_allowed(host: str) -> bool:
    """host 必须等于白名单域，或其子域。
    注意不能用裸 endswith：'evilhuijiwiki.com'.endswith('huijiwiki.com') 会误判为真。"""
    host = (host or "").lower()
    for d in allowed_domains():
        if host == d or host.endswith("." + d):
            return True
    return False


def _assert_public_host(host: str) -> None:
    """DNS 解析后逐个校验 IP：私有段/回环/链路本地/保留段一律拒绝。
    这一步是白名单的补充，专门防 DNS 解析绕过和 DNS rebinding。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        raise ProbeError(f"域名解析失败：{host}（{e}）")

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ProbeError(f"解析出非法 IP：{ip_str}")
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ProbeError("该地址指向内网或保留地址，出于安全考虑已拒绝访问")


def parse_wiki_url(raw: str) -> Dict[str, str]:
    """把用户输入的各种写法归一成 {origin, host, api_url, base_url}。
    接受：'isaac.huijiwiki.com'、'https://isaac.huijiwiki.com/wiki/首页'、带 http 的地址等。
    只允许 https（http 自动升级），因为 SSRF 里 file://、gopher:// 之类的花招都靠协议绕过。"""
    raw = (raw or "").strip()
    if not raw:
        raise ProbeError("请输入 Wiki 地址")
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()

    if scheme not in ("http", "https"):
        raise ProbeError(f"只支持 http/https 地址，收到的是 {scheme}://")
    if not host:
        raise ProbeError("地址里没解析出域名，请检查格式")

    if not _host_allowed(host):
        raise ProbeError(
            "该域名不在允许列表内。当前允许：" + "、".join(allowed_domains())
            + "（可在 backend/.env 的 ALLOWED_WIKI_DOMAINS 中追加）"
        )

    _assert_public_host(host)

    # http 一律升级为 https：目标站都支持，且能顺带挡掉一批协议层花招
    origin = urlunparse(("https", host, "", "", "", ""))
    return {
        "host": host,
        "origin": origin,
        "api_url": origin + "/api.php",
        # base_url 用 /wiki/ 前缀，和 games.json 里既有格式保持一致
        "base_url": origin + "/wiki/",
    }


def _bounded_get(url: str, max_bytes: int = MAX_BODY_BYTES) -> Tuple[int, str, bytes]:
    """带上限的 GET。两个要点：
    1) allow_redirects=False —— 否则 https://合法域 302 到内网就能把白名单架空；
    2) stream + 字节上限 —— 防止被拿来做 DoS 或拉大文件。"""
    resp = _SESSION.get(url, timeout=TIMEOUT, allow_redirects=False, stream=True)
    try:
        ctype = resp.headers.get("Content-Type", "")
        buf, total = [], 0
        for chunk in resp.iter_content(8192):
            if not chunk:
                break
            buf.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                break
        return resp.status_code, ctype, b"".join(buf)
    finally:
        resp.close()


def fetch_siteinfo(api_url: str) -> Dict[str, str]:
    """请求 MediaWiki 的 siteinfo。能正常返回 general 就说明这是一个可用的 MediaWiki 站。"""
    url = api_url + "?action=query&meta=siteinfo&siprop=general&format=json"
    try:
        status, ctype, body = _bounded_get(url)
    except ProbeError:
        raise
    except Exception as e:
        # 连不上（如被防火墙/Cloudflare 直接掐断）会走到这里，URL 里可能有用户输入，回显要克制
        raise ProbeError(f"无法访问该站点的 api.php：{type(e).__name__}")

    if status in (301, 302, 303, 307, 308):
        raise ProbeError("该地址发生了跳转，出于安全考虑不跟随，请直接填写最终域名")

    if status != 200:
        raise ProbeError(f"该站点的 api.php 返回 HTTP {status}，可能不是 MediaWiki 站点")

    head = body[:400].lstrip()
    if head[:1] not in (b"{", b"["):
        # 典型情况是被 Cloudflare 的人机校验挡下，返回的是 HTML 挑战页
        if b"just a moment" in body[:2000].lower() or "请稍候".encode("utf-8") in body[:2000]:
            raise ProbeError("目标站点被 Cloudflare 人机校验拦截，稍后重试或改用其他 Wiki")
        raise ProbeError("该地址返回的不是 wiki 接口数据，可能不是 MediaWiki 站点")

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        raise ProbeError("该地址返回的数据无法解析为 JSON")

    general = (data.get("query") or {}).get("general")
    if not isinstance(general, dict) or not general.get("sitename"):
        raise ProbeError("该地址能访问，但没返回站点信息，可能不是 MediaWiki 站点")

    return {
        "sitename": general.get("sitename") or "",
        "logo": general.get("logo") or "",
        "generator": general.get("generator") or "",
        "lang": general.get("lang") or "",
        "mainpage": general.get("mainpage") or "",
    }


def _icon_ok(url: str) -> Optional[str]:
    """验证候选图标真的能取到（200 + image 类型 + 非空）。取不到就返回 None。
    注意：图标取不到不影响主流程，最终会退回 emoji，所以这里一律不抛异常。"""
    if not url:
        return None
    if any(h in url for h in _PLACEHOLDER_ICON_HINTS):
        return None
    try:
        status, ctype, body = _bounded_get(url, max_bytes=MAX_ICON_BYTES)
    except Exception:
        return None
    if status != 200 or not body:
        return None
    if "image" not in (ctype or "").lower() and not url.lower().endswith((".ico", ".png", ".svg")):
        return None
    if url.startswith("http://"):
        # 混用 http 图标会让浏览器报混合内容警告，统一跳过
        return None
    return url


def find_icon(info: Dict[str, str], siteinfo: Dict[str, str]) -> Tuple[Optional[str], str]:
    """按可靠性依次尝试图标，返回 (url 或 None, 来源标签)。
    顺序说明：MediaWiki 的 logo 往往是横幅或占位图，favicon 更像素级图标，
    所以先试 favicon.ico，再退回 logo。全部失败则由调用方用 emoji 兜底。"""
    candidates = [
        (info["origin"] + "/favicon.ico", "favicon"),
        (siteinfo.get("logo") or "", "logo"),
    ]
    for url, source in candidates:
        got = _icon_ok(url)
        if got:
            return got, source
    return None, "none"


_LLM_SYSTEM = (
    "你是游戏 Wiki 信息抽取助手。你会收到一个 Wiki 站点的官方名称(sitename)和域名。请完成两件事：\n"
    "1) 判断该 Wiki 是否为「游戏」主题（游戏攻略站、游戏资料站等）。\n"
    "   若是公司/政府/学校/机构内部知识库等非游戏主题，则 is_game 为 false。\n"
    "2) 若是游戏，从 sitename 中提取干净的游戏名，去掉「中文维基」「Wiki」「百科」「站点」「攻略站」等后缀。\n"
    "只输出 JSON，不要解释、不要 markdown 代码块。格式：\n"
    '{"is_game": true, "game_name": "以撒的结合", "emoji": "👶", "reason": "一句话理由"}\n'
    "其中 emoji 选一个最能代表该游戏的字形；不确定时给 🎮。"
)


def _get_llm():
    global _LLM
    if _LLM is None:
        from langchain_openai import ChatOpenAI  # 与 qa_engine 用同一套配置，懒加载避免拖慢启动
        _LLM = ChatOpenAI(
            model=settings.llm_model,
            temperature=0,  # 抽取任务要稳定，固定 0，不走全局 LLM_TEMPERATURE
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    return _LLM


def _loads_loose(text: str) -> Optional[Dict]:
    """LLM 偶尔会带 ```json 包裹或前后废话，这里做宽松解析。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def guess_game_info(sitename: str, host: str) -> Dict:
    """让 LLM 剥游戏名 + 判断是否游戏主题。
    LLM 挂了不能让整个功能不可用，所以失败时退回一个保守的默认值，并把用于展示的
    名称退回 sitename 本身（用户可以在预览卡片上自己改）。"""
    fallback = {"is_game": True, "game_name": sitename, "emoji": "🎮", "reason": "AI 调用失败，已退回默认值"}
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        user = f"sitename: {sitename}\ndomain: {host}"
        ai = _get_llm().invoke([SystemMessage(content=_LLM_SYSTEM), HumanMessage(content=user)])
        data = _loads_loose(getattr(ai, "content", "") or "")
        if not isinstance(data, dict):
            return fallback
        name = (data.get("game_name") or "").strip()
        emoji = (data.get("emoji") or "").strip()
        return {
            "is_game": bool(data.get("is_game", True)),
            "game_name": name or sitename,
            "emoji": emoji or "🎮",
            "reason": (data.get("reason") or "").strip(),
        }
    except Exception as e:
        fallback["reason"] = f"AI 调用失败（{type(e).__name__}），已退回默认值"
        return fallback


def suggest_id(host: str, name: str) -> str:
    """id 优先取子域名（huijiwiki 上子域就是游戏代号，如 isaac.huijiwiki.com → isaac），
    它天然唯一且和域名对应；取不到再用名字兜底。"""
    parts = host.split(".")
    if len(parts) >= 3 and parts[0] not in ("www",):
        cand = re.sub(r"[^a-z0-9-]", "", parts[0].lower())
        if cand:
            return cand
    cand = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return cand or "wiki"


def probe_wiki(raw_url: str) -> Dict:
    """主入口：探测并返回预览信息（**不落库**，落库由 register 接口负责）。"""
    info = parse_wiki_url(raw_url)
    siteinfo = fetch_siteinfo(info["api_url"])
    icon_url, icon_source = find_icon(info, siteinfo)
    guess = guess_game_info(siteinfo["sitename"], info["host"])

    return {
        "wiki_api_url": info["api_url"],
        "wiki_base_url": info["base_url"],
        "host": info["host"],
        "sitename": siteinfo["sitename"],
        "generator": siteinfo["generator"],
        "suggested_id": suggest_id(info["host"], guess["game_name"]),
        "suggested_name": guess["game_name"],
        # 优先用真实图标；抓不到就用 AI 给的 emoji 兜底
        "suggested_icon": icon_url or guess["emoji"],
        "icon_source": icon_source,
        "icon_url": icon_url,
        "is_game": guess["is_game"],
        "reason": guess["reason"],
    }


# ============ 自动发现分类（自动建库用）============
# 站点自我管理类的分类，抓进来只会是噪声（如「需要翻译的页面」）。
# 注意只用「包含」判断，不做正则过度匹配，否则会误伤真实的内容分类。
_NON_CONTENT_HINTS = (
    # 中文管理类
    "维护", "模板", "需要", "待补", "未完成", "存废", "删除", "讨论", "帮助",
    "文件", "图片", "分类", "侵权", "翻译", "格式", "重定向", "消歧义", "页面",
    "维基", "施工",
    # 英文管理类（灰机等站点上管理分类大量是英文名，实测 92 个分类里混了不少）
    "template", "infobox", "navigational", "notice", "editing", "formatting",
    "utility", "imported", "module", "category",
)
MAX_CATEGORIES = 500


def _is_content_category(name: str) -> bool:
    n = (name or "").strip()
    if not n or len(n) > 20:  # 超长多半是复合的管理分类
        return False
    low = n.lower()
    return not any(h in low for h in _NON_CONTENT_HINTS)


def discover_categories(api_url: str, max_categories: int = MAX_CATEGORIES) -> List[str]:
    """列出该 wiki 全部「内容分类」，供自动建库使用。

    为什么必须自动发现（血的教训）：
      早先抓取清单里硬编码了「角色 / 怪物 / Boss」，但该 wiki 真实分类叫「人物」，
      另外两个压根不存在 —— 而 MediaWiki 对**不存在的分类返回空列表、不报错**，
      于是人物数据整整漏掉一类，直到用户提问答不上来才被发现。
      所以这里改成：先列站点真实存在的分类，再逐个抓，抓到 0 页就跳过并记录。

    只走 api.php，复用本模块既有的 UA 与超时设置；SSRF 校验由调用方先行完成。
    """
    cats: List[str] = []
    cont = None
    while len(cats) < max_categories:
        url = f"{api_url}?action=query&list=allcategories&aclimit=500&format=json"
        if cont:
            url += f"&accontinue={cont}"
        try:
            status, _ctype, body = _bounded_get(url, max_bytes=2 * 1024 * 1024)
        except Exception as e:
            raise ProbeError(f"获取分类列表失败：{type(e).__name__}")
        if status != 200:
            raise ProbeError(f"获取分类列表失败：HTTP {status}")
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            raise ProbeError("分类列表返回的不是合法 JSON")

        for item in (data.get("query") or {}).get("allcategories") or []:
            # 不同 MediaWiki 版本字段名不同：老版 {"*": name}，新版 {"category": name}
            name = item.get("*") or item.get("category") or ""
            if _is_content_category(name):
                cats.append(name)

        nxt = (data.get("continue") or {}).get("accontinue")
        if nxt:
            cont = nxt
        else:
            break

    # 去重并保序（分页边界可能重复）
    seen, out = set(), []
    for c in cats:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:max_categories]
