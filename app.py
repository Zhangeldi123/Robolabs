# app.py
import os
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from storage import init_db, upsert_lead, count_leads, Lead

# -----------------------------
# Env / config
# -----------------------------
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent

# Optional .env for local runs (platforms like Koyeb/Render use env vars)
env_path = BASE_DIR / ".env"
if env_path.exists() and env_path.stat().st_size > 0:
    load_dotenv(env_path)

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
SCHOOL_NAME = (os.getenv("SCHOOL_NAME") or "English School").strip()
TIMEZONE = (os.getenv("TIMEZONE") or "Asia/Aqtobe").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set it in environment variables (recommended) or in .env")

# -----------------------------
# UI helpers
# -----------------------------
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📌 Записаться на пробный урок")],
            [KeyboardButton(text="📚 Подобрать курс"), KeyboardButton(text="💬 Задать вопрос")],
            [KeyboardButton(text="💰 Цена и пакеты"), KeyboardButton(text="🧪 Определить уровень")],
        ],
        resize_keyboard=True,
    )

def age_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Взрослый"), KeyboardButton(text="Подросток (13–17)")],
            [KeyboardButton(text="Ребёнок (6–12)"), KeyboardButton(text="Не хочу говорить")],
        ],
        resize_keyboard=True,
    )

async def safe_reply(m: Message, text: str, **kwargs):
    text = (text or "").strip() or "Ок."
    await m.answer(text, **kwargs)

# -----------------------------
# FSM (intake form)
# -----------------------------
class Intake(StatesGroup):
    name = State()
    age_group = State()
    level = State()
    goal = State()
    schedule = State()
    contact = State()

# -----------------------------
# Bot setup
# -----------------------------
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await safe_reply(
        m,
        f"Привет! Я бот школы **{SCHOOL_NAME}** 🙂\n"
        f"Помогу выбрать курс и записаться на пробный урок.\n\n"
        f"С чего начнём?",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )

@dp.message(F.text == "📌 Записаться на пробный урок")
async def trial(m: Message, state: FSMContext):
    await state.set_state(Intake.name)
    await safe_reply(m, "Супер. Как тебя зовут?")

@dp.message(F.text == "📚 Подобрать курс")
async def pick_course(m: Message, state: FSMContext):
    await state.set_state(Intake.goal)
    await safe_reply(
        m,
        "Ок! Для чего английский?\n"
        "Например: разговорный / работа / IELTS / переезд / универ."
    )

@dp.message(F.text == "💰 Цена и пакеты")
async def pricing(m: Message, state: FSMContext | None = None):
    await safe_reply(
        m,
        "💰 Пример пакетов (замени на ваши реальные):\n"
        "• Пробный урок: 30–45 мин\n"
        "• Индивидуально: 2–3 раза в неделю\n"
        "• Группа: 6–10 человек\n\n"
        "Хочешь — подберу вариант под твою цель. Нажми «Подобрать курс»."
    )

@dp.message(F.text == "🧪 Определить уровень")
async def level_test(m: Message, state: FSMContext | None = None):
    await safe_reply(
        m,
        "Быстрый способ:\n"
        "1) Сколько лет учишь английский?\n"
        "2) Можешь ли смотреть видео без субтитров?\n"
        "3) Что сложнее: говорить или понимать?\n\n"
        "Ответь 2–3 предложениями — и я скажу примерный уровень (A1–C1)."
    )

@dp.message(F.text == "💬 Задать вопрос")
async def ask(m: Message, state: FSMContext | None = None):
    await safe_reply(m, "Напиши свой вопрос одним сообщением — отвечу 🙂")

# --- Intake flow ---
@dp.message(Intake.name, F.text)
async def intake_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(Intake.age_group)
    await safe_reply(m, "Кто будет заниматься?", reply_markup=age_menu())

@dp.message(Intake.age_group, F.text)
async def intake_age(m: Message, state: FSMContext):
    await state.update_data(age_group=m.text.strip())
    await state.set_state(Intake.level)
    await safe_reply(m, "Какой сейчас уровень? (если не знаешь — напиши «не знаю»)")

