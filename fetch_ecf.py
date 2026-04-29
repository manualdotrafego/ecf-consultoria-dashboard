#!/usr/bin/env python3
"""
ECF Consultoria — Dashboard Data Fetcher
Conta 001: act_310500857276337
Busca últimos 7 dias (hoje inclusive) → docs/data.json

Anti-duplicidade:
  msgs      → apenas onsite_conversion.messaging_first_reply
  leads     → apenas lead
  purchases → apenas purchase
"""

import os, json, re, time, requests
import html as html_mod
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

TOKEN      = os.getenv("META_ACCESS_TOKEN")
ACCOUNT_ID = "310500857276337"
BASE       = "https://graph.facebook.com/v21.0"
DAYS       = 7


# ── API helper ──────────────────────────────────────────────────────────
def get(path, params=None):
    p = {"access_token": TOKEN, **(params or {})}
    r = requests.get(f"{BASE}{path}", params=p, timeout=30)
    return r.json()


# ── Anti-duplication action parser ──────────────────────────────────────
def parse_actions(row):
    msgs = leads = purchases = add_cart = initiate = lpv = link_clicks = 0
    for a in row.get("actions", []):
        t, v = a["action_type"], int(float(a.get("value", 0)))
        if t == "onsite_conversion.messaging_first_reply": msgs      += v
        if t == "lead":                                    leads     += v
        if t == "purchase":                                purchases += v
        if t == "add_to_cart":                             add_cart  += v
        if t == "initiate_checkout":                       initiate  += v
        if t == "landing_page_view":                       lpv       += v
        if t == "link_click":                              link_clicks += v

    cp_msg = cpl = cpv = 0
    for c in row.get("cost_per_action_type", []):
        ct, cv = c["action_type"], float(c.get("value", 0))
        if ct == "onsite_conversion.messaging_first_reply": cp_msg = cv
        if ct == "lead":                                     cpl    = cv
        if ct == "purchase":                                 cpv    = cv

    return dict(msgs=msgs, leads=leads, purchases=purchases,
                add_to_cart=add_cart, initiate_checkout=initiate,
                lpv=lpv, link_clicks=link_clicks,
                cp_msg=round(cp_msg, 2), cpl=round(cpl, 2), cpv=round(cpv, 2))


# ── Aggregate list of daily rows ────────────────────────────────────────
def aggregate(rows):
    sp   = round(sum(r.get("spend", 0)             for r in rows), 2)
    msgs = sum(r.get("msgs", 0)                    for r in rows)
    lds  = sum(r.get("leads", 0)                   for r in rows)
    pur  = sum(r.get("purchases", 0)               for r in rows)
    impr = sum(r.get("impressions", 0)             for r in rows)
    clk  = sum(r.get("link_clicks", 0)             for r in rows)
    atc  = sum(r.get("add_to_cart", 0)             for r in rows)
    ini  = sum(r.get("initiate_checkout", 0)       for r in rows)
    lpv  = sum(r.get("lpv", 0)                     for r in rows)
    return {
        "spend": sp, "impressions": impr, "msgs": msgs, "leads": lds,
        "purchases": pur, "add_to_cart": atc, "initiate_checkout": ini,
        "lpv": lpv, "link_clicks": clk,
        "cp_msg": round(sp / msgs, 2)     if msgs > 0 else 0,
        "cpl":    round(sp / lds,  2)     if lds  > 0 else 0,
        "cpv":    round(sp / pur,  2)     if pur  > 0 else 0,
        "cpm":    round(sp / impr * 1000, 2) if impr > 0 else 0,
        "ctr":    round(clk / impr * 100, 2) if impr > 0 else 0,
        "lp_conv": round(lds / clk * 100, 2) if clk > 0 else 0,
    }


