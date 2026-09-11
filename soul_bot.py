#!/usr/bin/env python3
"""
Soul Analyst Bot — Telegram
Kirim CA Solana → analisis lengkap seperti Soul Scanner
Data: GMGN + DexScreener + RugCheck + Birdeye
"""

import os, json, asyncio, re, logging
from datetime import datetime, timezone

import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

# ── CONFIG ──────────────────────────────────────────────────────────────────
TG_TOKEN       = os.getenv("TG_TOKEN", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "")
BIRDEYE_KEY    = os.getenv("BIRDEYE_KEY", "")
ALLOWED_USERS  = os.getenv("ALLOWED_USERS", "")

ALLOWED = set(int(x) for x in ALLOWED_USERS.split(",") if x.strip()) if ALLOWED_USERS else set()
CA_RE   = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
}

# ── UTILS ───────────────────────────────────────────────────────────────────
def fmtn(n, prefix="$"):
    if n is None: return "?"
    n = float(n)
    if n >= 1_000_000: return f"{prefix}{n/1_000_000:.2f}M"
    if n >= 1_000:     return f"{prefix}{n/1_000:.1f}K"
    return f"{prefix}{n:.2f}"

def fpct(n):
    if n is None: return "?"
    n = float(n)
    return f"+{n:.1f}%" if n >= 0 else f"{n:.1f}%"

