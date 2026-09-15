# -*- coding: utf-8 -*-
# 生成《龙虎榜》HTML 报告
# 输入: 全市场龙虎榜实时拉取
# 输出: 脚本所在目录/lhb.html（日期为当天）
import time, random, requests, os, json, re
import datetime

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_15_7) AppleWebKit/537.36"
EM_SESSION = requests.Session()
EM_SESSION.trust_env = False  # 绕过系统代理，避免代理故障导致请求失败
EM_SESSION.headers.update({"User-Agent": UA})
TXT_OUTPUT_FILE = "lhb.html"

_em_last = [0.0]
def em_get(url, params=None, headers=None, timeout=15):
    wait = 1.2 - (time.time() - _em_last[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.4))
    try:
        r = EM_SESSION.get(url, params=params, headers=headers, timeout=timeout)
    finally:
        _em_last[0] = time.time()
    return r

DATE = time.strftime("%Y-%m-%d")

# 报告日期: 开盘(9:30)前显示昨日, 开盘后显示当日(速览类实时数据随开盘切换)
_now = datetime.datetime.now()
_market_open = _now.hour > 9 or (_now.hour == 9 and _now.minute >= 30)
REPORT_DATE = DATE if _market_open else (
    datetime.datetime.strptime(DATE, "%Y-%m-%d") - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

def db(report, filt, sort_col, sort_type="-1", size=500):
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {"reportName": report, "columns": "ALL", "filter": filt,
              "pageNumber": "1", "pageSize": str(size), "sortColumns": sort_col,
              "sortTypes": sort_type, "source": "WEB", "client": "WEB"}
    r = em_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    return (d.get("result") or {}).get("data") or []

# ---- 1. 全市场龙虎榜（当日无数据时回退到昨日）----
print("[1] 拉全市场龙虎榜 ...")
lhb, lhb_date = [], DATE
try:
    rows = db("RPT_DAILYBILLBOARD_DETAILSNEW",
              f"(TRADE_DATE>='{DATE}')(TRADE_DATE<='{DATE}')",
              "BILLBOARD_NET_AMT", "-1", 500)
    if not rows:
        lhb_date = (datetime.datetime.strptime(DATE, "%Y-%m-%d") -
                    datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        rows = db("RPT_DAILYBILLBOARD_DETAILSNEW",
                  f"(TRADE_DATE='{lhb_date}')", "BILLBOARD_NET_AMT", "-1", 500)
        if rows:
            print(f"  当日无龙虎榜，回退到昨日: {lhb_date}")
    seen = set()
    for it in rows:
        code = it.get("SECURITY_CODE")
        if code in seen:
            continue
        seen.add(code)
        lhb.append({
            "code": code, "name": it.get("SECURITY_NAME_ABBR"),
            "reason": (it.get("EXPLANATION") or ""),
            "net_buy_wan": round((it.get("BILLBOARD_NET_AMT") or 0)/1e4, 1),
            "buy_wan": round((it.get("BILLBOARD_BUY_AMT") or 0)/1e4, 1),
            "sell_wan": round((it.get("BILLBOARD_SELL_AMT") or 0)/1e4, 1),
            "chg": it.get("CHANGE_RATE"), "close": it.get("CLOSE_PRICE"),
            "turnover": it.get("TURNOVERRATE"),
        })
except Exception as e:
    print("  龙虎榜失败:", repr(e))
print(f"  共 {len(lhb)} 只（{lhb_date}）")

# ---- 2. 匹配紫阳东路席位（接口不支持like，拉全量明细本地过滤；日期跟随 lhb_date）----
print("[2] 匹配紫阳东路席位 ...")
zy_amt = {}   # code -> {"buy": 万, "sell": 万}
try:
    rows = db("RPT_BILLBOARD_DAILYDETAILSBUY", f"(TRADE_DATE='{lhb_date}')", "TRADE_DATE", "-1", 500)
    for it in rows:
        if "紫阳东路" in (it.get("OPERATEDEPT_NAME") or ""):
            code = it.get("SECURITY_CODE")
            d = zy_amt.setdefault(code, {"buy": 0.0, "sell": 0.0})
            d["buy"] += (it.get("BUY") or 0)/1e4
    rows = db("RPT_BILLBOARD_DAILYDETAILSSELL", f"(TRADE_DATE='{lhb_date}')", "TRADE_DATE", "-1", 500)
    for it in rows:
        if "紫阳东路" in (it.get("OPERATEDEPT_NAME") or ""):
            code = it.get("SECURITY_CODE")
            d = zy_amt.setdefault(code, {"buy": 0.0, "sell": 0.0})
            d["sell"] += (it.get("SELL") or 0)/1e4
except Exception as e:
    print("  紫阳东路匹配失败:", repr(e))
zy_codes = set(zy_amt)
print(f"  紫阳东路上榜: {len(zy_codes)} 只")

# ---- 3. 拉取板块（东财 F10，in 批量查询，50 只/批） ----
print("[3] 拉取个股板块 ...")
def secucode(code):
    return code + (".SZ" if code[0] in "03" else (".BJ" if code[0] in "48" else ".SH"))

boards = {}
try:
    codes = [r["code"] for r in lhb]
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        f = "(" + ",".join(f'"{secucode(c)}"' for c in batch) + ")"
        rows = db("RPT_F10_BASIC_ORGINFO", f"(SECUCODE in {f})", "SECUCODE", "-1", 50)
        for it in rows:
            em2016 = (it.get("EM2016") or "")
            boards[it["SECUCODE"].split(".")[0]] = em2016.split("-")[0] if em2016 else None
except Exception as e:
    print("  板块拉取失败:", repr(e))
print(f"  板块命中: {sum(1 for v in boards.values() if v)}/{len(lhb)}")

# ---- 4. AI 分析（Python 端调 kilo.ai，避免浏览器 CORS） ----
print("[4] AI 分析 ...")
def fmt_wan(w):
    if w is None:
        return "-"
    if abs(w) >= 10000:
        return f"{w/1e4:.2f}亿"
    return f"{w:.0f}万"

def fmt_wan_signed(w):
    if w is None:
        return "-"
    s = "+" if w >= 0 else ""
    return s + fmt_wan(w)

def ai_analyze():
    lines = []
    for r in sorted(lhb, key=lambda x: -(x["net_buy_wan"] or 0)):
        code = r["code"]
        if code not in zy_codes:
            continue
        d = zy_amt[code]
        parts = []
        if d["buy"] > 0:
            parts.append(f"买{fmt_wan(d['buy'])}")
        if d["sell"] > 0:
            parts.append(f"卖{fmt_wan(d['sell'])}")
        zy_s = " ".join(parts) if parts else "-"
        chg = r["chg"] if r["chg"] is not None else 0
        turn = f"{r['turnover']:.0f}%" if r["turnover"] is not None else "-"
        lines.append(f"{code} {r['name']} 涨跌{chg:+.2f}% 净买{fmt_wan_signed(r['net_buy_wan'])} 换手{turn} 板块:{boards.get(code) or '-'} 紫阳东路:{zy_s}")
    prompt = (f"以下是{DATE}龙虎榜数据，均为游资席位（武汉紫阳东路营业部）买入或卖出的个股，"
              "\"紫阳东路\"列为该席位的买卖金额：\n"
              + "\n".join(lines)
              + "\n\n请分析紫阳东路买入和卖出的个股，结合当前市场题材和情绪，以及个股的行业定位，"
                "唱多或唱空分析，得出走势预判、仓位建议、关注方向、避坑板块及风险提示等。用中文，分点输出。")
    try:
        last_err = None
        for attempt in range(3):  # 免费网关偶发超时/限流, 最多重试3次
            try:
                s = requests.Session()
                s.trust_env = False  # 绕过系统代理
                resp = s.post(
                    "https://api.kilo.ai/api/gateway/v1/chat/completions",
                    headers={"Authorization": "Bearer 12345678", "Content-Type": "application/json"},
                    json={"model": "openrouter/free",
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=180)
                resp.raise_for_status()
                d = resp.json()
                text = (d["choices"][0]["message"].get("content") or "").strip()
                if text:
                    return text
                last_err = "empty content"
            except Exception as e:
                last_err = repr(e)
            print(f"  AI 分析第{attempt+1}次失败: {last_err}, 重试...")
            time.sleep(3 * (attempt + 1))
        print("  AI 分析失败(已重试3次):", last_err)
        return None
    except Exception as e:
        print("  AI 分析失败:", repr(e))
        return None

# AI 分析放后台线程，与市场速览/HTML 生成并行，最后写文件前再等待
import threading
ai_text = None
def _ai_thread():
    global ai_text
    ai_text = ai_analyze() if lhb else None
    print(f"  AI 分析: {'OK ' + str(len(ai_text)) + ' 字' if ai_text else '跳过/失败'}")
ai_thread = threading.Thread(target=_ai_thread, daemon=True)
ai_thread.start()

# ---- 5. 市场速览数据 ----
def fetch_market_stats(s, lhb_count=0, lhb_total_chg=0, date_str=None):
    """市场速览: 指数行情 + 涨跌家数/涨跌停/总成交/昨日上榬/多板"""
    stats = {"indexes": [], "up": 0, "down": 0, "zt": 0, "dt": 0,
             "amount": 0.0, "lhb_count": lhb_count, "lhb_chg": lhb_total_chg,
             "duoban": 0, "duoban_chg": 0, "max_lbc": 0}

    # --- 指数: 腾讯行情(上证/创业板指/科创综指) ---
    IDX = [("sh000001", "上证指数"), ("sz399006", "创业板指"), ("sh000688", "科创综指")]
    try:
        r = s.get("https://qt.gtimg.cn/q=" + ",".join(f"s_{m}" for m, _ in IDX),
                   headers={"User-Agent": UA}, timeout=15)
        for m, n in IDX:
            mch = re.search(rf'v_s_{m}="([^"]*)"', r.text)
            if not mch:
                continue
            parts = mch.group(1).split("~")
            if len(parts) > 6:
                try:
                    stats["indexes"].append({
                        "name": n,
                        "price": float(parts[3]),
                        "chg_pct": float(parts[5]) if parts[5] else 0.0,
                    })
                except ValueError:
                    pass
    except Exception:
        pass

    # --- 多板: 东财涨停池（获取多板股列表 + 平均涨幅） ---
    try:
        yesterday = (date_str or REPORT_DATE).replace("-", "")
        r = s.get("https://push2ex.eastmoney.com/getTopicZTPool", params={
            "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": "0", "pagesize": "1000", "sort": "fbt:asc",
            "date": yesterday}, headers={"User-Agent": UA}, timeout=15)
        pool = (r.json().get("data") or {}).get("pool") or []
        duoban_stocks = [p for p in pool if (p.get("lbc") or 1) >= 2]
        stats["duoban"] = len(duoban_stocks)
        stats["max_lbc"] = max((p.get("lbc") or 1 for p in duoban_stocks), default=0)
        if duoban_stocks:
            stats["duoban_chg"] = sum(float(p.get("zdp") or 0) for p in duoban_stocks) / len(duoban_stocks)
        # 连板梯队明细: 全部涨停(含首板)按连板数分组, 组内按涨幅降序; 1板默认隐藏
        ladder = {}
        for p in pool:
            lbc = p.get("lbc") or 1
            ladder.setdefault(lbc, []).append({
                "name": p.get("n") or "",
                "chg": float(p.get("zdp") or 0),
            })
        stats["ladder"] = sorted(
            ({"lbc": k, "stocks": sorted(v, key=lambda x: -x["chg"])}
             for k, v in ladder.items()), key=lambda x: -x["lbc"])
    except Exception:
        pass

    # --- 市场统计: 新浪 A 股列表分页 ---
    def limit_by_code(code, name):
        """按代码段判定涨跌停阈值(±%): 北交所/创业板30 / ST5 / 主板10"""
        c = str(code)
        if name and "ST" in name:
            return 5.0
        if c.startswith(("8", "9", "300", "301")):
            return 30.0
        if c.startswith("688"):
            return 20.0
        return 10.0

    for node in ("hs_a", "bj_a"):
        try:
            page = 1
            while page <= 100:
                r = s.get(
                    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                    params={"node": node, "num": "100", "page": str(page),
                            "sort": "symbol", "asc": "1"},
                    headers={"User-Agent": UA}, timeout=15)
                try:
                    data = r.json()
                except ValueError:
                    break
                if not isinstance(data, list) or not data:
                    break
                for it in data:
                    try:
                        cp = float(it.get("changepercent") or 0)
                        amt = float(it.get("amount") or 0)
                        stats["amount"] += amt
                        if cp > 0:
                            stats["up"] += 1
                        elif cp < 0:
                            stats["down"] += 1
                        th = limit_by_code(it.get("code", ""), it.get("name", ""))
                        if cp >= th - 0.05:
                            stats["zt"] += 1
                        if cp <= -(th) + 0.05:
                            stats["dt"] += 1
                    except (TypeError, ValueError):
                        continue
                if len(data) < 100:
                    break
                page += 1
        except Exception:
            continue
    print(f"  市场速览: 涨{stats['up']} 跌{stats['down']} 涨停{stats['zt']} 跌停{stats['dt']} 成交{stats['amount']/1e8:.0f}亿")
    return stats


# ---- 6. 生成 HTML ----
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ---- 5.1 市场速览卡片HTML ----
lhb_avg_chg = sum(float(r["chg"] or 0) for r in lhb) / len(lhb) if lhb else 0.0
market_stats = fetch_market_stats(EM_SESSION, len(lhb), lhb_avg_chg, REPORT_DATE)
overview_html = f'<span class="mcard"><label>总成交</label><b>{market_stats["amount"] / 1e8:.0f} 亿</b><em>元</em></span>'
if market_stats["indexes"]:
    for i in range(3):
        idx = market_stats["indexes"][i]
        chg = idx["chg_pct"]
        cls = "up" if chg >= 0 else "down"
        overview_html += f'<span class="mcard"><label>{esc(idx["name"])}</label><b class="{cls}">{chg:+.2f}%</b></span>'
overview_html += f'''
<span class="mcard"><label>昨日上榜</label><b>{market_stats["lhb_count"]}</b><em>只</em></span>
<span class="mcard"><label>上涨家数</label><b class="up">{market_stats["up"]}</b><em class="up">家</em></span>
<span class="mcard"><label>下跌家数</label><b class="down">{market_stats["down"]}</b><em class="down">家</em></span>
<span class="mcard"><label>涨停数</label><b class="up">{market_stats["zt"]}</b><em class="up">只</em></span>
<span class="mcard"><label>跌停数</label><b class="down">{market_stats["dt"]}</b><em class="down">只</em></span>'''

# 龙虎榜总表HTML（紫阳东路命中置顶；前10行外加 hidden 类，点按钮展开）
lhb_rows = ""
for i, r in enumerate(sorted(lhb, key=lambda x: (x["code"] not in zy_codes, -(x["net_buy_wan"] or 0)))):
    is_zy = r["code"] in zy_codes
    row_cls = ' class="zy"' if is_zy else ""
    if i >= 10:
        row_cls = (' class="zy hidden"' if is_zy else ' class="hidden"')
    net = r["net_buy_wan"] or 0
    net_cls = "up" if net >= 0 else "down"
    chg = r["chg"] or 0
    chg_cls = "up" if chg >= 0 else "down"
    zy_mark = '<span class="zy-badge">-</span>'
    if is_zy:
        d = zy_amt[r["code"]]
        parts = []
        if d["buy"] > 0:
            parts.append(f'<span class="zy-buy">买{fmt_wan(d["buy"])}</span>')
        if d["sell"] > 0:
            parts.append(f'<span class="zy-sell">卖{fmt_wan(d["sell"])}</span>')
        zy_mark = f'<span class="zy-badge">{" ".join(parts)}</span>'
    board = boards.get(r["code"]) or "-"
    turn = f"{r['turnover']:.0f}%" if r['turnover'] is not None else "-"
    name_html = esc(r['name'])
    if is_zy:
        name_html = f'<span class="zy-name">{name_html}</span>'
    lhb_rows += f"""<tr{row_cls}><td>{i+1}</td><td>{r['code']}</td><td><strong>{name_html}</strong></td>
      <td class="{chg_cls}">{chg:+.2f}%</td>
      <td class="{net_cls}">{fmt_wan_signed(net)}</td>
      <td>{turn}</td>
      <td>{esc(board)}</td>
      <td>{zy_mark}</td></tr>"""

# 板块汇总表（按板块聚合：家数/净买额合计/紫阳东路家数/个股清单）
board_stat = {}
for r in lhb:
    b = boards.get(r["code"]) or "未知"
    st = board_stat.setdefault(b, {"n": 0, "net": 0.0, "zy": 0, "names": []})
    st["n"] += 1
    st["net"] += (r["net_buy_wan"] or 0)
    if r["code"] in zy_codes:
        st["zy"] += 1
    st["names"].append(r["name"])

board_rows = ""
RANK_CLS = {1: "rank-1", 2: "rank-2", 3: "rank-3"}
RANK_NAME = {1: "bd-name-rk-1", 2: "bd-name-rk-2", 3: "bd-name-rk-3"}
for i, (b, st) in enumerate(sorted(board_stat.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["net"])), 1):
    net_cls = "up" if st["net"] >= 0 else "down"
    row_cls = ' class="hidden"' if i > 10 else ""
    rank_cls = RANK_CLS.get(i, "")
    rank_td = f'<td class="rank {rank_cls}">{i}</td>' if rank_cls else f'<td class="rank">{i}</td>'
    bd_cls = RANK_NAME.get(i, "")
    bd_name = f'<strong class="bd-name {bd_cls}" style="color:#c08bff">{esc(b)}</strong>' if bd_cls else '<strong class="bd-name">' + esc(b) + '</strong>'
    zy_td = f'<span class="zy-badge">{st["zy"]}</span>' if st["zy"] else '-'
    names_html = "、".join(
        f'<span class="cp-name zy-name">{esc(n)}</span>' if code in zy_codes else f'<span class="cp-name">{esc(n)}</span>'
        for code, n in zip([r["code"] for r in lhb if (boards.get(r["code"]) or "未知") == b],
                           st["names"]))
    board_rows += f"""<tr{row_cls}>{rank_td}<td>{bd_name}</td>
      <td>{st['n']}</td>
      <td class="{net_cls}">{fmt_wan_signed(st['net'])}</td>
      <td>{zy_td}</td>
      <td class="reason">{names_html}</td></tr>"""

# 连板梯队表HTML（按连板数降序，每行一个梯队，组内按涨幅降序）
ladder_rows = ""
for g in market_stats.get("ladder", []):
    stocks_html = "、".join(
        f'<span class="cp-name">{esc(s["name"])}</span>'
        f'<i class="{"up" if s["chg"] >= 0 else "down"}">{s["chg"]:+.1f}%</i>'
        for s in g["stocks"])
    row_cls = ' class="hidden"' if g["lbc"] == 1 else ""
    ladder_rows += f"""<tr{row_cls}><td><strong class="lbc">{g["lbc"]} 板</strong></td>
      <td>{len(g["stocks"])}</td>
      <td class="reason">{stocks_html}</td></tr>"""

n_1ban = sum(len(g["stocks"]) for g in market_stats.get("ladder", []) if g["lbc"] == 1)
ladder_more = (f'<div class="more"><button onclick="var hs=this.closest(\'.panel\').querySelectorAll(\'tr.hidden\');'
               f'for(var i=0;i<hs.length;i++)hs[i].classList.remove(\'hidden\');this.parentNode.style.display=\'none\'">'
               f'展开全部（1板 {n_1ban} 只）</button></div>') if n_1ban else ""

html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>龙虎榜 {DATE}</title>
<style>
:root{{--bg:#0a0e17;--panel:#0e1626;--line:#1e2c44;--text:#e8f0ff;--muted:#7d93b3;
--red:#ff5c6c;--green:#2fd39b;--gold:#ffc96b;--blue:#6aa9ff;--purple:#c08bff}}
*{{box-sizing:border-box}}html,body{{margin:0;background:var(--bg);color:var(--text);
font:14px/1.55 Inter,"Microsoft YaHei",sans-serif;scrollbar-width:none;
-ms-overflow-style:none}}
html::-webkit-scrollbar,body::-webkit-scrollbar{{display:none;width:0;height:0}}
.wrap{{width:min(1400px,calc(100% - 32px));margin:0 auto;padding:16px 0 60px}}
h1{{font-size:22px;margin:6px 0 4px}}h1 span{{color:var(--blue)}}.sub{{color:var(--muted);font-size:13px}}
.up{{color:var(--red)}}.down{{color:var(--green)}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-top:10px}}
.panel h2{{margin:0 0 14px;font-size:16px}}
.panel-head{{display:flex;justify-content:space-between;align-items:baseline}}
.panel-head h2{{margin:0 0 14px}}
.panel-head .date{{color:var(--muted);font-size:13px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:var(--muted);font-size:11px;letter-spacing:.04em;padding:9px 10px;border-bottom:1px solid var(--line)}}
th.sortable{{cursor:pointer;user-select:none}}
th.sortable:hover{{color:var(--text)}}
th .arr{{font-size:9px;margin-left:3px;opacity:.7}}
td{{padding:5px 10px;border-bottom:1px solid #16233a;vertical-align:middle}}
tbody tr:hover{{background:#101c31}}
.reason{{color:var(--muted);font-size:12px;max-width:300px}}
tr.zy td{{background:#3a3210}}
.zy-badge{{color:#ffd97a;font-weight:700;margin-left:4px}}
td.rank{{color:var(--muted);font-weight:700;text-align:center}}
td.rank-1{{color:#c08bff}}
td.rank-2{{color:#c08bff}}
td.rank-3{{color:#c08bff}}
.bd-name-rk-1{{color:#c08bff}}
.bd-name-rk-2{{color:#c08bff}}
.bd-name-rk-3{{color:#c08bff}}
.cp-name{{cursor:pointer}}
.cp-name:hover{{color:var(--blue)}}
.zy-name{{color:#ffd97a}}
.th-zy{{color:#ffd97a !important}}
.zy-buy{{color:#ff5c6c}}.zy-sell{{color:#2fd39b}}
tbody td strong{{cursor:pointer}}
tbody td strong:hover{{color:var(--blue)}}
.copied{{color:#2fd39b !important}}
tr.hidden{{display:none}}
.more{{margin-top:10px;text-align:center}}
.more button{{background:#12203a;color:#6aa9ff;border:1px solid #24406b;border-radius:6px;
padding:6px 18px;font-size:13px;cursor:pointer}}
.more button:hover{{background:#1a2c4d}}
.ai-btn{{background:#3a2a10;color:#ffd97a;border:1px solid #8a6d1e;border-radius:6px;
padding:4px 14px;font-size:13px;font-weight:700;cursor:pointer}}
.ai-btn:hover{{background:#4a3515}}
#ai-box h1,#ai-box h2,#ai-box h3,#ai-box h4,#ai-box h5,#ai-box h6{{color:var(--gold);margin:10px 0 6px;font-size:15px}}
#ai-box h1{{font-size:17px}}#ai-box h2{{font-size:16px;border-bottom:1px solid var(--line);padding-bottom:5px}}
#ai-box .md-p{{margin:5px 0;line-height:1.6}}
#ai-box .md-list{{margin:4px 0 4px 18px;padding-left:12px}}
#ai-box .md-list li{{margin:2px 0;line-height:1.55}}
#ai-box ol.md-list{{list-style:decimal}}#ai-box ul.md-list{{list-style:disc}}
#ai-box .md-quote{{margin:8px 0;padding:6px 12px;border-left:3px solid var(--gold);background:#1a2333;border-radius:0 6px 6px 0;color:#d8e4ff}}
#ai-box .md-quote p{{margin:2px 0;line-height:1.6}}
#ai-box .md-table{{width:100%;border-collapse:collapse;margin:8px 0;font-size:12.5px}}
#ai-box .md-table th{{background:#16233a;color:var(--gold);padding:5px 8px;border:1px solid var(--line);font-size:12px}}
#ai-box .md-table td{{padding:4px 8px;border:1px solid var(--line);line-height:1.5}}
#ai-box .md-table tr:hover td{{background:#101c31}}
#ai-box hr{{border:none;border-top:1px solid var(--line);margin:10px 0}}
#ai-box code{{background:#16233a;color:var(--green);padding:1px 6px;border-radius:4px;font-size:12.5px}}
#ai-box pre code{{display:block;padding:10px 12px;line-height:1.6;overflow-x:auto}}
#ai-box strong{{color:#ffd97a}}
footer{{color:var(--muted);font-size:12px;margin-top:30px;border-top:1px solid var(--line);padding-top:12px}}
.overview{{display:flex;flex-wrap:wrap;gap:10px;padding:4px 0}}
.mcard{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:120px;flex:1 1 0;text-align:center;max-width:200px}}
.mcard label{{display:block;color:var(--muted);font-size:11px;margin-bottom:4px}}
.mcard b{{font-size:16px;font-weight:700;margin-right:4px}}
.mcard em{{font-style:normal;font-size:12px;color:var(--muted)}}
.mcard em.up{{color:var(--red)}}.mcard em.down{{color:var(--green)}}
.mcard i{{display:block;font-style:normal;font-size:12px;margin-top:2px}}
.lbc{{color:var(--gold)}}
.reason .cp-name{{margin-right:2px}}
.reason i{{font-style:normal;font-size:11px;color:inherit;margin-right:8px}}
h2.t-blue{{color:var(--blue)}}h2.t-gold{{color:var(--gold)}}h2.t-purple{{color:var(--purple)}}h2.t-green{{color:var(--green)}}
</style></head><body><div class="wrap">

<div class="panel"><div class="panel-head"><h2 class="t-blue">市场速览<span class="date">（{REPORT_DATE}）</span></h2></div>
<div class="overview">{overview_html}</div>
</div>

<div class="panel"><div class="panel-head"><h2 class="t-gold">全市场龙虎榜 · {len(lhb)} 只<span class="date">（{lhb_date}）</span></h2><span style="display:flex;align-items:center"><button class="ai-btn" id="ai-link">AI分析</button></span></div>
<div id="ai-box" style="display:none;background:#3a3210;border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:14px;font-size:13px;line-height:1.6"></div>
<script type="application/json" id="ai-data">__AI_DATA__</script>
<div style="overflow-x:auto"><table>
<thead><tr>
<th class="sortable" data-type="num">序号</th>
<th class="sortable" data-type="num">代码</th>
<th class="sortable" data-type="str">名称</th>
<th class="sortable" data-type="num">涨跌幅</th>
<th class="sortable" data-type="num">净买额</th>
<th class="sortable" data-type="num">换手</th>
<th class="sortable" data-type="str">板块</th>
<th class="sortable th-zy" data-type="zy">紫阳东路</th>
</tr></thead>
<tbody>{lhb_rows}</tbody></table></div>
<div class="more"><button id="btn-more" onclick="var hs=document.querySelectorAll('tr.hidden');for(var i=0;i<hs.length;i++)hs[i].classList.remove('hidden');this.parentNode.style.display='none'">展开全部（{max(len(lhb)-10, 0)} 只）</button></div>
</div>

<div class="panel"><div class="panel-head"><h2 class="t-purple">板块汇总 · {len(board_stat)} 个板块<span class="date">（{REPORT_DATE}）</span></h2></div>
<div style="overflow-x:auto"><table>
<thead><tr>
<th class="sortable" data-type="num">序号</th><th class="sortable" data-type="str">板块</th><th class="sortable" data-type="num">上榜家数</th><th class="sortable" data-type="num">净买额合计</th><th class="sortable th-zy" data-type="zy">紫阳东路</th><th>个股</th>
</tr></thead>
<tbody>{board_rows}</tbody></table></div>
<div class="more"><button id="btn-bmore" onclick="var hs=this.closest('.panel').querySelectorAll('tr.hidden');for(var i=0;i<hs.length;i++)hs[i].classList.remove('hidden');this.parentNode.style.display='none'">展开全部（{max(len(board_stat)-10, 0)} 个）</button></div>
</div>

<div class="panel"><div class="panel-head"><h2 class="t-green">连板梯队 · {len(market_stats.get("ladder", []))} 档<span class="date">（{REPORT_DATE}）</span></h2></div>
<div style="overflow-x:auto"><table>
<thead><tr>
<th class="sortable" data-type="num">梯队</th><th class="sortable" data-type="num">家数</th><th>个股</th>
</tr></thead>
<tbody>{ladder_rows}</tbody></table></div>
{ladder_more}</div>

<script>
(function(){{
  // 排序: 泛化到页面所有带 sortable 表头的表格
  function key(tr, col, type) {{
    var td = tr.children[col];
    var t = td.textContent.trim();
    if (type === 'num') {{
      var neg = t.indexOf('+') === 0 ? 1 : (t.indexOf('-') === 0 ? -1 : 1);
      var m = t.replace(/[+%,-]/g, '');
      var v = parseFloat(m);
      if (isNaN(v)) v = -Infinity;
      if (t.indexOf('亿') >= 0) v *= 10000;
      return v * neg;
    }}
    if (type === 'zy') {{
      // 紫阳东路列: 主表按买卖金额合计, 板块表按家数, 有值排前面
      var b = td.querySelector('.zy-buy'), s = td.querySelector('.zy-sell');
      if (b || s) {{
        function w(el) {{
          if (!el) return 0;
          var x = parseFloat(el.textContent.replace(/[^0-9.]/g, ''));
          if (isNaN(x)) return 0;
          return el.textContent.indexOf('亿') >= 0 ? x * 10000 : x;
        }}
        return (b ? w(b) : 0) + (s ? w(s) : 0);
      }}
      var n = parseFloat(t);
      return isNaN(n) ? -Infinity : n;
    }}
    return t;
  }}
  document.querySelectorAll('table').forEach(function (tbl) {{
    var tbody = tbl.querySelector('tbody');
    var ths = tbl.querySelectorAll('th.sortable');
    if (!ths.length) return;
    var curCol = -1, curDesc = true;
    ths.forEach(function (th, col) {{
      th.onclick = function () {{
        var type = th.dataset.type;
        if (col === curCol) curDesc = !curDesc; else {{ curCol = col; curDesc = true; }}
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        rows.sort(function (a, b) {{
          var ka = key(a, col, type), kb = key(b, col, type);
          var c;
          if (typeof ka === 'number' && typeof kb === 'number') c = ka - kb;
          else c = String(ka).localeCompare(String(kb), 'zh');
          return curDesc ? -c : c;
        }});
        rows.forEach(function (tr) {{ tbody.appendChild(tr); }});
        ths.forEach(function (t) {{
          var a = t.querySelector('.arr');
          if (a) a.remove();
        }});
        var arr = document.createElement('span');
        arr.className = 'arr';
        arr.textContent = curDesc ? '▼' : '▲';
        th.appendChild(arr);
      }};
    }});
  }});
  // 点击复制: 主表股票名称 + 板块表个股名称(仅 .cp-name, 板块名不复制)
  document.querySelectorAll('table tbody').forEach(function (tbody) {{
    tbody.addEventListener('click', function (e) {{
      var el = e.target.closest('.cp-name, td strong');
      if (!el || el.classList.contains('bd-name') || el.classList.contains('lbc')) return;
      var name = el.textContent.trim();
      function done() {{
        var old = el.textContent;
        el.textContent = '已复制';
        el.classList.add('copied');
        setTimeout(function () {{ el.textContent = old; el.classList.remove('copied'); }}, 800);
      }}
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(name).then(done);
      }} else {{
        var ta = document.createElement('textarea');
        ta.value = name;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        done();
      }}
    }});
  }});
  // AI 分析: 展示 Python 端预生成的结果（Markdown 渲染 + 打字机效果），无浏览器请求
  function mdToHtml(md) {{
    var lines = md.replace(/\\r/g, '').split('\\n');
    var out = [], inCode = false, listStack = [];
    function closeLists() {{ while (listStack.length) out.push('</' + listStack.pop() + '>'); }}
    function inline(s) {{
      s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
      s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/(^|[^*])\*([^*\\n]+)\*/g, '$1<em>$2</em>');
      return s;
    }}
    for (var i = 0; i < lines.length; i++) {{
      var line = lines[i];
      if (/^```/.test(line.trim())) {{  // 代码块
        if (inCode) {{ out.push('</code></pre>'); inCode = false; }}
        else {{ closeLists(); out.push('<pre><code>'); inCode = true; }}
        continue;
      }}
      if (inCode) {{ out.push(line); continue; }}
      if (/^\s*$/.test(line)) {{ closeLists(); continue; }}
      if (/^(-{{3,}}|\*{{3,}})$/.test(line.trim())) {{ closeLists(); out.push('<hr>'); continue; }}
      var h = line.match(/^(#{{1,6}})\s+(.*)$/);
      if (h) {{
        closeLists();
        var lv = h[1].length;
        out.push('<h' + lv + '>' + inline(h[2]) + '</h' + lv + '>');
        continue;
      }}
      var tq = line.match(/^>\s?(.*)$/);
      if (tq) {{
        closeLists();
        if (!out.length || out[out.length - 1].indexOf('<blockquote') < 0 || out[out.length-1].indexOf('</blockquote>') >= 0)
          out.push('<blockquote class="md-quote"><p>' + inline(tq[1]) + '</p>');
        else out[out.length - 1] = out[out.length - 1].replace(/<\/p>$/, '') + '<br>' + inline(tq[1]) + '</p>';
        continue;
      }}
      var tb = line.match(/^\|(.+)\|$/);
      if (tb && i + 1 < lines.length && /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {{
        closeLists();
        var heads = tb[1].split('|').map(function (x) {{ return x.trim(); }});
        var rows = [], j = i + 2;
        while (j < lines.length && /^\|(.+)\|$/.test(lines[j].trim())) {{
          rows.push(lines[j].trim().slice(1, -1).split('|').map(function (x) {{ return x.trim(); }}));
          j++;
        }}
        i = j - 1;
        var t = '<table class="md-table"><thead><tr>';
        heads.forEach(function (c) {{ t += '<th>' + inline(c) + '</th>'; }});
        t += '</tr></thead><tbody>';
        rows.forEach(function (r) {{
          t += '<tr>';
          r.forEach(function (c) {{ t += '<td>' + inline(c) + '</td>'; }});
          t += '</tr>';
        }});
        t += '</tbody></table>';
        out.push(t);
        continue;
      }}
      var ul = line.match(/^(\s*)[-*+]\s+(.*)$/);
      var ol = line.match(/^(\s*)\d+[.、]\s+(.*)$/);
      if (ul || ol) {{
        var tag = ul ? 'ul' : 'ol', indent = (ul || ol)[1].length;
        var want = Math.min(Math.floor(indent / 2) + 1, 3);
        while (listStack.length < want) {{ out.push('<' + tag + ' class="md-list">'); listStack.push(tag); }}
        while (listStack.length > want) {{ out.push('</' + listStack.pop() + '>'); }}
        out.push('<li>' + inline((ul || ol)[2]) + '</li>');
        continue;
      }}
      closeLists();
      out.push('<p class="md-p">' + inline(line) + '</p>');
    }}
    closeLists();
    if (inCode) out.push('</code></pre>');
    return out.join('\\n');
  }}
  document.getElementById('ai-link').onclick = function (e) {{
    e.preventDefault();
    var box = document.getElementById('ai-box');
    var dataEl = document.getElementById('ai-data');
    var text = null;
    try {{ text = JSON.parse(dataEl.textContent); }} catch (err) {{}}
    box.style.display = 'block';
    if (!text) {{
      box.innerHTML = '<span style="color:var(--muted)">AI 分析未生成（生成报告时接口失败或无数据），请重新运行脚本。</span>';
      return;
    }}
    var full = mdToHtml(text);
    if (box.dataset.done) {{ box.innerHTML = full; return; }}
    box.dataset.done = '1';
    box.innerHTML = '';
    // 打字机: 按段落渐进渲染, 避免半截 HTML 标签
    var i = 0, step = 3;
    (function type() {{
      if (i >= text.length) {{ box.innerHTML = full; return; }}
      i += step;
      box.innerHTML = mdToHtml(text.slice(0, i));
      setTimeout(type, 16);
    }})();
  }};
}})();
</script>
</div></body></html>"""

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lhb.html")
ai_thread.join()  # 等待后台 AI 分析完成（与数据拉取已并行）
# ai_text 必须在 join 后再注入: f-string 求值早于 join 会拿到 None
html = html.replace("__AI_DATA__", json.dumps(ai_text, ensure_ascii=False) if ai_text else "null")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"[3] HTML 已生成: {out}  ({os.path.getsize(out)} bytes)")