# ── Fetch insights (aggregate) ──────────────────────────────────────────
def camp_insights_agg(camp_id, since, until):
    row = get(f"/{camp_id}/insights", {
        "fields": "spend,impressions,inline_link_clicks,ctr,cpm,actions,cost_per_action_type",
        "time_range": json.dumps({"since": since, "until": until}),
    }).get("data", [{}])
    if not row: return {}
    r = row[0]
    m = parse_actions(r)
    m.update({
        "spend":       round(float(r.get("spend", 0)), 2),
        "impressions": int(r.get("impressions", 0)),
        "link_clicks": int(float(r.get("inline_link_clicks", 0))),
        "cpm":         round(float(r.get("cpm", 0)), 2),
        "ctr":         round(float(r.get("ctr", 0)), 2),
    })
    if m["leads"] > 0:  m["lp_conv"] = round(m["leads"] / max(m["link_clicks"], 1) * 100, 2)
    else:               m["lp_conv"] = 0
    return m


def camp_insights_daily(camp_id, since, until):
    rows = get(f"/{camp_id}/insights", {
        "fields": "spend,impressions,inline_link_clicks,ctr,cpm,actions,cost_per_action_type",
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": 1,
    }).get("data", [])
    result = []
    for r in rows:
        if float(r.get("spend", 0)) < 0.01: continue
        m = parse_actions(r)
        m.update({
            "date":        r.get("date_start"),
            "spend":       round(float(r.get("spend", 0)), 2),
            "impressions": int(r.get("impressions", 0)),
            "link_clicks": int(float(r.get("inline_link_clicks", 0))),
            "cpm":         round(float(r.get("cpm", 0)), 2),
            "ctr":         round(float(r.get("ctr", 0)), 2),
        })
        result.append(m)
    return result


