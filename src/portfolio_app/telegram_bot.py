from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from telegram import Update, Document
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .config import settings
from .logging_setup import setup_logging
from .db import SessionLocal
from .models import User, Upload, Job
from .ingestion import read_csv_bytes, parse_positions_snapshot

log = logging.getLogger("bot")


def _ensure_dirs() -> None:
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.REPORT_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.CACHE_DIR).mkdir(parents=True, exist_ok=True)


def _get_or_create_user(chat_id: str) -> User:
    with SessionLocal() as db:
        u = db.query(User).filter(User.telegram_chat_id == chat_id).one_or_none()
        if u:
            return u
        u = User(telegram_chat_id=chat_id)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u


def _size_ok(doc: Document) -> bool:
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    return (doc.file_size or 0) <= max_bytes


def _is_csv(doc: Document) -> bool:
    name = (doc.file_name or "").lower()
    return name.endswith(".csv")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    _get_or_create_user(chat_id)
    await update.message.reply_text(
        "Hi! Send /upload then attach your positions snapshot CSV.\n\n"
        "Required columns:\n"
        "- as_of_date, ticker, quantity, price, market_value\n\n"
        "Commands: /upload /run /report /weekly on|off /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/upload - instructions\n"
        "/run - run analytics on latest upload\n"
        "/report - get latest report\n"
        "/weekly on|off - enable/disable Monday summaries\n\n"
        "CSV required columns:\n"
        "- as_of_date, ticker, quantity, price, market_value\n"
    )


async def upload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Please send your CSV as a document attachment (file).")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return

    doc = update.message.document
    chat_id = str(update.effective_chat.id)
    user = _get_or_create_user(chat_id)

    if not _is_csv(doc):
        await update.message.reply_text("Please upload a .csv file.")
        return
    if not _size_ok(doc):
        await update.message.reply_text(f"File too large. Max {settings.MAX_UPLOAD_MB} MB.")
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    tg_file = await doc.get_file()
    data = await tg_file.download_as_bytearray()
    data_bytes = bytes(data)

    # Validate schema early
    try:
        df = read_csv_bytes(data_bytes)
        parsed = parse_positions_snapshot(df)
    except Exception as e:
        await update.message.reply_text(
            "❌ CSV schema error.\n"
            "Your file must be a positions snapshot with required columns:\n"
            "- as_of_date, ticker, quantity, price, market_value\n\n"
            f"Error: {e}"
        )
        return

    # Store
    user_dir = Path(settings.UPLOAD_DIR) / chat_id
    user_dir.mkdir(parents=True, exist_ok=True)
    stored_path = user_dir / f"{doc.file_unique_id}.csv"
    stored_path.write_bytes(data_bytes)

    with SessionLocal() as db:
        up = Upload(user_id=user.id, filename=doc.file_name or "upload.csv", stored_path=str(stored_path))
        db.add(up)
        db.commit()
        db.refresh(up)

    msg_lines = [
        f"✅ Upload saved (id={up.id}).",
        f"Holdings detected: {len(parsed.items)}",
    ]
    if parsed.as_of_date:
        msg_lines.append(f"As-of date: {parsed.as_of_date}")
    if parsed.warnings:
        msg_lines.append("")
        msg_lines.append("⚠️ Notes:")
        msg_lines.extend([f"- {w}" for w in parsed.warnings[:10]])

    msg_lines.append("")
    msg_lines.append("Run /run to compute analytics.")
    await update.message.reply_text("\n".join(msg_lines))