def age_str(ts_ms):
    if not ts_ms: return "?"
    try:
        created = datetime.fromtimestamp(int(ts_ms)/1000, tz=timezone.utc)
        diff = datetime.now(tz=timezone.utc) - created
        h = int(diff.total_seconds() // 3600)
        if h < 24: return f"{h}h"
        return f"{h//24}d {h%24}h"
    except:
        return "?"

# ── FETCHERS ────────────────────────────────────────────────────────────────
async def gmgn_security(ca: str, client: httpx.AsyncClient) -> dict:
    """Bundle, sniper, dev info dari GMGN"""
    urls = [
        f"https://gmgn.ai/api/v1/token_security/sol/{ca}",
        f"https://gmgn.ai/api/v1/rug_check/sol/{ca}",
    ]
    for url in urls:
        try:
            r = await client.get(url, headers=HEADERS_BROWSER, timeout=12)
            if r.status_code == 200:
                d = r.json()
                data = d.get("data") or d
                if data and isinstance(data, dict):
                    log.info(f"GMGN security OK: {list(data.keys())[:8]}")
                    return data
        except Exception as e:
            log.warning(f"GMGN security {url}: {e}")
    return {}

async def gmgn_token_info(ca: str, client: httpx.AsyncClient) -> dict:
    """Token info, holder count, top traders dari GMGN"""
    urls = [
        f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{ca}",
        f"https://gmgn.ai/api/v1/token_info/sol/{ca}",
    ]
    for url in urls:
        try:
            r = await client.get(url, headers=HEADERS_BROWSER, timeout=12)
            if r.status_code == 200:
                d = r.json()
                data = d.get("data") or d.get("token") or d
                if data and isinstance(data, dict):
                    log.info(f"GMGN token OK: {list(data.keys())[:8]}")
                    return data
        except Exception as e:
            log.warning(f"GMGN token {url}: {e}")
    return {}

async def gmgn_top_traders(ca: str, client: httpx.AsyncClient) -> list:
    """Smart money / top traders dari GMGN"""
    try:
        url = f"https://gmgn.ai/defi/quotation/v1/trades/sol/{ca}?limit=20&orderby=realized_profit&direction=desc"
        r = await client.get(url, headers=HEADERS_BROWSER, timeout=10)
        if r.status_code == 200:
            d = r.json()
            return d.get("data", {}).get("history") or d.get("data") or []
    except Exception as e:
        log.warning(f"GMGN traders: {e}")
    return []

async def dexscreener(ca: str, client: httpx.AsyncClient) -> dict:
    """Price, MC, liquidity, volume, chart link"""
    try:
        r = await client.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{ca}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        pairs = (r.json().get("pairs") or [])
        if not pairs: return {}
        p = sorted(pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd", 0) or 0), reverse=True)[0]
        return {
            "name":    p.get("baseToken", {}).get("name"),
            "ticker":  p.get("baseToken", {}).get("symbol"),
            "mc":      float(p.get("fdv") or p.get("marketCap") or 0),
            "price":   float(p.get("priceUsd") or 0),
            "liq":     float((p.get("liquidity") or {}).get("usd", 0) or 0),
            "vol_h1":  float((p.get("volume") or {}).get("h1", 0) or 0),
            "vol_h6":  float((p.get("volume") or {}).get("h6", 0) or 0),
            "vol_h24": float((p.get("volume") or {}).get("h24", 0) or 0),
            "chg_h1":  float((p.get("priceChange") or {}).get("h1", 0) or 0),
            "chg_h6":  float((p.get("priceChange") or {}).get("h6", 0) or 0),
            "chg_h24": float((p.get("priceChange") or {}).get("h24", 0) or 0),
            "buys_h1": int(((p.get("txns") or {}).get("h1") or {}).get("buys", 0)),
            "sells_h1":int(((p.get("txns") or {}).get("h1") or {}).get("sells", 0)),
            "created": p.get("pairCreatedAt"),
            "dex_url": p.get("url"),
            "dex_id":  p.get("dexId"),
        }
    except Exception as e:
        log.warning(f"DexScreener: {e}")
        return {}

async def rugcheck(ca: str, client: httpx.AsyncClient) -> dict:
    """RugCheck score dan risk list"""
    try:
        r = await client.get(
            f"https://api.rugcheck.xyz/v1/tokens/{ca}/report/summary",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            return {
                "score":   d.get("score"),
                "risks":   [x.get("name") for x in (d.get("risks") or [])],
                "verdict": d.get("score_normalised"),
            }
    except Exception as e:
        log.warning(f"RugCheck: {e}")
    return {}

async def birdeye_overview(ca: str, client: httpx.AsyncClient) -> dict:
    """Holder data dari Birdeye (butuh API key)"""
    if not BIRDEYE_KEY: return {}
    try:
        r = await client.get(
            f"https://public-api.birdeye.so/defi/token_overview?address={ca}",
            headers={"X-API-KEY": BIRDEYE_KEY, "x-chain": "solana"}, timeout=10
        )
        d = (r.json().get("data") or {})
        return {
            "holders":    d.get("holder"),
            "top10_pct":  d.get("top10HolderPercent"),
            "unique_24h": d.get("uniqueWallet24h"),
        }
    except Exception as e:
        log.warning(f"Birdeye: {e}")
    return {}

# ── PARSE GMGN DATA ─────────────────────────────────────────────────────────
def parse_gmgn(sec: dict, info: dict) -> dict:
    """Extract Soul Scanner-like fields dari GMGN response"""
    result = {}

    # From security endpoint
    if sec:
        result["is_honeypot"]     = sec.get("is_honeypot") or sec.get("honeypot_with_same_creator")
        result["is_mintable"]     = sec.get("mintable") or sec.get("is_mintable")
        result["is_blacklist"]    = sec.get("transfer_pausable") or sec.get("can_blacklist")
        result["creator_addr"]    = sec.get("creator_address") or sec.get("creator")
        result["creator_bal"]     = sec.get("creator_balance")
        result["creator_pct"]     = sec.get("creator_percent")
        result["dev_sold"]        = sec.get("creator_token_status") == "sold_out"
        result["top10_pct"]       = sec.get("top10_holder_rate") or sec.get("top_holder_rate")
        result["bundle_pct"]      = sec.get("bundled_supply") or sec.get("bundle_supply")
        result["sniper_pct"]      = sec.get("sniper_supply") or sec.get("snipers_supply")
        result["fake_holder_pct"] = sec.get("fake_token") or sec.get("fake_holder_percent")
        result["holders"]         = sec.get("holder_count") or sec.get("holders")
        result["lp_burned"]       = sec.get("lp_burned") or sec.get("burn_ratio")
        result["is_open_source"]  = sec.get("open_source")

    # From token info endpoint
    if info:
        result["holders"]      = result.get("holders") or info.get("holder_count") or info.get("holders")
        result["twitter"]      = info.get("twitter") or info.get("social", {}).get("twitter") if isinstance(info.get("social"), dict) else None
        result["telegram"]     = info.get("telegram") or info.get("social", {}).get("telegram") if isinstance(info.get("social"), dict) else None
        result["website"]      = info.get("website") or info.get("social", {}).get("website") if isinstance(info.get("social"), dict) else None
        result["is_cto"]       = info.get("is_cto") or info.get("cto")
        result["total_supply"] = info.get("total_supply")
        result["launchpad"]    = info.get("launchpad") or info.get("platform")

    return result

# ── SCORING ─────────────────────────────────────────────────────────────────
def compute_score(dex: dict, gm: dict, rug: dict, bird: dict) -> dict:
    green, red, neutral = [], [], []
    pts = 50

    # --- Price momentum ---
    chg1 = dex.get("chg_h1") or 0
    if chg1 > 30:    green.append(f"Pump *+{chg1:.0f}%* dalam 1h 🚀"); pts += 10
    elif chg1 > 0:   green.append(f"Harga naik *+{chg1:.1f}%* dalam 1h"); pts += 5
    elif chg1 > -20: neutral.append(f"Harga {fpct(chg1)} dalam 1h")
    else:            red.append(f"Harga dump *{chg1:.1f}%* dalam 1h"); pts -= 8

    # --- Liquidity ---
    liq = dex.get("liq") or 0
    mc  = dex.get("mc") or 1
    lr  = liq / mc * 100
    if lr >= 25:   green.append(f"Liquidity sehat *{lr:.1f}%* ({fmtn(liq)})"); pts += 8
    elif lr >= 15: neutral.append(f"Liquidity {lr:.1f}% ({fmtn(liq)})")
    else:          red.append(f"Liquidity tipis *{lr:.1f}%* — slippage tinggi"); pts -= 10

    # --- Volume ---
    vol1 = dex.get("vol_h1") or 0
    vr   = vol1 / mc * 100 if mc else 0
    buys  = dex.get("buys_h1") or 0
    sells = dex.get("sells_h1") or 0
    if vr > 50:   green.append(f"Volume/MC *{vr:.0f}%* — sangat aktif ({buys}B/{sells}S)"); pts += 8
    elif vr > 15: neutral.append(f"Volume 1h {fmtn(vol1)} — normal ({buys}B/{sells}S)")
    else:         red.append(f"Volume rendah *{vr:.1f}%* dari MC"); pts -= 5

    # --- Bundle ---
    bundle = gm.get("bundle_pct")
    if bundle is not None:
        b = float(bundle) * 100 if float(bundle) <= 1 else float(bundle)
        if b <= 1:    green.append(f"Bundle *{b:.1f}%* — hampir nol, aman"); pts += 10
        elif b <= 10: neutral.append(f"Bundle *{b:.1f}%* — masih dalam batas")
        else:         red.append(f"Bundle masih *{b:.1f}%* — risiko dump!"); pts -= 15

    # --- Sniper ---
    sniper = gm.get("sniper_pct")
    if sniper is not None:
        s = float(sniper) * 100 if float(sniper) <= 1 else float(sniper)
        if s <= 2:    green.append(f"Sniper *{s:.1f}%* — sudah exit"); pts += 8
        elif s <= 10: neutral.append(f"Sniper sisa *{s:.1f}%* — pantau terus")
        else:         red.append(f"Sniper *{s:.1f}%* — potensi dump besar"); pts -= 12

    # --- Dev wallet ---
    dev_sold = gm.get("dev_sold")
    dev_pct  = gm.get("creator_pct")
    if dev_sold:
        neutral.append("Dev sudah *sold out* — CTO mode")
    elif dev_pct is not None:
        dp = float(dev_pct) * 100 if float(dev_pct) <= 1 else float(dev_pct)
        if dp == 0:   green.append("Dev wallet *0%* — tidak ada risiko dump dev"); pts += 5
        elif dp <= 5: neutral.append(f"Dev masih pegang *{dp:.1f}%*")
        else:         red.append(f"Dev masih pegang *{dp:.1f}%* — waspada dump"); pts -= 8

    # --- Fake holders ---
    fh = gm.get("fake_holder_pct")
    if fh is not None:
        f = float(fh) * 100 if float(fh) <= 1 else float(fh)
        if f <= 10:   green.append(f"Fake holders *{f:.1f}%* — distribusi genuine"); pts += 6
        elif f <= 18: neutral.append(f"Fake holders *{f:.1f}%* — agak tinggi")
        else:         red.append(f"Fake holders *{f:.1f}%* — banyak wallet bot!"); pts -= 10

    # --- Top 10 concentration ---
    top10 = gm.get("top10_pct") or bird.get("top10_pct")
    if top10 is not None:
        t = float(top10) * 100 if float(top10) <= 1 else float(top10)
        if t < 25:   green.append(f"Top 10 holders *{t:.1f}%* — merata"); pts += 5
        elif t < 40: neutral.append(f"Top 10 holders *{t:.1f}%*")
        else:        red.append(f"Top 10 holders *{t:.1f}%* — sangat terkonsentrasi"); pts -= 8

    # --- Honeypot ---
    if gm.get("is_honeypot"):
        red.append("⚠️ *HONEYPOT DETECTED* — tidak bisa dijual!"); pts -= 40

    # --- Mintable ---
    if gm.get("is_mintable"):
        red.append("Token *mintable* — supply bisa ditambah kapanpun"); pts -= 10

    # --- RugCheck ---
    rscore = rug.get("score")
    risks  = rug.get("risks") or []
    if rscore is not None:
        if rscore <= 300:   green.append(f"RugCheck *{rscore}* — relatif aman"); pts += 8
        elif rscore <= 600: neutral.append(f"RugCheck score *{rscore}* — risiko sedang")
        else:               red.append(f"RugCheck *{rscore}* — risiko tinggi!"); pts -= 12
    if risks:
        red.append("Risks: _" + ", ".join(risks[:5]) + "_")
        pts -= min(len(risks) * 2, 10)

    # --- Holders ---
    holders = gm.get("holders") or bird.get("holders")
    if holders:
        h = int(holders)
        if h > 1000:  green.append(f"*{h:,} holders* — distribusi luas"); pts += 5
        elif h > 300: neutral.append(f"*{h:,} holders*")
        else:         red.append(f"Hanya *{h:,} holders* — sangat awal"); pts -= 3

    # --- Social ---
    has_tw  = bool(gm.get("twitter"))
    has_tg  = bool(gm.get("telegram"))
    has_web = bool(gm.get("website"))
    sc = sum([has_tw, has_tg, has_web])
    if sc >= 3:   green.append("Social lengkap *X · TG · Website*"); pts += 4
    elif sc == 2: neutral.append(f"Social {sc}/3 platform")
    elif sc == 1: neutral.append(f"Social hanya {sc}/3 platform")
    else:         red.append("Tidak ada social media terdeteksi"); pts -= 4

    # --- LP Burned ---
    lp = gm.get("lp_burned")
    if lp:
        lp_val = float(lp) * 100 if float(lp) <= 1 else float(lp)
        if lp_val > 80: green.append(f"LP burned *{lp_val:.0f}%* — liquidity terkunci"); pts += 6
        else:           neutral.append(f"LP burned {lp_val:.0f}%")

    # Final
    pts = max(0, min(100, pts))
    if pts >= 72:   verdict, vc = "✅ BUY", "buy"
    elif pts >= 56: verdict, vc = "👀 WATCH", "watch"
    elif pts >= 40: verdict, vc = "🎲 HIGH RISK GAMBLE", "gamble"
    else:           verdict, vc = "❌ AVOID", "avoid"

    # Price targets
    mc_val = dex.get("mc")
    entry = tp1 = tp2 = sl = None
    if mc_val and mc_val > 0:
        entry = fmtn(mc_val, "$")
        tp1   = fmtn(mc_val * (1.5 if dex.get("chg_h1", 0) < 50 else 2.0), "$")
        tp2   = fmtn(mc_val * 3.0, "$")
        sl    = fmtn(mc_val * 0.7, "$")

    return {
        "green": green, "red": red, "neutral": neutral,
        "pts": pts, "verdict": verdict, "vc": vc,
        "entry": entry, "tp1": tp1, "tp2": tp2, "sl": sl
    }

# ── AI ANALYSIS ─────────────────────────────────────────────────────────────
async def ai_analysis(ca: str, dex: dict, gm: dict, rug: dict, sc: dict, client: httpx.AsyncClient) -> str:
    if not OPENROUTER_KEY: return ""
    bundle = gm.get("bundle_pct")
    sniper = gm.get("sniper_pct")
    b_str  = f"{float(bundle)*100 if bundle and float(bundle)<=1 else bundle or 0:.1f}%"
    s_str  = f"{float(sniper)*100 if sniper and float(sniper)<=1 else sniper or 0:.1f}%"

    prompt = f"""Kamu adalah Pro Analyst Crypto Solana spesialis pump.fun dan PumpSwap.

Data token ${dex.get('ticker')} ({dex.get('name')}):
• CA: {ca}
• MC: {fmtn(dex.get('mc'))} | Liquidity: {fmtn(dex.get('liq'))}
• Volume 1h: {fmtn(dex.get('vol_h1'))} | 24h: {fmtn(dex.get('vol_h24'))}
• Price 1h: {fpct(dex.get('chg_h1'))} | 24h: {fpct(dex.get('chg_h24'))}
• Buys/Sells 1h: {dex.get('buys_h1')}/{dex.get('sells_h1')}
• Age: {age_str(dex.get('created'))}
• Bundle: {b_str} | Sniper: {s_str}
• Fake holders: {gm.get('fake_holder_pct') or 'N/A'}
• Dev sold: {gm.get('dev_sold')} | Dev %: {gm.get('creator_pct') or 'N/A'}
• Holders: {gm.get('holders') or 'N/A'}
• RugCheck: {rug.get('score')} | Risks: {', '.join(rug.get('risks') or []) or 'none'}
• Honeypot: {gm.get('is_honeypot')} | Mintable: {gm.get('is_mintable')}
• Social: Twitter={bool(gm.get('twitter'))} TG={bool(gm.get('telegram'))} Web={bool(gm.get('website'))}
• Local Score: {sc['pts']}/100 → {sc['verdict']}
• Green: {'; '.join(sc['green'][:3])}
• Red: {'; '.join(sc['red'][:3])}

Tulis analisis SINGKAT (maks 180 kata) dalam format Telegram:

*📊 ANALISIS*
[2-3 kalimat insight utama]

*🎯 LEVEL*
Entry MC: {sc.get('entry') or '?'}
TP1: {sc.get('tp1') or '?'} | TP2: {sc.get('tp2') or '?'}
Stop Loss: {sc.get('sl') or '?'}

*⚠️ WATCH OUT*
[1-2 risiko terbesar yang harus diperhatikan]

Gunakan Bahasa Indonesia. Bold untuk angka penting. Tajam dan actionable."""

    try:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://soul-analyst.app",
                "X-Title": "Soul Analyst Bot"
            },
            json={
                "model": "google/gemini-flash-1.5",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "temperature": 0.35
            },
            timeout=20
        )
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning(f"AI: {e}")
        return ""

# ── BUILD MESSAGE ────────────────────────────────────────────────────────────
def build_msg(ca: str, dex: dict, gm: dict, rug: dict, bird: dict, sc: dict, ai: str) -> str:
    name   = dex.get("name") or "Unknown"
    ticker = dex.get("ticker") or "?"
    age    = age_str(dex.get("created"))
    chg1   = dex.get("chg_h1") or 0

    # Score bar
    filled = round(sc["pts"] / 10)
    bar    = "▓" * filled + "░" * (10 - filled)

    # Launchpad badge
    lp_badge = ""
    launch = gm.get("launchpad") or ""
    if "pump" in str(launch).lower(): lp_badge = "🟢 pump.fun"
    elif launch: lp_badge = launch

    lines = [
        f"*{name}* `${ticker}` {lp_badge}",
        f"`{ca[:8]}...{ca[-6:]}`  ·  Age: *{age}*",
        "",
        f"💰 MC: *{fmtn(dex.get('mc'))}*  🔝 ATH: _{fmtn(dex.get('mc'))}_",
        f"💧 Liq: *{fmtn(dex.get('liq'))}*  ·  {dex.get('dex_id','').upper() or 'DEX'}",
        f"📊 Vol 1h: *{fmtn(dex.get('vol_h1'))}*  ·  24h: *{fmtn(dex.get('vol_h24'))}*",
        f"📈 1h: *{fpct(chg1)}*  ·  6h: *{fpct(dex.get('chg_h6'))}*  ·  24h: *{fpct(dex.get('chg_h24'))}*",
        f"🔄 Txn 1h: *{dex.get('buys_h1',0)}B / {dex.get('sells_h1',0)}S*",
        "",
    ]

    # GMGN data block
    bundle = gm.get("bundle_pct")
    sniper = gm.get("sniper_pct")
    fh     = gm.get("fake_holder_pct")
    holders = gm.get("holders") or bird.get("holders")
    top10  = gm.get("top10_pct")
    dev_pct = gm.get("creator_pct")
    dev_sold = gm.get("dev_sold")
    lp_burn = gm.get("lp_burned")

    def to_pct_str(v):
        if v is None: return "N/A"
        f = float(v)
        f = f * 100 if f <= 1 else f
        return f"{f:.1f}%"

    gmgn_lines = []
    if bundle is not None:   gmgn_lines.append(f"📦 Bundle: *{to_pct_str(bundle)}*")
    if sniper is not None:   gmgn_lines.append(f"🔫 Sniper: *{to_pct_str(sniper)}*")
    if fh is not None:       gmgn_lines.append(f"👻 Fake holders: *{to_pct_str(fh)}*")
    if holders:              gmgn_lines.append(f"👥 Holders: *{int(holders):,}*")
    if top10 is not None:    gmgn_lines.append(f"🐳 Top 10: *{to_pct_str(top10)}*")
    if dev_sold:             gmgn_lines.append(f"🛠️ Dev: *Sold out* (CTO)")
    elif dev_pct is not None:gmgn_lines.append(f"🛠️ Dev: *{to_pct_str(dev_pct)}*")
    if lp_burn is not None:  gmgn_lines.append(f"🔥 LP Burned: *{to_pct_str(lp_burn)}*")
    if gm.get("is_honeypot"):gmgn_lines.append(f"🚨 *HONEYPOT!*")
    if gm.get("is_mintable"):gmgn_lines.append(f"⚠️ *Mintable supply*")

    # RugCheck
    rscore = rug.get("score")
    risks  = rug.get("risks") or []
    if rscore is not None:
        emoji = "✅" if rscore <= 300 else "⚠️" if rscore <= 600 else "🔴"
        gmgn_lines.append(f"🛡 RugCheck: *{rscore}* {emoji}")
    if risks:
        gmgn_lines.append(f"   └ _{', '.join(risks[:4])}_")

    if gmgn_lines:
        lines.extend(gmgn_lines)
        lines.append("")

    # Score
    lines += [
        f"*Risk Score: {sc['pts']}/100*",
        f"`{bar}`",
        "",
    ]

    # Flags
    if sc["green"]:
        lines.append("*✅ Positif*")
        lines.extend("  · " + x for x in sc["green"][:5])
        lines.append("")
    if sc["red"]:
        lines.append("*🔴 Risiko*")
        lines.extend("  · " + x for x in sc["red"][:5])
        lines.append("")
    if sc["neutral"]:
        lines.append("*🟡 Netral*")
        lines.extend("  · " + x for x in sc["neutral"][:3])
        lines.append("")

    # Verdict
    lines.append(f"*{sc['verdict']}*")
    lines.append("")

    # Price targets (from local score)
    if sc.get("entry"):
        lines += [
            f"📍 Entry: *{sc['entry']}* MC",
            f"🎯 TP1: *{sc['tp1']}*  ·  TP2: *{sc['tp2']}*",
            f"🛑 SL: *{sc['sl']}* MC",
            "",
        ]

    # AI
    if ai:
        lines += ["─" * 22, ai, ""]

    # Social
    social_links = []
    if gm.get("twitter"):  social_links.append(f"[X]({gm['twitter']})")
    if gm.get("telegram"): social_links.append(f"[TG]({gm['telegram']})")
    if gm.get("website"):  social_links.append(f"[Web]({gm['website']})")
    if dex.get("dex_url"): social_links.append(f"[Chart]({dex['dex_url']})")
    # DexScreener + RugCheck links
    social_links.append(f"[RugCheck](https://rugcheck.xyz/tokens/{ca})")
    social_links.append(f"[GMGN](https://gmgn.ai/sol/token/{ca})")
    if social_links:
        lines.append("  ".join(social_links))
        lines.append("")

    lines.append("_⚠️ Bukan financial advice. DYOR._")
    return "\n".join(lines)

# ── BOT HANDLERS ────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👻 *Soul Analyst Bot*\n\n"
        "Kirim *Contract Address* Solana langsung.\n"
        "Bot analisis otomatis: bundle · sniper · dev · fake holders · AI verdict.\n\n"
        "Data: GMGN · DexScreener · RugCheck · Birdeye\n"
        "AI: Gemini Flash via OpenRouter\n\n"
        "/help untuk info lengkap",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*📖 Cara Pakai*\n\n"
        "Kirim CA Solana langsung, contoh:\n"
        "`FZn8XbGgp3MMfKELH3k5JxzMHqL8jdMrFfEV37pcpump`\n\n"
        "*⚙️ Commands*\n"
        "/start — mulai\n"
        "/help — bantuan\n"
        "/status — cek status API keys\n"
        "/set\\_or [key] — set OpenRouter key\n"
        "/set\\_birdeye [key] — set Birdeye key (opsional)\n\n"
        "*📊 Data yang diambil*\n"
        "• GMGN: bundle, sniper, dev wallet, fake holders, LP burn\n"
        "• DexScreener: MC, liquidity, volume, price change\n"
        "• RugCheck: security score, risk flags\n"
        "• Birdeye: holder distribution (butuh API key)\n"
        "• Gemini AI: analisis & rekomendasi",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    or_ok = "✅" if OPENROUTER_KEY else "❌ Belum diset"
    be_ok = "✅" if BIRDEYE_KEY else "⚠️ Opsional, belum diset"
    await update.message.reply_text(
        f"*Status API Keys*\n\n"
        f"OpenRouter: {or_ok}\n"
        f"Birdeye: {be_ok}\n"
        f"Telegram: ✅",
        parse_mode="Markdown"
    )

async def cmd_set_or(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global OPENROUTER_KEY
    if not ctx.args:
        await update.message.reply_text("Usage: `/set_or sk-or-v1-xxxxx`", parse_mode="Markdown")
        return
    OPENROUTER_KEY = ctx.args[0].strip()
    await update.message.reply_text("✅ OpenRouter key disimpan untuk sesi ini.")

async def cmd_set_birdeye(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BIRDEYE_KEY
    if not ctx.args:
        await update.message.reply_text("Usage: `/set_birdeye YOUR_KEY`", parse_mode="Markdown")
        return
    BIRDEYE_KEY = ctx.args[0].strip()
    await update.message.reply_text("✅ Birdeye key disimpan untuk sesi ini.")

async def handle_ca(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if ALLOWED and uid not in ALLOWED:
        await update.message.reply_text("⛔ Akses tidak diizinkan.")
        return

    text = (update.message.text or "").strip()
    cas  = CA_RE.findall(text)
    if not cas:
        await update.message.reply_text(
            "❓ Kirim Contract Address Solana yang valid.\n"
            "Contoh: `FZn8XbGgp3MMfKELH3k5JxzMHqL8jdMrFfEV37pcpump`",
            parse_mode="Markdown"
        )
        return

    ca  = cas[0]
    msg = await update.message.reply_text("🔍 Scanning token...")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        await msg.edit_text("🔍 Fetching GMGN data...")
        gm_sec, gm_info, dex_data, rug_data, bird_data = await asyncio.gather(
            gmgn_security(ca, client),
            gmgn_token_info(ca, client),
            dexscreener(ca, client),
            rugcheck(ca, client),
            birdeye_overview(ca, client),
        )

        if not dex_data and not gm_sec:
            await msg.edit_text(
                "❌ Token tidak ditemukan.\n"
                "Pastikan CA benar dan token sudah listing di DEX.",
                parse_mode="Markdown"
            )
            return

        gm = parse_gmgn(gm_sec, gm_info)
        sc = compute_score(dex_data, gm, rug_data, bird_data)

        if OPENROUTER_KEY:
            await msg.edit_text("🤖 AI menganalisis...")
            ai_text = await ai_analysis(ca, dex_data, gm, rug_data, sc, client)
        else:
            ai_text = ""

        reply = build_msg(ca, dex_data, gm, rug_data, bird_data, sc, ai_text)

        try:
            await msg.edit_text(reply, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            # Fallback: kirim tanpa markdown kalau ada karakter bermasalah
            log.warning(f"Markdown error: {e}")
            plain = reply.replace("*", "").replace("_", "").replace("`", "")
            await msg.edit_text(plain, disable_web_page_preview=True)

# ── MAIN ────────────────────────────────────────────────────────────────────
def main():
    if not TG_TOKEN:
        raise ValueError("TG_TOKEN belum diset! Set di environment variable.")
    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("set_or",      cmd_set_or))
    app.add_handler(CommandHandler("set_birdeye", cmd_set_birdeye))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca))
    log.info("🚀 Soul Analyst Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