@dp.message(Intake.level, F.text)
async def intake_level(m: Message, state: FSMContext):
    await state.update_data(level=m.text.strip())
    await state.set_state(Intake.goal)
    await safe_reply(m, "Какая цель? (разговорный/IELTS/работа/переезд и т.д.)")

@dp.message(Intake.goal, F.text)
async def intake_goal(m: Message, state: FSMContext):
    await state.update_data(goal=m.text.strip())
    await state.set_state(Intake.schedule)
    await safe_reply(
        m,
        f"Когда удобно заниматься? (дни/время) + часовой пояс.\n"
        f"Если ты в Казахстане, обычно это {TIMEZONE}."
    )

@dp.message(Intake.schedule, F.text)
async def intake_schedule(m: Message, state: FSMContext):
    await state.update_data(schedule=m.text.strip())
    await state.set_state(Intake.contact)
    await safe_reply(
        m,
        "Оставь контакт для связи (ник/телефон) или напиши «без контакта».\n"
        "⚠️ Пиши только то, что готов(а) сообщить."
    )

@dp.message(Intake.contact, F.text)
async def intake_contact(m: Message, state: FSMContext):
    data = await state.get_data()
    lead = Lead(
        tg_id=m.from_user.id,
        name=data.get("name", ""),
        age_group=data.get("age_group", ""),
        level=data.get("level", ""),
        goal=data.get("goal", ""),
        schedule=data.get("schedule", ""),
        contact=m.text.strip(),
    )
    upsert_lead(lead)
    await state.clear()

    await safe_reply(
        m,
        "✅ Готово! Я записал(а) заявку.\n\n"
        "Следующий шаг: напиши 2–3 удобных слота по времени (например: вт 19:00, чт 20:00), "
        "и мы подтвердим пробный урок.\n\n"
        "Если хочешь — могу сразу предложить формат (индивидуально/группа) по твоей цели.",
        reply_markup=main_menu(),
    )

    # Notify admin (optional)
    if ADMIN_ID and ADMIN_ID != 0:
        try:
            await bot.send_message(
                ADMIN_ID,
                "📥 НОВЫЙ ЛИД:\n"
                f"tg_id: {lead.tg_id}\n"
                f"name: {lead.name}\n"
                f"age: {lead.age_group}\n"
                f"level: {lead.level}\n"
                f"goal: {lead.goal}\n"
                f"schedule: {lead.schedule}\n"
                f"contact: {lead.contact}"
            )
        except Exception:
            logging.exception("Failed to notify admin")

# --- Admin command ---
@dp.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    await safe_reply(m, f"📊 Лидов в базе: {count_leads()}")

# --- Fallback ---
@dp.message(F.text)
async def fallback(m: Message):
    text = (m.text or "").lower().strip()

    if any(k in text for k in ["ielts", "toefl"]):
        await safe_reply(m, "Если цель экзамен — ок. Скажи: какой дедлайн и текущий уровень? Тогда подберу план.")
        return
    if any(k in text for k in ["цена", "стоимость", "сколько"]):
        await pricing(m)
        return

    await safe_reply(
        m,
        "Понял(а). Чтобы точнее помочь: какая цель английского?\n"
        "1) разговорный  2) работа  3) IELTS  4) переезд  5) школа/универ"
    )

# -----------------------------
# Healthcheck web server (for platforms that expect an open port)
# -----------------------------
async def run_health_server(stop_event: asyncio.Event):
    app = web.Application()

    async def health(_request):
        return web.Response(text="ok")

    app.router.add_get("/health", health)
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logging.info("Health server listening on 0.0.0.0:%s", port)

    await stop_event.wait()

    logging.info("Shutting down health server...")
    await runner.cleanup()

# -----------------------------
# Main
# -----------------------------
async def main():
    init_db()
    logging.info("Starting bot polling...")

    stop_event = asyncio.Event()

    try:
        await asyncio.gather(
            run_health_server(stop_event),
            dp.start_polling(bot),
        )
    finally:
        stop_event.set()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
