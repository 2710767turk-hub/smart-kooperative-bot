import asyncio
import os
import requests

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")


# ---------- КЛАВИАТУРЫ ----------

def currency_selection_kb():
    """Клавиатура выбора валюты"""
    kb = InlineKeyboardBuilder()
    kb.button(text="Валюта USD", callback_data="currency_usd")
    kb.button(text="Валюта EUR", callback_data="currency_eur")
    kb.button(text="Валюта KZT", callback_data="currency_kzt")
    kb.adjust(1)  # По одной кнопке в ряд
    return kb.as_markup()


def back_to_menu_kb():
    """Кнопка возврата в главное меню"""
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Возврат в главное меню", callback_data="back_to_menu")
    return kb.as_markup()


# ---------- API ----------

def get_currency_rates():
    """
    Возвращает:
    1 USD = X RUB
    1 EUR = Y RUB
    1 RUB = Z KZT (обратный курс)
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
    # считаем RUB → KZT (обратный курс: сколько тенге стоит 1 рубль)
    rub_to_kzt = usd_to_kzt / usd_to_rub

    return usd_to_rub, eur_to_rub, rub_to_kzt


# ---------- ХЕНДЛЕРЫ ----------

async def start_handler(message: Message):
    # Приветственное сообщение с изображением
    photo = FSInputFile("ChatGPT Image 22 янв. 2026 г., 16_23_08.png")
    await message.answer_photo(
        photo=photo,
        caption="Здравствуйте! 👋\n\n🏦 Здесь вы можете быстро совершить обмен РУБЛИ на ДОЛЛАРЫ или ЕВРО из РФ в банк Республики Казахстан.",
        has_spoiler=False
    )
    
    # После картинки - запрос курса на сервер и показ выбора валюты
    # Происходит запрос курса валют на сервер (в фоне)
    try:
        # Запрашиваем курс заранее, чтобы он был готов
        get_currency_rates()
    except:
        pass  # Игнорируем ошибки на этом этапе
    
    # Показываем выбор валюты
    await message.answer(
        "Выберите валюту, курс которой вы хотите посмотреть",
        reply_markup=currency_selection_kb()
    )


async def currency_usd_handler(callback: CallbackQuery):
    """Обработчик для валюты USD"""
    try:
        # Запрос курса валюты с сервера
        usd_to_rub, eur_to_rub, rub_to_kzt = get_currency_rates()
        
        # Формируем сообщение согласно схеме
        text = f"На сегодня курс USD = {usd_to_rub:.2f}"
        
        await callback.message.answer(
            text,
            reply_markup=back_to_menu_kb()
        )
    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка при получении курса валют: {str(e)}",
            reply_markup=back_to_menu_kb()
        )
    finally:
        await callback.answer()


async def currency_eur_handler(callback: CallbackQuery):
    """Обработчик для валюты EUR"""
    try:
        # Запрос курса валюты с сервера
        usd_to_rub, eur_to_rub, rub_to_kzt = get_currency_rates()
        
        # Формируем сообщение согласно схеме
        text = f"На сегодня курс EUR = {eur_to_rub:.2f}"
        
        await callback.message.answer(
            text,
            reply_markup=back_to_menu_kb()
        )
    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка при получении курса валют: {str(e)}",
            reply_markup=back_to_menu_kb()
        )
    finally:
        await callback.answer()


async def currency_kzt_handler(callback: CallbackQuery):
    """Обработчик для валюты KZT"""
    try:
        # Запрос курса валюты с сервера
        usd_to_rub, eur_to_rub, rub_to_kzt = get_currency_rates()
        
        # Формируем сообщение согласно схеме
        text = f"На сегодня курс KZT = {rub_to_kzt:.2f}"
        
        await callback.message.answer(
            text,
            reply_markup=back_to_menu_kb()
        )
    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка при получении курса валют: {str(e)}",
            reply_markup=back_to_menu_kb()
        )
    finally:
        await callback.answer()


async def back_to_menu_handler(callback: CallbackQuery):
    # Возврат в главное меню - отправляем приветствие и изображение
    photo = FSInputFile("ChatGPT Image 22 янв. 2026 г., 16_23_08.png")
    await callback.message.answer_photo(
        photo=photo,
        caption="Здравствуйте! 👋\n\n🏦 Здесь вы можете быстро совершить обмен РУБЛИ на ДОЛЛАРЫ или ЕВРО из РФ в банк Республики Казахстан.",
        has_spoiler=False
    )
    
    # После картинки - запрос курса на сервер и показ выбора валюты
    try:
        # Запрашиваем курс заранее
        get_currency_rates()
    except:
        pass
    
    # Показываем выбор валюты
    await callback.message.answer(
        "Выберите валюту, курс которой вы хотите посмотреть",
        reply_markup=currency_selection_kb()
    )
    await callback.answer()


# ---------- ЗАПУСК ----------

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден в .env")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start_handler, CommandStart())
    dp.callback_query.register(currency_usd_handler, F.data == "currency_usd")
    dp.callback_query.register(currency_eur_handler, F.data == "currency_eur")
    dp.callback_query.register(currency_kzt_handler, F.data == "currency_kzt")
    dp.callback_query.register(back_to_menu_handler, F.data == "back_to_menu")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