async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    user = _get_or_create_user(chat_id)

    with SessionLocal() as db:
        last_upload = db.query(Upload).filter(Upload.user_id == user.id).order_by(Upload.id.desc()).first()
        if not last_upload:
            await update.message.reply_text("No uploads found. Use /upload and send a CSV first.")
            return
        job = Job(user_id=user.id, upload_id=last_upload.id, kind="analysis", status="queued")
        db.add(job)
        db.commit()
        db.refresh(job)

    await update.message.reply_text(f"Queued analysis job {job.id}. Use /report shortly.")


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    user = _get_or_create_user(chat_id)

    with SessionLocal() as db:
        last_job = (
            db.query(Job)
            .filter(Job.user_id == user.id, Job.kind == "analysis")
            .order_by(Job.id.desc())
            .first()
        )

    if not last_job:
        await update.message.reply_text("No jobs yet. Run /run first.")
        return

    if last_job.status in ("queued", "running"):
        await update.message.reply_text(f"Job {last_job.id} is still {last_job.status}. Try /report again soon.")
        return

    if last_job.status == "failed":
        await update.message.reply_text(f"Job {last_job.id} failed:\n{last_job.error}")
        return

    if not last_job.result_json:
        await update.message.reply_text("Job finished but no result stored. Check worker logs.")
        return

    result = json.loads(last_job.result_json)
    msg = format_result_message(result)
    await update.message.reply_text(msg)

    if last_job.report_paths_json:
        paths = json.loads(last_job.report_paths_json)
        for p in paths:
            try:
                await update.message.reply_photo(photo=open(p, "rb"))
            except Exception:
                pass


async def weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    _get_or_create_user(chat_id)

    args = context.args if context.args else []
    if not args or args[0] not in ("on", "off"):
        await update.message.reply_text("Usage: /weekly on  OR  /weekly off")
        return
    enabled = args[0] == "on"
    with SessionLocal() as db:
        u = db.query(User).filter(User.telegram_chat_id == chat_id).one()
        u.weekly_enabled = enabled
        db.commit()
    await update.message.reply_text(f"Weekly summary {'enabled' if enabled else 'disabled'}. (Sent Mondays.)")


def format_result_message(result: dict[str, Any]) -> str:
    w = result.get("window", {})
    p = result.get("portfolio", {}).get("metrics", {})
    r = result.get("rebalance", {})
    after = r.get("metrics_after", {})
    tsla = result.get("tsla", {})
    bench = result.get("benchmarks", {})

    def fmt(x):
        if x is None:
            return "n/a"
        return f"{x:.2%}"

    def fmtf(x):
        if x is None:
            return "n/a"
        return f"{x:.2f}"

    lines = []
    asof = result.get("snapshot_as_of_date")
    if asof:
        lines.append(f"🗓 Snapshot as-of: {asof}")
        lines.append("")

    lines.append(f"📊 Trailing window: {w.get('start')} → {w.get('end')} ({w.get('days')} days)")
    hc = result.get("holdings_count")
    if hc is not None:
        lines.append(f"Holdings: {hc}")
    lines.append("")

    lines.append("Portfolio:")
    lines.append(f"- Return: {fmt(p.get('return'))}")
    lines.append(f"- Vol (ann.): {fmt(p.get('vol'))}")
    lines.append(f"- Max DD: {fmt(p.get('max_drawdown'))}")
    lines.append(f"- Sharpe (rf=2%): {fmtf(p.get('sharpe'))}")
    lines.append("")

    lines.append("Benchmarks:")
    for k, v in bench.items():
        m = v.get("metrics", {})
        lines.append(
            f"- {k}: Ret {fmt(m.get('return'))}, Vol {fmt(m.get('vol'))}, "
            f"MaxDD {fmt(m.get('max_drawdown'))}, Sharpe {fmtf(m.get('sharpe'))}"
        )
    lines.append("")

    lines.append("TSLA concentration:")
    lines.append(f"- Weight: {fmt(tsla.get('tsla_weight'))}")
    vs = tsla.get("variance_share")
    lines.append(f"- Variance share: {fmt(vs) if vs is not None else 'n/a'}")
    lines.append("")

    lines.append("Rebalance (TSLA -25% shares, pro-rata to others):")
    lines.append(f"- Return after: {fmt(after.get('return'))}")
    lines.append(f"- Vol after: {fmt(after.get('vol'))}")
    lines.append(f"- Max DD after: {fmt(after.get('max_drawdown'))}")
    lines.append(f"- Sharpe after: {fmtf(after.get('sharpe'))}")

    warns = result.get("warnings") or []
    if warns:
        lines.append("")
        lines.append("⚠️ Warnings:")
        for x in warns[:10]:
            lines.append(f"- {x}")

    return "\n".join(lines)


def main() -> None:
    setup_logging()
    _ensure_dirs()

    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("upload", upload_cmd))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("weekly", weekly_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    log.info("Starting Telegram bot (polling)…")
    app.run_polling()


if __name__ == "__main__":
    main()