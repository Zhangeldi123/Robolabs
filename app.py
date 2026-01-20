# app.py
import os
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from google import genai

from storage import init_db, upsert_lead, count_leads, Lead

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent

# Optional .env for local runs
env_path = BASE_DIR / ".env"
if env_path.exists() and env_path.stat().st_size > 0:
    load_dotenv(env_path)

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
SCHOOL_NAME = (os.getenv("SCHOOL_NAME") or "English School").strip()
TIMEZONE = (os.getenv("TIMEZONE") or "Asia/Aqtobe").strip()

# Gemini model: fast + cheap, good for чат-бота
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set it in environment variables or .env")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# --------- System prompt (agent instruction) ----------
def load_agent_prompt() -> str:
    p = BASE_DIR / "agent_prompt.txt"
    if p.exists() and p.stat().st_size > 0:
        return p.read_text(encoding="utf-8", errors="ignore").strip()

    # fallback prompt (если файла нет)
    return f"""
Ты — ИИ-ассистент школы английского языка "{SCHOOL_NAME}".
Твоя цель — помогать пользователю выбрать курс и записаться на пробный урок.

Правила:
- Пиши коротко, понятно, дружелюбно, без канцелярита.
- Если пользователь спрашивает про цены/пакеты — дай пример и предложи записаться на пробный.
- Если пользователь спрашивает про уровень — уточни 2–3 вопроса и оцени A1–C1.
- Если вопрос не по теме школы — мягко верни к теме и предложи помощь.
- Не проси лишние персональные данные. Для записи достаточно: имя, цель, удобное время, контакт (по желанию).
- Если пользователь хочет записаться: направь в “Записаться на пробный урок” и собери анкету.
""".strip()

AGENT_PROMPT = load_agent_prompt()

# --------- Tiny memory (per user) ----------
# хранит последние N сообщений (для контекста ИИ)
MEM: Dict[int, List[Tuple[str, str]]] = {}
MEM_MAX = 10

def mem_add(user_id: int, role: str, text: str) -> None:
    MEM.setdefault(user_id, [])
    MEM[user_id].append((role, text))
    if len(MEM[user_id]) > MEM_MAX:
        MEM[user_id] = MEM[user_id][-MEM_MAX:]

def mem_pack(user_id: int) -> str:
    items = MEM.get(user_id, [])
    out = []
    for role, txt in items:
        out.append(f"{role.upper()}: {txt}")
    return "\n".join(out).strip()

# --------- Gemini client ----------
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

async def ask_gemini(user_id: int, user_text: str) -> str:
    """
    Асинхронная обёртка (SDK синхронный -> в отдельный поток)
    """
    if not gemini_client:
        return "ИИ сейчас не подключён (нет GEMINI_API_KEY). Но я могу помочь кнопками меню 🙂"

    # Контекст: system + краткая память
    history = mem_pack(user_id)
    prompt = (
        f"SYSTEM:\n{AGENT_PROMPT}\n\n"
        f"CONTEXT (short chat history):\n{history}\n\n"
        f"USER:\n{user_text}\n"
    )

    def _call() -> str:
        resp = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return (resp.text or "").strip()

    try:
        return await asyncio.to_thread(_call)
    except Exception:
        logging.exception("Gemini call failed")
        return "Упс, сейчас не получилось ответить через ИИ. Попробуй ещё раз через минуту 🙂"

# --------- Keyboards ----------
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📌 Записаться на пробный урок")],
            [KeyboardButton(text="📚 Подобрать курс"), KeyboardButton(text="💬 Вопрос ИИ")],
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

# --------- FSM ----------
class Intake(StatesGroup):
    name = State()
    age_group = State()
    level = State()
    goal = State()
    schedule = State()
    contact = State()

class AIChat(StatesGroup):
    question = State()

# --------- Handlers ----------
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    mem_add(m.from_user.id, "user", "/start")
    await m.answer(
        f"Привет! Я ИИ-ассистент школы **{SCHOOL_NAME}** 🙂\n"
        f"Помогу выбрать курс и записаться на пробный урок.\n\n"
        f"Выбери кнопку ниже 👇",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )

