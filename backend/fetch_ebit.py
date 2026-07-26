"""
EBIT 全量数据抓取 V2 - 覆盖所有A股（5660只）
数据源：新浪财报利润表 → EBIT(营业利润)
缓存策略：增量续传（已存在的跳过）
"""
import json, time, random, os, urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
EBIT_FILE = os.path.join(DATA_DIR, "ebit_cache.json")


def get_all_a_stocks_mootdx() -> list:
    """从通达信获取全部A股列表"""
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
            if c.startswith(('200','900','395','399','880','881','882','883','884','885','886','887','888','889')): continue
            if any(kw in n for kw in ['A股','B股','基金','ETF','债券','REIT','LOF','回购']): continue
            if not n: continue
            if c.startswith(('0','3','6')):
                stocks.append(c)
        return stocks
    except Exception as e:
        print(f"mootdx获取失败: {e}")
        return []


def fetch_lrb(code: str) -> dict | None:
    """获取单只股票最新利润表"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = (f"https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService"
           f".getFinanceReport2022?paperCode={prefix}{code}&source=lrb&type=0&page=1&num=6")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        report_list = data.get("result", {}).get("data", {}).get("report_list", {})
        if not report_list:
            return None

        periods = sorted(report_list.keys(), reverse=True)
        records = []
        for period in periods:
            fields = {}
            for it in report_list[period].get("data", []):
                fields[it.get("item_title", "")] = it.get("item_value", "0")
            revenue = float(fields.get("营业总收入", "0").replace(",", ""))
            op_profit = float(fields.get("营业利润", "0").replace(",", ""))
            net_profit = float(fields.get("净利润", "0").replace(",", ""))
            fin_cost = float(fields.get("财务费用", "0").replace(",", ""))
            tax = float(fields.get("所得税费用", "0").replace(",", ""))
            ebit = op_profit
            ebit_precise = net_profit + tax + fin_cost
            ptype = "年报" if period.endswith(("1231", "12-31")) else \
                    ("中报" if period.endswith(("0630", "06-30")) else "季报")
            records.append({
                "period": period, "period_type": ptype,
                "revenue": revenue, "ebit": ebit,
                "ebit_margin": round(ebit / revenue * 100, 2) if revenue else 0,
                "net_profit": net_profit, "ebit_precise": ebit_precise,
            })

        if not records: return None
        latest_annual = next((r for r in records if r["period_type"] == "年报"), None)
        chosen = latest_annual or records[0]

        return {
            "code": code,
            "latest_period": chosen["period"],
            "period_type": chosen["period_type"],
            "revenue": round(chosen["revenue"], 0),
            "ebit": round(chosen["ebit"], 0),
            "ebit_margin": chosen["ebit_margin"],
            "net_profit": round(chosen["net_profit"], 0),
            "ttm_ebit": round(sum(r["ebit"] for r in records[:4]), 0) if len(records) >= 4 else round(chosen["ebit"], 0),
        }
    except Exception as e:
        return None


def format_ebit(val):
    return round(val / 1e8, 1) if val else None


def batch_fetch():
    """批量从全A股拉取EBIT"""
    # 1. 获取全A股列表
    all_codes = get_all_a_stocks_mootdx()
    if not all_codes:
        print("❌ 无法获取A股列表")
        return
    print(f"🎯 全A股: {len(all_codes)} 只")

    # 2. 加载已有缓存
    cache = {}
    if os.path.exists(EBIT_FILE):
        with open(EBIT_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

    # 去掉已缓存的
    todo = [c for c in all_codes if c not in cache]
    print(f"📋 已有缓存: {len(cache)} 只")
    print(f"🔄 需要拉取: {len(todo)} 只")

    new_count = 0
    fail_count = 0
    skip_count = len(cache)

    for i, code in enumerate(todo):
        result = fetch_lrb(code)
        if result:
            cache[code] = result
            new_count += 1
            ebit_str = f"EBIT={format_ebit(result['ebit'])}亿" if result['ebit'] else "N/A"
            print(f"  ✅ [{new_count}/{len(todo)}] {code}: {ebit_str} 利润率={result['ebit_margin']}%")
        else:
            fail_count += 1
            cache[code] = {"code": code, "error": True}
            if (i + 1) % 20 == 0:
                print(f"  ❌ [{i+1}/{len(todo)}] {code}: 获取失败")

        # 限流
        if (i + 1) % 5 == 0:
            time.sleep(random.uniform(0.3, 0.6))

        # 每50只保存
        if new_count > 0 and (i + 1) % 50 == 0:
            with open(EBIT_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=None)
            print(f"  💾 已保存 ({i+1}/{len(todo)}, 有效{new_count}, 失败{fail_count})")

    # 最终保存
    with open(EBIT_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=None)

    valid = sum(1 for v in cache.values() if isinstance(v, dict) and not v.get("error"))
    print(f"\n🎯 完成！EBIT缓存: {len(cache)}/{len(all_codes)}, 有效: {valid}")
    print(f"📁 {EBIT_FILE}")


if __name__ == "__main__":
    batch_fetch()
