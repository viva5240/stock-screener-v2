"""
股票筛选器 V2 - FastAPI 后端（双轨数据模式 + 多源热度评分）
数据源：东财API(全量) + 腾讯财经(实时行情) + 同花顺热点 + 东财股吧 + 雪球
"""
import time, random, json, os, urllib.request, threading
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import requests as req

# 导入热度评分模块
import heat_score

app = FastAPI(title="股票筛选器 V2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
EM_SESSION = req.Session()
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.5
_em_last_call = [0.0]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SEED_FILE = os.path.join(DATA_DIR, "industry_stocks.json")

# ========== 东财 API (节流 + 重试) ==========

def em_get(url, params=None, timeout=5, retries=1, **kwargs):
    for attempt in range(retries + 1):
        wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
        if wait > 0: time.sleep(wait + random.uniform(0.3, 0.8))
        try:
            r = EM_SESSION.get(url, params=params, timeout=timeout, **kwargs)
            _em_last_call[0] = time.time()
            return r
        except Exception as e:
            _em_last_call[0] = time.time()
            if attempt < retries:
                time.sleep(2 + random.uniform(0, 2))
                continue
            raise e

# ========== EBIT 缓存 ==========

EBIT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "ebit_cache.json")
_ebit_cache = None


def load_ebit_cache() -> dict:
    global _ebit_cache
    if _ebit_cache is not None:
        return _ebit_cache
    if os.path.exists(EBIT_FILE):
        try:
            with open(EBIT_FILE, "r", encoding="utf-8") as f:
                _ebit_cache = json.load(f)
        except Exception:
            _ebit_cache = {}
    else:
        _ebit_cache = {}
    return _ebit_cache


def format_ebit(val: float) -> float | None:
    """将EBIT从元转为亿元"""
    if val is None or val == 0:
        return None
    return round(val / 1e8, 1)


# ========== 筹码分布缓存 ==========

CHIP_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "chip_cache.json")
_chip_cache = None


def load_chip_cache() -> dict:
    global _chip_cache
    if _chip_cache is not None:
        return _chip_cache
    if os.path.exists(CHIP_FILE):
        try:
            with open(CHIP_FILE, "r", encoding="utf-8") as f:
                _chip_cache = json.load(f)
        except Exception:
            _chip_cache = {}
    else:
        _chip_cache = {}
    return _chip_cache


# ========== 行业列表 ==========

INDUSTRY_NAME_TO_BK = {}  # 构建行业名→BK代码映射

INDUSTRY_MAP = {
    "半导体": "BK0477", "软件开发": "BK0478", "IT服务": "BK0479",
    "消费电子": "BK0480", "元件": "BK0481", "光学光电子": "BK0482",
    "通信设备": "BK0483", "计算机设备": "BK0484", "通信服务": "BK0485",
    "电网设备": "BK0486", "电力": "BK0487", "光伏设备": "BK0488",
    "风电设备": "BK0489", "电池": "BK0490", "能源金属": "BK0491",
    "汽车整车": "BK0492", "汽车零部件": "BK0493", "自动化设备": "BK0494",
    "通用设备": "BK0495", "专用设备": "BK0496", "轨交设备": "BK0497",
    "工程机械": "BK0498", "国防军工": "BK0499", "航天装备": "BK0500",
    "化学制药": "BK0501", "中药": "BK0502", "生物制品": "BK0503",
    "医疗器械": "BK0504", "医疗服务": "BK0505", "医药商业": "BK0506",
    "白酒": "BK0507", "食品加工": "BK0508", "饮料制造": "BK0509",
    "调味发酵品": "BK0510", "休闲食品": "BK0511", "养殖业": "BK0512",
    "种植业": "BK0513", "银行": "BK0514", "证券": "BK0515",
    "保险": "BK0516", "多元金融": "BK0517", "房地产开发": "BK0518",
    "建筑材料": "BK0519", "建筑装饰": "BK0520", "钢铁": "BK0521",
    "工业金属": "BK0522", "小金属": "BK0523", "贵金属": "BK0524",
    "煤炭开采": "BK0525", "油气开采": "BK0526", "石油加工": "BK0527",
    "化学制品": "BK0528", "化学纤维": "BK0529", "塑料": "BK0530",
    "橡胶": "BK0531", "农化制品": "BK0532", "造纸": "BK0533",
    "包装印刷": "BK0534", "家居用品": "BK0535", "家用电器": "BK0536",
    "纺织制造": "BK0537", "服装家纺": "BK0538", "商贸零售": "BK0539",
    "旅游零售": "BK0540", "酒店餐饮": "BK0541", "教育": "BK0542",
    "游戏": "BK0543", "影视院线": "BK0544", "广告营销": "BK0545",
    "出版": "BK0546", "交通物流": "BK0547", "港口航运": "BK0548",
    "铁路公路": "BK0549", "航空机场": "BK0550", "环保": "BK0551",
    "公用事业": "BK0552", "综合": "BK0553",
}
# 行业列表API
INDUSTRIES = [{"code": v, "name": k} for k, v in sorted(INDUSTRY_MAP.items(), key=lambda x: x[0])]
INDUSTRY_BK_TO_NAME = {v: k for k, v in INDUSTRY_MAP.items()}