# ── Main fetch ──────────────────────────────────────────────────────────
def fetch():
    today = date.today()
    since = (today - timedelta(days=DAYS - 1)).strftime("%Y-%m-%d")
    until = today.strftime("%Y-%m-%d")

    print(f"ECF Consultoria | {since} → {until}")

    # 1. Active campaigns
    resp  = get(f"/act_{ACCOUNT_ID}/campaigns", {
        "fields": "id,name,objective,effective_status",
        "effective_status": json.dumps(["ACTIVE"]),
        "limit": 200,
    })
    camps = resp.get("data", [])
    print(f"  Campanhas ativas: {len(camps)}")
    camp_map = {c["id"]: c for c in camps}

    # 2. Per-campaign insights (aggregate + daily)
    print("  Buscando insights por campanha...")
    camps_out = []
    for c in camps:
        agg  = camp_insights_agg(c["id"], since, until)
        daly = camp_insights_daily(c["id"], since, until)
        if agg.get("spend", 0) <= 0: continue
        camps_out.append({
            "id":        c["id"],
            "name":      c["name"],
            "objective": c["objective"],
            "status":    c["effective_status"],
            "daily":     daly,
            "summary":   agg,
            **agg,
        })
    camps_out.sort(key=lambda x: -x.get("spend", 0))

    # 3. Account-level daily (all active camps combined)
    print("  Buscando diário da conta...")
    acct_daily_raw = get(f"/act_{ACCOUNT_ID}/insights", {
        "fields": "spend,impressions,inline_link_clicks,ctr,cpm,actions,cost_per_action_type",
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": 1,
    }).get("data", [])

    daily_by_date = {}
    for r in acct_daily_raw:
        if float(r.get("spend", 0)) < 0.01: continue
        m = parse_actions(r)
        m.update({
            "date":        r.get("date_start"),
            "spend":       round(float(r.get("spend", 0)), 2),
            "impressions": int(r.get("impressions", 0)),
            "link_clicks": int(float(r.get("inline_link_clicks", 0))),
            "cpm":         round(float(r.get("cpm", 0)), 2),
            "ctr":         round(float(r.get("ctr", 0)), 2),
        })
        daily_by_date[m["date"]] = m

    empty_day = lambda d: {"date": d, "spend": 0, "impressions": 0, "msgs": 0,
                           "leads": 0, "purchases": 0, "cpl": 0, "cp_msg": 0,
                           "cpv": 0, "cpm": 0, "ctr": 0, "link_clicks": 0,
                           "add_to_cart": 0, "initiate_checkout": 0, "lpv": 0}
    all_days = [daily_by_date.get(
        (today - timedelta(days=DAYS - 1 - i)).strftime("%Y-%m-%d"),
        empty_day((today - timedelta(days=DAYS - 1 - i)).strftime("%Y-%m-%d"))
    ) for i in range(DAYS)]

    summary = aggregate(all_days)

    # 4. Active ads
    print("  Buscando anúncios ativos...")
    ads_raw = get(f"/act_{ACCOUNT_ID}/ads", {
        "fields": "id,name,campaign_id,adset_id,adset_name,effective_status,creative{thumbnail_url}",
        "effective_status": json.dumps(["ACTIVE"]),
        "limit": 300,
    }).get("data", [])
    print(f"  Ads ativos: {len(ads_raw)}")

    # 5. Ad-level insights
    ad_data = get(f"/act_{ACCOUNT_ID}/insights", {
        "fields": "ad_id,spend,impressions,inline_link_clicks,ctr,cpm,actions,cost_per_action_type",
        "time_range": json.dumps({"since": since, "until": until}),
        "level": "ad",
        "filtering": json.dumps([{"field": "ad.effective_status", "operator": "IN", "value": ["ACTIVE"]}]),
        "limit": 300,
    }).get("data", [])

    ad_insights = {}
    for r in ad_data:
        aid = r.get("ad_id")
        m = parse_actions(r)
        m.update({
            "spend":       round(float(r.get("spend", 0)), 2),
            "impressions": int(r.get("impressions", 0)),
            "link_clicks": int(float(r.get("inline_link_clicks", 0))),
            "cpm":         round(float(r.get("cpm", 0)), 2),
            "ctr":         round(float(r.get("ctr", 0)), 2),
        })
        ad_insights[aid] = m

    # 6. Preview URLs — always refresh (URLs expire)
    print(f"  Buscando previews ({len(ads_raw)} ads)...")
    preview_map = {}
    for ad in ads_raw:
        r = get(f"/{ad['id']}/previews", {"ad_format": "MOBILE_FEED_STANDARD"})
        for p in r.get("data", []):
            m = re.search(r'src="([^"]+)"', p.get("body", ""))
            if m:
                preview_map[ad["id"]] = html_mod.unescape(m.group(1))
                break
        time.sleep(0.08)

    # 7. Build ads output
    ads_out = []
    for ad in ads_raw:
        m = ad_insights.get(ad["id"], {})
        if m.get("spend", 0) <= 0: continue
        camp = camp_map.get(ad.get("campaign_id", ""), {})
        ads_out.append({
            "id":            ad["id"],
            "name":          ad["name"],
            "campaign_id":   ad.get("campaign_id", ""),
            "campaign_name": camp.get("name", ""),
            "adset":         ad.get("adset_name", ""),
            "thumbnail":     ad.get("creative", {}).get("thumbnail_url", ""),
            "preview_url":   preview_map.get(ad["id"], ""),
            "active":        True,
            **m,
        })
    ads_out.sort(key=lambda x: -x.get("spend", 0))

    return {
        "last_updated": today.strftime("%Y-%m-%dT") + f"{__import__('datetime').datetime.utcnow().strftime('%H:%M:%S')}Z",
        "account": {"id": f"act_{ACCOUNT_ID}", "name": "ECF Consultoria", "currency": "BRL"},
        "date_range": {"since": since, "until": until},
        "summary":   summary,
        "daily":     all_days,
        "campaigns": camps_out,
        "ads":       ads_out,
    }


if __name__ == "__main__":
    data = fetch()
    out = Path("docs/data.json")
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    s = data["summary"]
    print(f"\n✅ Salvo: {out}")
    print(f"   Campanhas: {len(data['campaigns'])} | Ads: {len(data['ads'])}")
    print(f"   Gasto 7d: R${s['spend']:,.2f} | Leads: {s['leads']} | Msgs: {s['msgs']} | Compras: {s['purchases']}")
    print(f"   Período: {data['date_range']['since']} → {data['date_range']['until']}")
