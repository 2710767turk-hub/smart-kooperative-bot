import asyncio
import os
import requests

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")


# ---------- КЛАВИАТУРЫ ----------

def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Запросить курс", callback_data="get_rates")
    return kb.as_markup()


def back_to_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Возврат в главное меню", callback_data="back_to_menu")
    return kb.as_markup()


# ---------- API ----------

def get_currency_rates():
    """
    Возвращает:
    1 USD = X RUB
    1 EUR = Y RUB
    1 KZT = Z RUB
    Источник: openexchangerates.org
    """
    api_key = os.getenv("OPENEXCHANGE_API_KEY")
    if not api_key:
        raise ValueError("OPENEXCHANGE_API_KEY не найден в .env")

    url = "https://openexchangerates.org/api/latest.json"
    params = {
        "app_id": api_key,
        "symbols": "RUB,EUR,KZT"
    }

    response = requests.get(url, params=params, timeout=10)
    data = response.json()

    # защита от ошибок API
    if "rates" not in data:
        raise ValueError(f"Некорректный ответ OpenExchangeRates: {data}")

    usd_to_rub = data["rates"]["RUB"]
    usd_to_eur = data["rates"]["EUR"]
    usd_to_kzt = data["rates"]["KZT"]

    # считаем EUR → RUB через USD
    eur_to_rub = usd_to_rub / usd_to_eur
    # считаем KZT → RUB через USD
    kzt_to_rub = usd_to_rub / usd_to_kzt

    return usd_to_rub, eur_to_rub, kzt_to_rub


# ---------- ХЕНДЛЕРЫ ----------

async def start_handler(message: Message):
    await message.answer(
        "Привет. Здесь вы можете получить актуальный курс валюты USD, EUR и KZT",
        reply_markup=main_menu_kb()
    )


async def get_rates_handler(callback: CallbackQuery):
    usd_to_rub, eur_to_rub, kzt_to_rub = get_currency_rates()

    text = (
        "📈 Актуальный курс:\n\n"
        f"1 USD = {usd_to_rub:.2f} RUB\n"
        f"1 EUR = {eur_to_rub:.2f} RUB\n"
        f"1 KZT = {kzt_to_rub:.4f} RUB"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_menu_kb()
    )
    await callback.answer()


async def back_to_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "Привет. Здесь вы можете получить актуальный курс валюты USD, EUR и KZT",
        reply_markup=main_menu_kb()
    )
    await callback.answer()


# ---------- ЗАПУСК ----------

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден в .env")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start_handler, CommandStart())
    dp.callback_query.register(get_rates_handler, F.data == "get_rates")
    dp.callback_query.register(back_to_menu_handler, F.data == "back_to_menu")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
