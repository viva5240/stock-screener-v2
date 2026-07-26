"""
话题热度评分模块 (Heat Score Engine) — V2 简化版
数据源核心：同花顺热点排名（零鉴权 73ms）
扩展源（备选/未来接入）：东财股吧、雪球、东财研报、慧博
评分算法：同花顺TOP1=100分，按排名指数衰减
"""

import time, threading
from datetime import datetime
import urllib.request, json

CACHE_TTL = 1800  # 30分钟

WEIGHTS = {
    "ths_rank": 0.60,       # 同花顺热点排名（核心，稳定快速）
    "east_guba": 0.15,      # 东财股吧（备选）
    "xueqiu": 0.10,         # 雪球（备选）
    "east_report": 0.10,    # 东财研报（备选）
    "hibor": 0.05,          # 慧博（备选）
}

_cache = {"data": {}, "updated_at": 0}
_cache_lock = threading.Lock()

# ===== 1. 同花顺热点排名（稳定可靠） =====

def fetch_ths_hot() -> list[dict]:
    """同花顺当日强势股，零鉴权 ~73ms"""
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{today}/orderby/date/orderway/desc/charset/GBK/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    if data.get("errocode", 0) != 0:
        return []
    rows = data.get("data") or []
    return [{
        "code": r.get("code", ""),
        "name": r.get("name", ""),
        "rank": i + 1,
        "zhangfu": float(r.get("zhangfu", 0) or 0),
        "reason": r.get("reason", ""),
    } for i, r in enumerate(rows)]

# ===== 评分引擎 =====

def compute_heat_scores() -> dict[str, dict]:
    """
    综合评分 — 基于同花顺热点排名
    排名1=100分，排名n=100-2*(n-1)，最低0分
    """
    ths_data = fetch_ths_hot()
    if not ths_data:
        print("[HeatScore] 同花顺热点无数据")
        return {}

    # 同花顺排名分（核心）
    scores = {}
    for s in ths_data:
        rank = s["rank"]
        # 排名1=100, 排名2=98, 排名3=96... 最低0
        rank_score = max(0, 100 - (rank - 1) * 2.5)

        heat = rank_score * (WEIGHTS["ths_rank"] / 0.60)  # 归一化到纯同花顺基准

        scores[s["code"]] = {
            "code": s["code"],
            "name": s["name"],
            "heat_score": round(heat, 1),
            "ths_rank": rank,
            "ths_reason": s.get("reason", ""),
            "ths_zhangfu": s.get("zhangfu"),
            # 以下为扩展字段（后续接入其他数据源后填充）
            "guba_count": 0,
            "xueqiu_followers": 0,
            "report_count": 0,
            "hibor_count": 0,
            "detail": {
                "ths_score": round(rank_score, 1),
                "guba_score": 0,
                "xueqiu_score": 0,
                "report_score": 0,
                "hibor_score": 0,
            }
        }

    print(f"[HeatScore] {len(scores)} 只股票评分完成, TOP1={list(scores.values())[0]['name']}={list(scores.values())[0]['heat_score']}分")
    return scores

# ===== 缓存管理器 =====

def refresh_cache():
    """刷新缓存"""
    global _cache
    try:
        scores = compute_heat_scores()
        with _cache_lock:
            _cache["data"] = scores
            _cache["updated_at"] = int(time.time())
    except Exception as e:
        print(f"[HeatScore] 刷新失败: {e}")

def get_heat_score(code: str) -> dict:
    with _cache_lock:
        return _cache["data"].get(code, {})

def get_all_heat_scores() -> dict:
    with _cache_lock:
        return dict(_cache["data"])

def get_cache_status() -> dict:
    with _cache_lock:
        return {
            "stock_count": len(_cache["data"]),
            "updated_at": _cache["updated_at"],
            "age_seconds": int(time.time()) - _cache["updated_at"] if _cache["updated_at"] > 0 else -1,
        }

def start_cache_refresher():
    def _run():
        while True:
            refresh_cache()
            time.sleep(CACHE_TTL)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"[HeatScore] 后台刷新已启动, 间隔={CACHE_TTL}s")

if __name__ == "__main__":
    s = compute_heat_scores()
    for code in list(s.keys())[:10]:
        v = s[code]
        print(f"{v['name']}({code}): {v['heat_score']}分 #{v['ths_rank']} {v['ths_reason'][:30]}")