# 大行业分组
INDUSTRY_GROUPS = {
    "科技电子": ["半导体", "软件开发", "IT服务", "消费电子", "元件", "光学光电子", "计算机设备"],
    "通信": ["通信设备", "通信服务"],
    "新能源": ["光伏设备", "风电设备", "电池", "能源金属"],
    "电力": ["电网设备", "电力"],
    "汽车": ["汽车整车", "汽车零部件"],
    "机械设备": ["自动化设备", "通用设备", "专用设备", "轨交设备", "工程机械"],
    "国防军工": ["国防军工", "航天装备"],
    "医药": ["化学制药", "中药", "生物制品", "医疗器械", "医疗服务", "医药商业"],
    "食品饮料": ["白酒", "食品加工", "饮料制造", "调味发酵品", "休闲食品"],
    "农业": ["养殖业", "种植业"],
    "金融": ["银行", "证券", "保险", "多元金融"],
    "地产建筑": ["房地产开发", "建筑材料", "建筑装饰"],
    "周期资源": ["钢铁", "工业金属", "小金属", "贵金属", "煤炭开采", "油气开采", "石油加工"],
    "化工": ["化学制品", "化学纤维", "塑料", "橡胶", "农化制品"],
    "轻工制造": ["造纸", "包装印刷", "家居用品", "纺织制造", "服装家纺"],
    "消费服务": ["商贸零售", "旅游零售", "酒店餐饮", "教育", "游戏", "影视院线"],
    "传媒": ["广告营销", "出版"],
    "交运物流": ["交通物流", "港口航运", "铁路公路", "航空机场"],
    "公用环保": ["环保", "公用事业"],
    "家用电器": ["家用电器"],
    "综合": ["综合"],
}


@app.get("/api/industries")
def get_industries():
    return {"industries": INDUSTRIES, "total": len(INDUSTRIES)}


@app.get("/api/industry_groups")
def get_industry_groups():
    """获取大行业分组列表"""
    groups = []
    for group_name, subs in INDUSTRY_GROUPS.items():
        groups.append({
            "name": group_name,
            "subs": [{"name": s, "code": INDUSTRY_MAP.get(s, "")} for s in subs]
        })
    return {"groups": groups, "total": len(groups)}


# ========== 全A股列表获取（mootdx通达信TCP） ==========

ALL_A_STOCKS_CACHE = []
ALL_A_STOCKS_TS = 0


