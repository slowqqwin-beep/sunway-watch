import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / ".state" / "sunway_state.json"
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

SOURCES = [
    {
        "name": "新浪年度/定期报告",
        "url": "http://money.finance.sina.com.cn/corp/view/vCB_Bulletin.php?stockid=300136&type=list&page_type=ndbg",
    },
    {
        "name": "新浪中报/定期报告摘要",
        "url": "http://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllBulletin/stockid/300136.phtml?ftype=zqbgzy",
    },
    {
        "name": "东方财富个股资料",
        "url": "https://data.eastmoney.com/stockdata/300136.html",
    },
]

KEYWORDS = [
    "年度报告", "半年度报告", "季度报告", "一季度报告", "三季度报告",
    "年度报告摘要", "半年度报告摘要",
    "投资者关系活动记录表", "机构调研", "调研纪要",
    "定增", "募投", "募资", "项目进度", "建设期", "达产",
    "订单", "产能", "满产",
    "商业卫星", "卫星通信", "高速连接器", "高频高速", "散热",
    "毛利率", "经营现金流", "应收账款",
]

BLOCK_TITLES = {
    "信维通信",
    "年度报告",
    "半年度报告",
    "一季度报告",
    "三季度报告",
    "信维通信数据全景图",
    "信维通信机构调研",
}

BLOCK_WORDS = [
    "新浪财经", "新浪股票", "东方财富", "客户端", "手机站", "桌面快捷方式",
    "加入自选股", "全球财经快讯", "数据中心", "Choice数据", "手机买基金",
    "新股申购", "新股日历", "资金流向", "AH股比价", "主力排名", "板块资金",
    "个股研报", "行业研报", "盈利预测", "千股千评", "龙虎榜单", "限售解禁",
    "大宗交易", "期指持仓", "融资融券", "股权质押", "条件选股",
    "信维通信吧", "精准买卖点", "大单成交", "问董秘", "机构散户",
    "特色", "公告大全", "派现与募资对比", "新股发行", "配股", "可转债", "分红送配",
    "数据全景图", "公司核心数据", "公司数据全览", "大事提醒", "信息地雷",
]

DETAIL_URL_HINTS = [
    "vCB_AllBulletinDetail.php",
    "finalpage",
    "static.cninfo.com.cn",
]

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": {}}

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def fetch(url):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()

def has_recent_year_in_title(title):
    years = re.findall(r"20\d{2}", title)
    if not years:
        return True
    latest_year = max(int(y) for y in years)
    return latest_year >= 2024

def looks_like_detail_url(url):
    return any(hint in url for hint in DETAIL_URL_HINTS)

def is_relevant(title, context, url=""):
    title = clean(title)
    context = clean(context)
    text = f"{title} {context} {url}"

    if not title or len(title) < 6:
        return False

    if title in BLOCK_TITLES:
        return False

    if any(block in title for block in BLOCK_WORDS):
        return False

    if any(block in context for block in BLOCK_WORDS[:15]):
        return False

    if not any(keyword in text for keyword in KEYWORDS):
        return False

    if not (
        "信维通信" in title
        or "深圳市信维通信股份有限公司" in title
        or looks_like_detail_url(url)
    ):
        return False

    if not has_recent_year_in_title(title):
        return False

    if "报告" in title and "信维通信" not in title and "深圳市信维通信股份有限公司" not in title:
        return False

    return True

def extract_links(html, base_url, source_name):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen_local = set()

    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        href = clean(a.get("href"))

        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        full_url = urljoin(base_url, href)
        parent_text = clean(a.parent.get_text(" ", strip=True)) if a.parent else ""

        if len(parent_text) > 500:
            parent_text = parent_text[:500]

        if not is_relevant(title, parent_text, full_url):
            continue

        item_id = hashlib.md5(f"{title}|{full_url}".encode("utf-8")).hexdigest()
        if item_id in seen_local:
            continue
        seen_local.add(item_id)

        results.append({
            "id": item_id,
            "title": title,
            "url": full_url,
            "source": source_name,
                        "context": title,
        })

    return results

def send_webhook(text):
    if not WEBHOOK_URL:
        print(text)
        return

    payloads = [
        {"text": text},
        {"msg_type": "text", "content": {"text": text}},
        {"markdown": text},
    ]

    for payload in payloads:
        try:
            r = requests.post(WEBHOOK_URL, json=payload, headers=UA, timeout=15)
            if r.ok:
                return
        except Exception:
            pass

    print(text)

def sort_items(items):
    def sort_key(item):
        years = re.findall(r"20\d{2}", item["title"])
        year = max(int(y) for y in years) if years else 0
        return (year, item["title"])
    return sorted(items, key=sort_key, reverse=True)

def main():
    state = load_state()
    print(f"[DEBUG] STATE_FILE = {STATE_FILE}")
    all_items = []

    for source in SOURCES:
        try:
            html = fetch(source["url"])
            all_items.extend(extract_links(html, source["url"], source["name"]))
        except Exception as e:
            print(f"[WARN] {source['name']} 抓取失败: {e}")

    dedup = {}
    for item in all_items:
        dedup[item["id"]] = item
    all_items = sort_items(list(dedup.values()))

    new_items = [item for item in all_items if item["id"] not in state["seen"]]

    for item in all_items:
        state["seen"][item["id"]] = {
            "title": item["title"],
            "url": item["url"],
            "source": item["source"],
            "seen_at": datetime.now().isoformat(timespec="seconds"),
        }

    save_state(state)

    if not new_items:
        print("今日没有发现新的高相关公告/财报/调研线索。")
        return

    lines = [f"信维通信监控更新 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    for i, item in enumerate(new_items, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   来源: {item['source']}")
        lines.append(f"   链接: {item['url']}")
        if item["context"]:
            lines.append(f"   线索: {item['context']}")

    send_webhook("\n".join(lines))

if __name__ == "__main__":
    main()