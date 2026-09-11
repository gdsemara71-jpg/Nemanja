#!/usr/bin/env python3
"""
Soul WL Bot — Trade Journal & Win/Loss Tracker
WIN  = +50% atau lebih
LOSS = -60% atau lebih (minus)
OPEN = belum ditutup
"""

import os, sqlite3, logging, re
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── CONFIG ──────────────────────────────────────────────────────────────────
TG_TOKEN     = os.getenv("WL_TG_TOKEN", "")
DB_PATH      = os.getenv("DB_PATH", "wl_trades.db")
WIN_THRESHOLD  = 50.0   # % profit = WIN
LOSS_THRESHOLD = -60.0  # % loss  = LOSS

# ── CONVERSATION STATES ─────────────────────────────────────────────────────
(
    WAIT_CA, WAIT_TICKER, WAIT_ENTRY_MC, WAIT_ENTRY_SOL,
    WAIT_CLOSE_PNL, WAIT_CLOSE_SOL_OUT, WAIT_NOTE
) = range(7)

# ── DATABASE ────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                ca          TEXT,
                ticker      TEXT NOT NULL,
                entry_mc    TEXT,
                entry_sol   REAL,
                exit_sol    REAL,
                pnl_pct     REAL,
                outcome     TEXT DEFAULT 'open',
                note        TEXT,
                opened_at   TEXT NOT NULL,
                closed_at   TEXT
            )
        """)
        db.commit()
    log.info("DB initialized")

# ── UTILS ───────────────────────────────────────────────────────────────────
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

def determine_outcome(pnl_pct: float) -> str:
    if pnl_pct >= WIN_THRESHOLD:  return "win"
    if pnl_pct <= LOSS_THRESHOLD: return "loss"
    return "open"

def outcome_emoji(outcome: str) -> str:
    return {"win": "✅", "loss": "❌", "open": "⏳"}.get(outcome, "❓")

def fmt_pnl(pnl):
    if pnl is None: return "—"
    return f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"

def fmt_sol(sol):
    if sol is None: return "—"
    return f"{sol:.3f} SOL"

def calc_pnl(sol_in, sol_out):
    if not sol_in or sol_in == 0: return None
    return (sol_out - sol_in) / sol_in * 100

def shorten_ca(ca):
    if not ca or len(ca) < 12: return ca or "—"
    return f"{ca[:6]}...{ca[-4:]}"

# ── STATS CALCULATOR ─────────────────────────────────────────────────────────
def get_stats(user_id: int) -> dict:
    with get_db() as db:
        rows = db.execute(
            "SELECT outcome, pnl_pct, entry_sol, exit_sol FROM trades WHERE user_id=?",
            (user_id,)
        ).fetchall()

    total = len(rows)
    wins  = [r for r in rows if r["outcome"] == "win"]
    losses= [r for r in rows if r["outcome"] == "loss"]
    opens = [r for r in rows if r["outcome"] == "open"]
    closed= wins + losses

    wr = len(wins) / len(closed) * 100 if closed else 0

    # SOL PnL
    total_in  = sum(r["entry_sol"] for r in rows if r["entry_sol"])
    total_out = sum(r["exit_sol"]  for r in rows if r["exit_sol"])
    net_sol   = total_out - total_in if (total_in and total_out) else None

    avg_win  = sum(r["pnl_pct"] for r in wins   if r["pnl_pct"]) / len(wins)   if wins   else None
    avg_loss = sum(r["pnl_pct"] for r in losses  if r["pnl_pct"]) / len(losses) if losses else None

    # Best & worst
    closed_pnl = [r["pnl_pct"] for r in closed if r["pnl_pct"] is not None]
    best  = max(closed_pnl)  if closed_pnl else None
    worst = min(closed_pnl)  if closed_pnl else None

    return {
        "total": total, "wins": len(wins), "losses": len(losses), "opens": len(opens),
        "closed": len(closed), "wr": wr,
        "net_sol": net_sol, "total_in": total_in, "total_out": total_out,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "best": best, "worst": worst,
    }

def stats_msg(user_id: int) -> str:
    s = get_stats(user_id)
    if s["total"] == 0:
        return "📭 Belum ada trade tercatat.\nGunakan /trade untuk mulai."

    win_bar_filled = round(s["wr"] / 10)
    win_bar = "🟩" * win_bar_filled + "⬜" * (10 - win_bar_filled)

    net_txt = ""
    if s["net_sol"] is not None:
        sign = "+" if s["net_sol"] >= 0 else ""
        net_txt = f"\n💰 Net SOL: *{sign}{s['net_sol']:.3f} SOL*"
        if s["total_in"]:
            net_txt += f"\n   In: {s['total_in']:.3f} → Out: {s['total_out']:.3f}"

    lines = [
        "📊 *TRADING STATS*",
        "",
        f"{win_bar}",
        f"Win Rate: *{s['wr']:.1f}%*  ({s['wins']}W / {s['losses']}L)",
        "",
        f"✅ Win:   *{s['wins']}*   (threshold ≥+{WIN_THRESHOLD:.0f}%)",
        f"❌ Loss:  *{s['losses']}*   (threshold ≤{LOSS_THRESHOLD:.0f}%)",
        f"⏳ Open:  *{s['opens']}*",
        f"📈 Total: *{s['total']}*",
        net_txt,
        "",
    ]

    if s["avg_win"] is not None:
        lines.append(f"📈 Avg Win:  *+{s['avg_win']:.1f}%*")
    if s["avg_loss"] is not None:
        lines.append(f"📉 Avg Loss: *{s['avg_loss']:.1f}%*")
    if s["best"] is not None:
        lines.append(f"🏆 Best:  *{fmt_pnl(s['best'])}*")
    if s["worst"] is not None:
        lines.append(f"💀 Worst: *{fmt_pnl(s['worst'])}*")

    lines += [
        "",
        f"WIN  ≥ +{WIN_THRESHOLD:.0f}%  ·  LOSS ≤ {LOSS_THRESHOLD:.0f}%",
    ]
    return "\n".join(l for l in lines if l is not None)

def journal_msg(user_id: int, page: int = 0, filter_outcome: str = "all") -> tuple:
    """Returns (text, has_more)"""
    PER_PAGE = 8
    with get_db() as db:
        if filter_outcome == "all":
            rows = db.execute(
                "SELECT * FROM trades WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, PER_PAGE + 1, page * PER_PAGE)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM trades WHERE user_id=? AND outcome=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, filter_outcome, PER_PAGE + 1, page * PER_PAGE)
            ).fetchall()

    has_more = len(rows) > PER_PAGE
    rows = rows[:PER_PAGE]

    if not rows:
        return "📭 Belum ada trade.", False

    filter_label = {"all": "Semua", "win": "WIN ✅", "loss": "LOSS ❌", "open": "OPEN ⏳"}.get(filter_outcome, filter_outcome)
    lines = [f"📒 *JOURNAL* — {filter_label} (hal {page+1})", ""]

    for r in rows:
        emoji = outcome_emoji(r["outcome"])
        pnl   = fmt_pnl(r["pnl_pct"])
        sol_info = ""
        if r["entry_sol"]:
            sol_info = f" · {fmt_sol(r['entry_sol'])}"
            if r["exit_sol"]:
                net = r["exit_sol"] - r["entry_sol"]
                sign = "+" if net >= 0 else ""
                sol_info += f" → {sign}{net:.3f}"
        date = r["opened_at"][:10] if r["opened_at"] else "?"
        note = f"\n   _📝 {r['note'][:50]}_" if r["note"] else ""
        ca_txt = f"\n   `{shorten_ca(r['ca'])}`" if r["ca"] else ""

        lines.append(
            f"{emoji} *${r['ticker']}* — {pnl}{sol_info}"
            f"\n   {date}{ca_txt}{note}"
        )
        lines.append("")

    return "\n".join(lines).strip(), has_more

# ── KEYBOARDS ───────────────────────────────────────────────────────────────
def journal_keyboard(page: int, has_more: bool, filter_outcome: str = "all"):
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"journal:{page-1}:{filter_outcome}"))
    if has_more:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"journal:{page+1}:{filter_outcome}"))

    filters_row = [
        InlineKeyboardButton("All",  callback_data=f"journal:0:all"),
        InlineKeyboardButton("✅ Win",  callback_data=f"journal:0:win"),
        InlineKeyboardButton("❌ Loss", callback_data=f"journal:0:loss"),
        InlineKeyboardButton("⏳ Open", callback_data=f"journal:0:open"),
    ]

    rows = [filters_row]
    if nav: rows.append(nav)
    return InlineKeyboardMarkup(rows)

def open_trades_keyboard(user_id: int):
    with get_db() as db:
        rows = db.execute(
            "SELECT id, ticker FROM trades WHERE user_id=? AND outcome='open' ORDER BY id DESC LIMIT 10",
            (user_id,)
        ).fetchall()
    if not rows:
        return None, []
    buttons = [[InlineKeyboardButton(f"${r['ticker']} (#{r['id']})", callback_data=f"close:{r['id']}")] for r in rows]
    buttons.append([InlineKeyboardButton("❌ Batal", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons), rows

# ── CONVERSATION: ADD TRADE ──────────────────────────────────────────────────
async def cmd_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "📝 *Catat Trade Baru*\n\n"
        "Kirim *Contract Address* token (atau ketik `-` untuk skip):",
        parse_mode="Markdown"
    )
    return WAIT_CA

async def got_ca(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["ca"] = None if text == "-" else text
    await update.message.reply_text("Ticker / nama token? (contoh: `GUSIC`)", parse_mode="Markdown")
    return WAIT_TICKER

async def got_ticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = update.message.text.strip().upper().lstrip("$")
    ctx.user_data["ticker"] = ticker
    await update.message.reply_text(
        f"Entry MC untuk *${ticker}*?\n(contoh: `$120K` atau `120000` atau `-` untuk skip)",
        parse_mode="Markdown"
    )
    return WAIT_ENTRY_MC

async def got_entry_mc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["entry_mc"] = None if text == "-" else text
    await update.message.reply_text(
        "Berapa SOL yang dimasukkan?\n(contoh: `0.5` atau `-` untuk skip)",
        parse_mode="Markdown"
    )
    return WAIT_ENTRY_SOL

async def got_entry_sol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    sol = None
    if text != "-":
        try: sol = float(text.replace(",", "."))
        except: pass
    ctx.user_data["entry_sol"] = sol
    await update.message.reply_text(
        "Catatan trade? (opsional, ketik `-` untuk skip)\n"
        "Contoh: _CTO, bundle sudah exit, entry early_",
        parse_mode="Markdown"
    )
    return WAIT_NOTE

async def got_note_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["note"] = None if text == "-" else text

    uid    = update.effective_user.id
    ticker = ctx.user_data.get("ticker", "?")
    ca     = ctx.user_data.get("ca")
    mc     = ctx.user_data.get("entry_mc")
    sol    = ctx.user_data.get("entry_sol")
    note   = ctx.user_data.get("note")

    with get_db() as db:
        db.execute(
            "INSERT INTO trades (user_id,ca,ticker,entry_mc,entry_sol,outcome,note,opened_at) VALUES (?,?,?,?,?,?,?,?)",
            (uid, ca, ticker, mc, sol, "open", note, now_iso())
        )
        db.commit()

    sol_txt = f"\n💰 Entry: *{fmt_sol(sol)}*" if sol else ""
    mc_txt  = f"\n📍 Entry MC: *{mc}*" if mc else ""
    note_txt= f"\n📝 _{note}_" if note else ""

    await update.message.reply_text(
        f"✅ Trade *${ticker}* dicatat sebagai *OPEN*{mc_txt}{sol_txt}{note_txt}\n\n"
        f"Gunakan /close untuk menutup trade dan catat hasil.",
        parse_mode="Markdown"
    )
    ctx.user_data.clear()
    return ConversationHandler.END

# ── CONVERSATION: CLOSE TRADE ────────────────────────────────────────────────
async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb, rows = open_trades_keyboard(uid)
    if not rows:
        await update.message.reply_text("📭 Tidak ada trade OPEN.\nGunakan /trade untuk mencatat trade baru.")
        return ConversationHandler.END

    await update.message.reply_text(
        "🔒 *Tutup trade mana?*",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    return WAIT_CLOSE_PNL

async def close_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        await query.edit_message_text("❌ Dibatalkan.")
        return ConversationHandler.END

    trade_id = int(data.split(":")[1])
    ctx.user_data["close_id"] = trade_id

    with get_db() as db:
        trade = db.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()

    if not trade:
        await query.edit_message_text("❌ Trade tidak ditemukan.")
        return ConversationHandler.END

    ctx.user_data["close_ticker"] = trade["ticker"]
    ctx.user_data["close_entry_sol"] = trade["entry_sol"]

    await query.edit_message_text(
        f"🔒 Menutup *${trade['ticker']}* (#{trade_id})\n\n"
        f"Berapa % PnL-nya?\n"
        f"Contoh: `+150` atau `-40` atau `250`\n\n"
        f"_(WIN ≥ +{WIN_THRESHOLD:.0f}%  ·  LOSS ≤ {LOSS_THRESHOLD:.0f}%)_",
        parse_mode="Markdown"
    )
    return WAIT_CLOSE_PNL

async def got_close_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace("%", "").replace("+", "")
    try:
        pnl = float(text.replace(",", "."))
    except:
        await update.message.reply_text("❌ Format salah. Kirim angka, contoh: `150` atau `-40`", parse_mode="Markdown")
        return WAIT_CLOSE_PNL

    ctx.user_data["close_pnl"] = pnl
    outcome = determine_outcome(pnl)
    ctx.user_data["close_outcome"] = outcome

    entry_sol = ctx.user_data.get("close_entry_sol")
    if entry_sol:
        sol_out_est = entry_sol * (1 + pnl / 100)
        await update.message.reply_text(
            f"PnL: *{fmt_pnl(pnl)}* → *{outcome_emoji(outcome)} {outcome.upper()}*\n\n"
            f"Berapa SOL yang keluar?\n"
            f"_(estimasi: {sol_out_est:.3f} SOL, atau ketik angka lain, atau `-` skip)_",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"PnL: *{fmt_pnl(pnl)}* → *{outcome_emoji(outcome)} {outcome.upper()}*\n\n"
            f"Berapa SOL yang keluar? (atau `-` untuk skip)",
            parse_mode="Markdown"
        )
    return WAIT_CLOSE_SOL_OUT

async def got_close_sol_out(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    sol_out = None
    if text != "-":
        try: sol_out = float(text.replace(",", "."))
        except: pass

    pnl     = ctx.user_data["close_pnl"]
    outcome = ctx.user_data["close_outcome"]
    tid     = ctx.user_data["close_id"]
    ticker  = ctx.user_data["close_ticker"]
    entry_sol = ctx.user_data.get("close_entry_sol")

    # If sol_out not given but we have entry_sol + pnl, estimate
    if sol_out is None and entry_sol:
        sol_out = entry_sol * (1 + pnl / 100)

    with get_db() as db:
        db.execute(
            "UPDATE trades SET pnl_pct=?, exit_sol=?, outcome=?, closed_at=? WHERE id=?",
            (pnl, sol_out, outcome, now_iso(), tid)
        )
        db.commit()

    # Calculate net
    net_txt = ""
    if entry_sol and sol_out:
        net = sol_out - entry_sol
        sign = "+" if net >= 0 else ""
        net_txt = f"\n💰 {fmt_sol(entry_sol)} → {fmt_sol(sol_out)} (*{sign}{net:.3f} SOL*)"

    emoji = outcome_emoji(outcome)
    await update.message.reply_text(
        f"{emoji} *${ticker}* ditutup sebagai *{outcome.upper()}*\n"
        f"PnL: *{fmt_pnl(pnl)}*{net_txt}\n\n"
        f"/stats untuk lihat statistik terbaru",
        parse_mode="Markdown"
    )
    ctx.user_data.clear()
    return ConversationHandler.END

async def cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Dibatalkan.")
    return ConversationHandler.END

# ── STATS & JOURNAL COMMANDS ─────────────────────────────────────────────────
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(stats_msg(uid), parse_mode="Markdown")

async def cmd_journal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text, has_more = journal_msg(uid, 0, "all")
    kb = journal_keyboard(0, has_more, "all")
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def journal_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, page_str, filter_outcome = query.data.split(":")
    page = int(page_str)
    uid  = query.from_user.id
    text, has_more = journal_msg(uid, page, filter_outcome)
    kb = journal_keyboard(page, has_more, filter_outcome)
    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

async def cmd_delete_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with get_db() as db:
        row = db.execute(
            "SELECT id, ticker FROM trades WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
        if not row:
            await update.message.reply_text("📭 Tidak ada trade untuk dihapus.")
            return
        db.execute("DELETE FROM trades WHERE id=?", (row["id"],))
        db.commit()
    await update.message.reply_text(f"🗑 Trade *${row['ticker']}* (#{row['id']}) dihapus.", parse_mode="Markdown")

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ya, hapus semua", callback_data="reset_confirm"),
        InlineKeyboardButton("❌ Batal", callback_data="cancel"),
    ]])
    await update.message.reply_text(
        "⚠️ *Hapus SEMUA data trade?* Tidak bisa dikembalikan!",
        reply_markup=kb, parse_mode="Markdown"
    )

async def reset_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "reset_confirm":
        uid = query.from_user.id
        with get_db() as db:
            db.execute("DELETE FROM trades WHERE user_id=?", (uid,))
            db.commit()
        await query.edit_message_text("🗑 Semua data trade dihapus.")
    else:
        await query.edit_message_text("❌ Dibatalkan.")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 *Soul WL Bot*\n\n"
        "Track semua trade kamu dengan Win/Loss otomatis.\n\n"
        f"✅ WIN  = PnL ≥ *+{WIN_THRESHOLD:.0f}%*\n"
        f"❌ LOSS = PnL ≤ *{LOSS_THRESHOLD:.0f}%*\n"
        f"⏳ OPEN = belum ditutup\n\n"
        "*Commands:*\n"
        "/trade — catat trade baru\n"
        "/close — tutup trade & catat hasil\n"
        "/stats — lihat win rate & statistik\n"
        "/journal — riwayat semua trade\n"
        "/del — hapus trade terakhir\n"
        "/reset — hapus semua data\n"
        "/help — bantuan",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*📖 Cara Pakai Soul WL Bot*\n\n"
        "*1. Catat trade baru:*\n"
        "  /trade → ikuti langkah-langkah\n\n"
        "*2. Tutup trade:*\n"
        "  /close → pilih trade → masukkan % PnL\n\n"
        "*3. Lihat statistik:*\n"
        "  /stats → win rate, avg win/loss, net SOL\n\n"
        "*4. Riwayat trade:*\n"
        "  /journal → filter WIN/LOSS/OPEN\n\n"
        f"*Threshold:*\n"
        f"  WIN  ≥ +{WIN_THRESHOLD:.0f}%\n"
        f"  LOSS ≤ {LOSS_THRESHOLD:.0f}%\n"
        f"  OPEN = di antara keduanya",
        parse_mode="Markdown"
    )

# ── MAIN ────────────────────────────────────────────────────────────────────
def main():
    if not TG_TOKEN:
        raise ValueError("WL_TG_TOKEN belum diset!")

    init_db()
    app = ApplicationBuilder().token(TG_TOKEN).build()

    # Trade conversation
    trade_conv = ConversationHandler(
        entry_points=[CommandHandler("trade", cmd_trade)],
        states={
            WAIT_CA:        [MessageHandler(filters.TEXT & ~filters.COMMAND, got_ca)],
            WAIT_TICKER:    [MessageHandler(filters.TEXT & ~filters.COMMAND, got_ticker)],
            WAIT_ENTRY_MC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_entry_mc)],
            WAIT_ENTRY_SOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_entry_sol)],
            WAIT_NOTE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, got_note_open)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )

    # Close conversation
    close_conv = ConversationHandler(
        entry_points=[CommandHandler("close", cmd_close)],
        states={
            WAIT_CLOSE_PNL: [
                CallbackQueryHandler(close_selected, pattern=r"^(close:\d+|cancel)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_close_pnl),
            ],
            WAIT_CLOSE_SOL_OUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_close_sol_out),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("journal",cmd_journal))
    app.add_handler(CommandHandler("del",    cmd_delete_last))
    app.add_handler(CommandHandler("reset",  cmd_reset))
    app.add_handler(trade_conv)
    app.add_handler(close_conv)
    app.add_handler(CallbackQueryHandler(journal_page,  pattern=r"^journal:"))
    app.add_handler(CallbackQueryHandler(reset_confirm, pattern=r"^(reset_confirm|cancel)$"))

    log.info("🚀 Soul WL Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