def get_all_a_stocks(force_refresh=False) -> list:
    """从通达信获取全部A股列表（~5660只）"""
    global ALL_A_STOCKS_CACHE, ALL_A_STOCKS_TS
    now = time.time()
    if not force_refresh and ALL_A_STOCKS_CACHE and (now - ALL_A_STOCKS_TS) < 3600:
        return ALL_A_STOCKS_CACHE

    try:
        from mootdx.quotes import Quotes
        import re
        client = Quotes.factory(market='std')
        df = client.stock_all()
        stocks = []
        for _, row in df.iterrows():
            c = str(row['code']).strip()
            n = str(row['name']).strip().replace('\u0000', '')
            if not re.match(r'^\d{6}$', c): continue
            if c.startswith(('200','900','395','399','880','881',
                             '882','883','884','885','886','887','888','889')): continue
            if any(kw in n for kw in ['A股','B股','基金','ETF','债券','REIT','LOF','回购']): continue
            if not n: continue
            if c.startswith(('0','3','6')):
                stocks.append({"code": c, "name": n})
        ALL_A_STOCKS_CACHE = stocks
        ALL_A_STOCKS_TS = now
        print(f"[Server] 全A股列表加载完成: {len(stocks)} 只")
        return stocks
    except Exception as e:
        print(f"[Server] mootdx获取失败: {e}")
        return _fallback_all_stocks()


def _fallback_all_stocks() -> list:
    """fallback到种子缓存"""
    cache = SEED_CACHE if SEED_CACHE else load_seed_cache()
    seen = set()
    stocks = []
    for ind_stocks in cache.values():
        for s in ind_stocks:
            if s["code"] not in seen:
                seen.add(s["code"])
                stocks.append(s)
    return stocks


# ========== 行业股票获取（种子缓存） ==========

SEED_CACHE = None


def load_seed_cache() -> dict:
    global SEED_CACHE
    if SEED_CACHE is not None:
        return SEED_CACHE
    if os.path.exists(SEED_FILE):
        try:
            with open(SEED_FILE, "r", encoding="utf-8") as f:
                SEED_CACHE = json.load(f)
        except Exception:
            SEED_CACHE = {}
    else:
        SEED_CACHE = {}
    return SEED_CACHE


def get_stocks_by_industry(industry_name: str) -> list:
    """获取行业股票列表（种子缓存）"""
    cache = load_seed_cache()
    return cache.get(industry_name, [])


# ========== 腾讯财经实时行情 ==========

def tencent_batch_quote(codes: list[str]) -> dict:
    """批量拉取腾讯财经实时行情"""
    result = {}
    batch_size = 200
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        prefixed = []
        for c in batch:
            if c.startswith(("6", "9")): prefixed.append(f"sh{c}")
            elif c.startswith("8"): prefixed.append(f"bj{c}")
            else: prefixed.append(f"sz{c}")

        url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
        try:
            req_obj = urllib.request.Request(url)
            req_obj.add_header("User-Agent", UA)
            resp = urllib.request.urlopen(req_obj, timeout=15)
            data = resp.read().decode("gbk")
            for line in data.strip().split(";"):
                if not line.strip() or "=" not in line or '"' not in line:
                    continue
                key = line.split("=")[0].split("_")[-1]
                vals = line.split('"')[1].split("~")
                if len(vals) < 53: continue
                code = key[2:]
                result[code] = _parse_tencent(vals)
        except Exception as e:
            print(f"腾讯财经批次拉取失败: {e}")

        if i + batch_size < len(codes):
            time.sleep(0.3)
    return result


def _parse_tencent(vals: list) -> dict:
    def f(idx, default=0.0):
        try: return float(vals[idx]) if vals[idx] else default
        except: return default
    return {
        "name": vals[1] if len(vals) > 1 else "",
        "price": f(3), "last_close": f(4), "open": f(5),
        "change_pct": f(32), "change_amt": f(31),
        "high": f(33), "low": f(34), "amount_wan": f(37),
        "turnover_pct": f(38), "pe_ttm": f(39), "amplitude_pct": f(43),
        "mcap_yi": f(44), "float_mcap_yi": f(45), "pb": f(46),
        "limit_up": f(47), "limit_down": f(48), "vol_ratio": f(49),
        "pe_static": f(52),
    }


# ========== 筛选 API ==========

