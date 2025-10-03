#!/usr/bin/env python3

import json
import logging
from datetime import datetime
import yfinance as yf
import pandas as pd
import feedparser
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = "8342333980:AAEIbs8ADR7yU0SNvQIuV1AUQtUaq9JPvmU"
DATA_FILE = "bot_state.json"
SCAN_INTERVAL_MIN = 15
RSI_PERIOD = 14
RSI_SELL = 70
PRICE_DROP_THRESH = 0.10
VOLUME_SPIKE_FACTOR = 3.0
E24_RSS = "https://e24.no/rss/boers-og-finans"  # E24 børs-nyheter RSS
# ---------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_STATE = {
    "tickers": ["EQNR.OL", "YAR.OL"],
    "chat_id": None,
    "last_sent": {},
    "last_news": []
}

# ---------- STATE HANDLING ----------
def load_state():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except:
        save_state(DEFAULT_STATE)
        return DEFAULT_STATE.copy()

def save_state(state):
    with open(DATA_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

state = load_state()

# ---------- HELPERS ----------
def compute_rsi(series, period=14):
    delta = series.diff().dropna()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.isna().all() else None

def fetch_ohlcv(ticker, period="60d"):
    df = yf.Ticker(ticker).history(period=period)
    return df

def analyze_ticker(ticker):
    df = fetch_ohlcv(ticker)
    if df.empty or len(df) < RSI_PERIOD+5:
        return None
    latest_close = df["Close"].iloc[-1]
    rsi = compute_rsi(df["Close"], RSI_PERIOD)
    high_30 = df["High"].iloc[-30:].max()
    drop_pct = (high_30 - latest_close)/high_30 if high_30>0 else 0
    avg_vol = df["Volume"].iloc[-30:].replace(0, pd.NA).dropna().mean()
    vol = df["Volume"].iloc[-1]
    vol_spike = avg_vol>0 and (vol/avg_vol)>=VOLUME_SPIKE_FACTOR

    reasons=[]
    if rsi and rsi>=RSI_SELL: reasons.append(f"RSI {rsi:.1f}>=70")
    if drop_pct>=PRICE_DROP_THRESH: reasons.append(f"Pris ned {drop_pct*100:.1f}% fra 30d-high")
    if vol_spike: reasons.append(f"Volumspike: {vol/1_000_000:.1f}M")

    if reasons:
        emoji="📉🚨💥⚠️🔥"
        msg=f"{emoji}\n*SELL SIGNAL* for {ticker}\n" + " | ".join(reasons) + f"\nKurs: {latest_close:.2f} NOK\nTid: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{emoji}"
        return msg
    return None

def fetch_news():
    feed = feedparser.parse(E24_RSS)
    new_articles = []
    for entry in feed.entries:
        if entry.link not in state.get("last_news", []):
            new_articles.append(entry)
            state.setdefault("last_news", []).append(entry.link)
    if new_articles:
        save_state(state)
    return new_articles

def news_messages():
    articles = fetch_news()
    msgs=[]
    for art in articles:
        msgs.append(f"📰 *NYHET* 📰\n{art.title}\n{art.link}")
    return msgs

# ---------- TELEGRAM COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text("Botten kjører! Varsler sendes hit. Bruk /add /remove /list for tickere.")

async def add_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    ticker = context.args[0].upper()
    state.setdefault("tickers", [])
    if ticker not in state["tickers"]:
        state["tickers"].append(ticker)
        save_state(state)
        await update.message.reply_text(f"Legget til {ticker} ✅")

async def remove_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    ticker = context.args[0].upper()
    if ticker in state.get("tickers", []):
        state["tickers"].remove(ticker)
        save_state(state)
        await update.message.reply_text(f"Fjernet {ticker} ✅")

async def list_tickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Overvåkede tickere:\n" + "\n".join(state.get("tickers", [])))

# ---------- SCAN JOB ----------
def scheduled_scan(app):
    chat_id = state.get("chat_id")
    if not chat_id: return
    for t in state.get("tickers", []):
        msg = analyze_ticker(t)
        if msg: app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    for nmsg in news_messages():
        app.bot.send_message(chat_id=chat_id, text=nmsg, parse_mode="Markdown")

# ---------- MAIN ----------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_ticker))
    app.add_handler(CommandHandler("remove", remove_ticker))
    app.add_handler(CommandHandler("list", list_tickers))

    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: scheduled_scan(app), "interval", minutes=SCAN_INTERVAL_MIN)
    scheduler.start()

    app.run_polling()

if __name__=="__main__":
    if TELEGRAM_TOKEN=="SETT_INN_DIN_TELEGRAM_BOT_TOKEN_HER":
        print("Sett TELEGRAM_TOKEN først")
    else:
        main()