@dp.message(F.text == "📌 Записаться на пробный урок")
async def trial(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.set_state(Intake.name)
    await m.answer("Супер. Как тебя зовут?")

@dp.message(F.text == "📚 Подобрать курс")
async def pick_course(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.set_state(Intake.goal)
    await m.answer("Для чего английский? (разговорный / работа / IELTS / переезд / универ)")

@dp.message(F.text == "💰 Цена и пакеты")
async def pricing(m: Message, state: FSMContext | None = None):
    mem_add(m.from_user.id, "user", m.text)
    await m.answer(
        "💰 Пример (замени на ваши реальные):\n"
        "• Пробный урок: 30–45 минут\n"
        "• Индивидуально: 2–3 раза в неделю\n"
        "• Группа: 6–10 человек\n\n"
        "Хочешь — подберу вариант под цель. Нажми «📚 Подобрать курс»."
    )

@dp.message(F.text == "🧪 Определить уровень")
async def level_test(m: Message, state: FSMContext | None = None):
    mem_add(m.from_user.id, "user", m.text)
    await m.answer(
        "Быстрая оценка уровня:\n"
        "1) Сколько лет учишь английский?\n"
        "2) Смотришь ли видео без субтитров?\n"
        "3) Что сложнее: говорить или понимать?\n\n"
        "Ответь 2–3 предложениями — и я скажу примерный уровень (A1–C1)."
    )

@dp.message(F.text == "💬 Вопрос ИИ")
async def ai_mode(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.set_state(AIChat.question)
    await m.answer("Ок! Задай вопрос про обучение/уровень/курс/IELTS — отвечу 🙂")

# ----- Intake flow -----
@dp.message(Intake.name, F.text)
async def intake_name(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.update_data(name=m.text.strip())
    await state.set_state(Intake.age_group)
    await m.answer("Кто будет заниматься?", reply_markup=age_menu())

@dp.message(Intake.age_group, F.text)
async def intake_age(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.update_data(age_group=m.text.strip())
    await state.set_state(Intake.level)
    await m.answer("Какой сейчас уровень? (если не знаешь — напиши «не знаю»)")

@dp.message(Intake.level, F.text)
async def intake_level(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.update_data(level=m.text.strip())
    await state.set_state(Intake.goal)
    await m.answer("Какая цель? (разговорный/IELTS/работа/переезд и т.д.)")

@dp.message(Intake.goal, F.text)
async def intake_goal(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.update_data(goal=m.text.strip())
    await state.set_state(Intake.schedule)
    await m.answer(
        f"Когда удобно заниматься? (дни/время) + часовой пояс.\n"
        f"Если ты в Казахстане, обычно {TIMEZONE}."
    )

@dp.message(Intake.schedule, F.text)
async def intake_schedule(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)
    await state.update_data(schedule=m.text.strip())
    await state.set_state(Intake.contact)
    await m.answer(
        "Оставь контакт для связи (ник/телефон) или напиши «без контакта».\n"
        "⚠️ Пиши только то, что готов(а) сообщить."
    )

@dp.message(Intake.contact, F.text)
async def intake_contact(m: Message, state: FSMContext):
    mem_add(m.from_user.id, "user", m.text)

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

    await m.answer(
        "✅ Готово! Заявка сохранена.\n\n"
        "Чтобы подтвердить пробный урок — напиши 2–3 удобных времени.\n"
        "Или задай вопрос через «💬 Вопрос ИИ».",
        reply_markup=main_menu(),
    )

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

# ----- AI Q&A -----
@dp.message(AIChat.question, F.text)
async def ai_answer(m: Message, state: FSMContext):
    user_text = m.text.strip()
    mem_add(m.from_user.id, "user", user_text)

    answer = await ask_gemini(m.from_user.id, user_text)

    # чуть-чуть защиты от пустого ответа
    if not answer:
        answer = "Я не смог(ла) сформулировать ответ. Спроси иначе или нажми «📚 Подобрать курс»."

    mem_add(m.from_user.id, "assistant", answer)
    await m.answer(answer, reply_markup=main_menu())

# ----- Admin -----
@dp.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    await m.answer(f"📊 Лидов в базе: {count_leads()}")

@dp.message(Command("reset_ai"))
async def reset_ai(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    MEM.clear()
    await m.answer("✅ AI memory очищена.")

# ----- Fallback: если не в анкете и не в AI-режиме, отвечаем ИИ кратко -----
@dp.message(F.text)
async def fallback(m: Message, state: FSMContext):
    # если пользователь НЕ в состоянии анкеты/AI, можно отвечать ИИ автоматически
    cur = await state.get_state()
    if cur:
        return

    user_text = (m.text or "").strip()
    mem_add(m.from_user.id, "user", user_text)

    answer = await ask_gemini(m.from_user.id, user_text)
    mem_add(m.from_user.id, "assistant", answer)
    await m.answer(answer, reply_markup=main_menu())

# --------- Health server (для Koyeb health checks) ----------
async def run_health_server(stop_event: asyncio.Event):
    app = web.Application()

    async def health(_request):
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logging.info("Health server listening on 0.0.0.0:%s", port)

    await stop_event.wait()
    await runner.cleanup()

# --------- Main ----------
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