class ScreenRequest(BaseModel):
    industry: Optional[str] = None
    min_pe: Optional[float] = None
    max_pe: Optional[float] = None
    min_pb: Optional[float] = None
    max_pb: Optional[float] = None
    min_mcap: Optional[float] = None
    max_mcap: Optional[float] = None
    min_change: Optional[float] = None
    max_change: Optional[float] = None
    min_turnover: Optional[float] = None
    max_turnover: Optional[float] = None
    min_vol_ratio: Optional[float] = None
    max_vol_ratio: Optional[float] = None
    min_ebit: Optional[float] = None  # EBIT(亿)下限
    max_ebit: Optional[float] = None  # EBIT(亿)上限
    min_ebit_margin: Optional[float] = None  # EBIT利润率(%)下限
    max_ebit_margin: Optional[float] = None  # EBIT利润率(%)上限
    min_chip_profit: Optional[float] = None  # 盈利筹码比例(%)下限
    max_chip_profit: Optional[float] = None  # 盈利筹码比例(%)上限
    min_mcap_pct: Optional[float] = None  # 市值在3年区间分位(%)下限
    max_mcap_pct: Optional[float] = None  # 市值在3年区间分位(%)上限
    min_heat: Optional[float] = None   # 话题热度最小值
    max_heat: Optional[float] = None   # 话题热度最大值
    sort_by: str = "change_pct"
    sort_order: str = "desc"
    page: int = 1
    page_size: int = 50
    max_results: int = 500


