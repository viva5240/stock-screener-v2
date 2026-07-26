"""
筹码分布 + 3年市值分位估算
基于历史K线数据计算：
1. 盈利筹码比例（近120天）
2. 市值在近3年区间中所处分位（近800天）
"""
import json
import time
import os
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SEED_FILE = os.path.join(DATA_DIR, "industry_stocks.json")
CHIP_FILE = os.path.join(DATA_DIR, "chip_cache.json")
DAYS_CHIP = 120    # 筹码盈利比例用120天
DAYS_3Y = 800      # 3年市值分位用800天（约3年3个月）


def fetch_kline_sina(code: str, days: int = DAYS_3Y) -> list | None:
    """从新浪获取日K线数据"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/"
           f"json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}"
           f"&scale=240&ma=no&datalen={days}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list) and len(data) > 0:
            return data
    except Exception:
        pass
    return None


def calc_profit_ratio(kline: list, current_price: float) -> float:
    """计算近似的盈利筹码比例（近DAYS_CHIP天）"""
    recent = kline[-DAYS_CHIP:] if len(kline) > DAYS_CHIP else kline
    total_vol = 0.0
    profit_vol = 0.0
    for bar in recent:
        close = float(bar.get("close", 0))
        vol = float(bar.get("volume", 0))
        if vol <= 0: continue
        total_vol += vol
        if current_price > close:
            profit_vol += vol
    return round(profit_vol / total_vol * 100, 1) if total_vol else 0.0


def calc_mcap_percentile(kline: list, current_price: float) -> float:
    """计算当前价格在近3年区间中的分位（近似市值分位）"""
    min_p = min(float(bar.get("close", 0)) for bar in kline)
    max_p = max(float(bar.get("close", 0)) for bar in kline)
    if max_p <= min_p:
        return 50.0
    pct = (current_price - min_p) / (max_p - min_p) * 100
    return round(max(0, min(100, pct)), 1)


def get_all_a_stocks_mootdx() -> list:
    """从通达信获取全部A股代码列表"""
    try:
        from mootdx.quotes import Quotes
        import re
        client = Quotes.factory(market='std')
        df = client.stock_all()
        codes = []
        for _, row in df.iterrows():
            c = str(row['code']).strip()
            n = str(row['name']).strip().replace('\u0000', '')
            if not re.match(r'^\d{6}$', c): continue
            if c.startswith(('200','900','395','399','880','881','882','883','884','885','886','887','888','889')): continue
            if any(kw in n for kw in ['A股','B股','基金','ETF','债券','REIT','LOF','回购']): continue
            if not n: continue
            if c.startswith(('0','3','6')):
                codes.append(c)
        return codes
    except Exception as e:
        print(f"mootdx获取失败: {e}")
        # Fallback: 从seed cache读取
        if os.path.exists(SEED_FILE):
            try:
                with open(SEED_FILE, "r", encoding="utf-8") as f:
                    seed = json.load(f)
                codes = list(set(s["code"] for v in seed.values() for s in v))
                if codes:
                    print(f"fallback到种子缓存: {len(codes)} 只")
                    return codes
            except Exception:
                pass
        return []


def batch_fetch():
    """批量从全A股拉取K线计算盈利比例+市值分位"""
    all_codes = get_all_a_stocks_mootdx()
    if not all_codes:
        print("❌ 无法获取A股列表")
        return
    print(f"🎯 全A股: {len(all_codes)} 只")

    # 已有缓存
    cache = {}
    if os.path.exists(CHIP_FILE):
        with open(CHIP_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

    total = len(all_codes)
    new_count = 0
    fail_count = 0

    print(f"📋 已有缓存: {len(cache)} 只")
    print(f"🔄 需要拉取: {len([c for c in all_codes if c not in cache])} 只")

    for i, code in enumerate(sorted(all_codes)):
        if code in cache:
            continue

        # 获取K线
        kline = fetch_kline_sina(code)
        if not kline:
            fail_count += 1
            if (i + 1) % 20 == 0:
                print(f"  ❌ [{i+1}/{total}] {code}: K线获取失败")
            continue

        # 获取当前价格
        prefix = "sh" if code.startswith("6") else "sz"
        url = f"https://qt.gtimg.cn/q={prefix}{code}"
        current_price = 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            resp = urllib.request.urlopen(req, timeout=8)
            data = resp.read().decode("gbk")
            vals = data.split('"')[1].split("~")
            if len(vals) > 3:
                p = float(vals[3])
                if p > 0:
                    current_price = p
        except Exception:
            pass

        if current_price <= 0:
            fail_count += 1
            continue

        # 计算盈利比例
        ratio = calc_profit_ratio(kline, current_price)
        # 计算3年市值分位
        mcap_pct = calc_mcap_percentile(kline, current_price)
        # 3年价格区间
        prices = [float(b.get("close", 0)) for b in kline if float(b.get("close", 0)) > 0]
        min_p = min(prices) if prices else 0
        max_p = max(prices) if prices else 0

        cache[code] = {
            "profit_ratio": ratio,
            "mcap_percentile": mcap_pct,
            "price_3y_min": round(min_p, 2),
            "price_3y_max": round(max_p, 2),
            "current_price": round(current_price, 2),
            "kline_days": len(kline),
        }
        new_count += 1

        if new_count % 10 == 0:
            print(f"  ✅ [{i+1}/{total}] {code}: 盈利比例={ratio}% 市值分位={mcap_pct}% (共{new_count}只)")

        # 限流
        time.sleep(0.15)

        # 每50只保存
        if new_count > 0 and new_count % 50 == 0:
            with open(CHIP_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=None)
            print(f"  💾 已保存 ({i+1}/{total})")

    # 最终保存
    with open(CHIP_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=None)

    print(f"\n🎯 完成！有效数据: {len(cache)}/{total}")
    print(f"📁 保存至: {CHIP_FILE}")


if __name__ == "__main__":
    batch_fetch()