@app.post("/api/screen")
def screen_stocks(req: ScreenRequest):
    # 1. 获取股票列表
    if req.industry:
        stocks = get_stocks_by_industry(req.industry)
    else:
        stocks = get_all_a_stocks()

    if not stocks:
        return {"total": 0, "stocks": [], "message": "未找到股票"}

    # 限制数量防止超时：无筛选时限制2000只，有筛选条件时全量处理
    has_filters = any([
        req.industry, req.min_pe, req.max_pe, req.min_pb, req.max_pb,
        req.min_mcap, req.max_mcap, req.min_change, req.max_change,
        req.min_turnover, req.max_turnover, req.min_vol_ratio, req.max_vol_ratio,
        req.min_ebit, req.max_ebit, req.min_ebit_margin, req.max_ebit_margin,
        req.min_chip_profit, req.max_chip_profit,
        req.min_mcap_pct, req.max_mcap_pct,
        req.min_heat, req.max_heat,
    ])
    max_limit = 3000 if has_filters else 1500
    if len(stocks) > max_limit:
        stocks = stocks[:max_limit]

    # 2. 实时行情
    codes = [s["code"] for s in stocks]
    quotes = tencent_batch_quote(codes)

    # 3. 筛选
    ebit_cache = load_ebit_cache()
    results = []
    for s in stocks:
        q = quotes.get(s["code"])
        if not q or q["price"] == 0:
            continue

        pe, pb = q["pe_ttm"], q["pb"]
        mcap, change = q["mcap_yi"], q["change_pct"]
        turnover, vr = q["turnover_pct"], q["vol_ratio"]

        # EBIT 数据
        eb = ebit_cache.get(s["code"], {})
        if eb and not eb.get("error"):
            ebit_yi = format_ebit(eb.get("ebit"))
            ebit_margin = eb.get("ebit_margin")
            ttm_ebit_yi = format_ebit(eb.get("ttm_ebit"))
            revenue_yi = format_ebit(eb.get("revenue"))
        else:
            ebit_yi = ebit_margin = ttm_ebit_yi = revenue_yi = None

        if req.min_pe is not None and pe < req.min_pe: continue
        if req.max_pe is not None and pe > req.max_pe: continue
        if req.min_pb is not None and pb < req.min_pb: continue
        if req.max_pb is not None and pb > req.max_pb: continue
        if req.min_mcap is not None and mcap < req.min_mcap: continue
        if req.max_mcap is not None and mcap > req.max_mcap: continue
        if req.min_change is not None and change < req.min_change: continue
        if req.max_change is not None and change > req.max_change: continue
        if req.min_turnover is not None and turnover < req.min_turnover: continue
        if req.max_turnover is not None and turnover > req.max_turnover: continue
        if req.min_vol_ratio is not None and vr < req.min_vol_ratio: continue
        if req.max_vol_ratio is not None and vr > req.max_vol_ratio: continue
        # EBIT 筛选
        if req.min_ebit is not None and (ebit_yi is None or ebit_yi < req.min_ebit): continue
        if req.max_ebit is not None and (ebit_yi is None or ebit_yi > req.max_ebit): continue
        if req.min_ebit_margin is not None and (ebit_margin is None or ebit_margin < req.min_ebit_margin): continue
        if req.max_ebit_margin is not None and (ebit_margin is None or ebit_margin > req.max_ebit_margin): continue

        # 筹码分布
        chip = load_chip_cache().get(s["code"], {})
        chip_profit = chip.get("profit_ratio") if isinstance(chip, dict) else None
        mcap_pct = chip.get("mcap_percentile") if isinstance(chip, dict) else None
        if req.min_chip_profit is not None and (chip_profit is None or chip_profit < req.min_chip_profit): continue
        if req.max_chip_profit is not None and (chip_profit is None or chip_profit > req.max_chip_profit): continue
        if req.min_mcap_pct is not None and (mcap_pct is None or mcap_pct < req.min_mcap_pct): continue
        if req.max_mcap_pct is not None and (mcap_pct is None or mcap_pct > req.max_mcap_pct): continue

        # 话题热度分
        hs = heat_score.get_heat_score(s["code"])
        heat_val = hs.get("heat_score", None) if hs else None
        if req.min_heat is not None and (heat_val is None or heat_val < req.min_heat): continue
        if req.max_heat is not None and (heat_val is None or heat_val > req.max_heat): continue

        results.append({
            "code": s["code"], "name": q["name"],
            "price": round(q["price"], 2),
            "change_pct": round(q["change_pct"], 2),
            "pe_ttm": round(pe, 1) if pe > 0 else None,
            "pb": round(pb, 1) if pb > 0 else None,
            "mcap_yi": round(mcap, 1),
            "turnover_pct": round(turnover, 2),
            "vol_ratio": round(vr, 2) if vr > 0 else None,
            "amount_wan": round(q["amount_wan"], 0),
            "high": round(q["high"], 2), "low": round(q["low"], 2),
            "ebit_yi": ebit_yi,
            "ebit_margin": ebit_margin,
            "ttm_ebit_yi": ttm_ebit_yi,
            "revenue_yi": revenue_yi,
            "chip_profit": chip_profit,
            "mcap_pct": mcap_pct,
            "heat_score": heat_val,
            "heat_rank": hs.get("ths_rank") if hs else None,
            "heat_reason": hs.get("ths_reason", "") if hs else "",
        })

    # 4. 排序
    reverse = req.sort_order == "desc"
    sk = req.sort_by
    if sk == "change_pct":
        results.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=reverse)
    else:
        results.sort(key=lambda x: (x.get(sk, 0) or 0), reverse=reverse)

    total = len(results)
    results = results[:req.max_results]
    start = (req.page - 1) * req.page_size
    page_data = results[start:start + req.page_size]

    pe_vals = [r["pe_ttm"] for r in results if r["pe_ttm"]]
    pb_vals = [r["pb"] for r in results if r["pb"]]
    mc_vals = [r["mcap_yi"] for r in results if r["mcap_yi"]]

    return {
        "total": total, "page": req.page, "page_size": req.page_size,
        "stocks": page_data,
        "summary": {
            "avg_pe": round(sum(pe_vals)/len(pe_vals), 1) if pe_vals else None,
            "avg_pb": round(sum(pb_vals)/len(pb_vals), 1) if pb_vals else None,
            "avg_mcap": round(sum(mc_vals)/len(mc_vals), 1) if mc_vals else None,
        },
        "message": f"从 {len(stocks)} 只股票中筛选出 {total} 只"
    }


@app.get("/api/stats/{code}")
def stock_stats(code: str):
    codes = [code]
    quotes = tencent_batch_quote(codes)
    q = quotes.get(code)
    if not q:
        raise HTTPException(status_code=404, detail="未找到该股票")

    # 概念板块
    concepts = []
    try:
        url = f"https://finance.pae.baidu.com/api/getrelatedblock?code={code}&market=ab&typeCode=all&finClientType=pc"
        r = req.get(url, headers={"User-Agent": UA,
            "Origin": "https://gushitong.baidu.com",
            "Referer": "https://gushitong.baidu.com/"}, timeout=10)
        d = r.json()
        if str(d.get("ResultCode", -1)) == "0":
            for block in d.get("Result", []):
                if "概念" in block.get("type", ""):
                    concepts.extend(item.get("name","") for item in block.get("list",[]))
    except Exception:
        pass

    # EBIT 数据
    eb = load_ebit_cache().get(code, {})
    ebit_yi = format_ebit(eb.get("ebit")) if not eb.get("error") else None
    # 筹码分布
    chip = load_chip_cache().get(code, {})
    chip_profit = chip.get("profit_ratio") if isinstance(chip, dict) else None
    mcap_pct = chip.get("mcap_percentile") if isinstance(chip, dict) else None

    return {
        "code": code, "name": q["name"],
        "price": round(q["price"], 2),
        "change_pct": round(q["change_pct"], 2),
        "change_amt": round(q["change_amt"], 2),
        "open": round(q["open"], 2), "high": round(q["high"], 2),
        "low": round(q["low"], 2), "last_close": round(q["last_close"], 2),
        "amount_wan": round(q["amount_wan"], 0),
        "turnover_pct": round(q["turnover_pct"], 2),
        "pe_ttm": round(q["pe_ttm"], 1) if q["pe_ttm"] > 0 else None,
        "pb": round(q["pb"], 1) if q["pb"] > 0 else None,
        "pe_static": round(q["pe_static"], 1) if q["pe_static"] > 0 else None,
        "mcap_yi": round(q["mcap_yi"], 1),
        "float_mcap_yi": round(q["float_mcap_yi"], 1),
        "vol_ratio": round(q["vol_ratio"], 2) if q["vol_ratio"] > 0 else None,
        "amplitude_pct": round(q["amplitude_pct"], 2),
        "limit_up": round(q["limit_up"], 2),
        "limit_down": round(q["limit_down"], 2),
        "concepts": concepts,
        "ebit_yi": ebit_yi,
        "ebit_margin": eb.get("ebit_margin") if not eb.get("error") else None,
        "chip_profit": chip_profit,
        "mcap_pct": mcap_pct,
    }


# ========== 热度评分 ==========

@app.get("/api/heat_scores")
def get_heat_scores():
    """获取全部话题热度数据"""
    scores = heat_score.get_all_heat_scores()
    status = heat_score.get_cache_status()
    # 转为列表并按热度排序
    items = sorted(scores.values(), key=lambda x: -x["heat_score"])
    return {"scores": items, "total": len(items), "cache": status}


@app.get("/api/heat_score/{code}")
def get_stock_heat_score(code: str):
    """获取单只股票话题热度"""
    hs = heat_score.get_heat_score(code)
    if not hs:
        return {"code": code, "heat_score": None, "message": "暂无热度数据"}
    return hs


# ========== 静态文件 ==========

@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"))


# ========== 启动事件 ==========

@app.on_event("startup")
def startup():
    """服务器启动时预热热度缓存"""
    def _warmup():
        try:
            print("[Server] 正在预热热度缓存...")
            heat_score.refresh_cache()
            print("[Server] 热度缓存预热完成")
            heat_score.start_cache_refresher()
        except Exception as e:
            print(f"[Server] 热度缓存预热失败: {e}")
    thread = threading.Thread(target=_warmup, daemon=True)
    thread.start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
