import asyncio
import os
import requests
from datetime import datetime, timedelta
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ---------- КЭШИРОВАНИЕ КУРСОВ ----------

class RateCache:
    """Кэш для курсов валют (20 минут)"""
    def __init__(self):
        self.cache: Optional[dict] = None
        self.cache_time: Optional[datetime] = None
        self.cache_duration = timedelta(minutes=20)
    
    def is_valid(self) -> bool:
        """Проверяет, действителен ли кэш"""
        if self.cache is None or self.cache_time is None:
            return False
        return datetime.now() - self.cache_time < self.cache_duration
    
    def get(self) -> Optional[dict]:
        """Получает данные из кэша"""
        if self.is_valid():
            return self.cache
        return None
    
    def set(self, data: dict):
        """Сохраняет данные в кэш"""
        self.cache = data
        self.cache_time = datetime.now()

rate_cache = RateCache()


# ---------- КЛАВИАТУРЫ ----------

def exchange_direction_kb():
    """Клавиатура выбора направления обмена"""
    kb = InlineKeyboardBuilder()
    kb.button(text="RUB → KZT", callback_data="direction_rub_to_kzt")
    kb.button(text="KZT → RUB", callback_data="direction_kzt_to_rub")
    kb.adjust(1)  # По одной кнопке в ряд
    return kb.as_markup()


def back_to_menu_kb():
    """Кнопка возврата в главное меню"""
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Возврат в главное меню", callback_data="back_to_menu")
    return kb.as_markup()


# ---------- СОСТОЯНИЯ FSM ----------

class ExchangeStates(StatesGroup):
    waiting_amount = State()


# ---------- API И РАСЧЕТЫ ----------

def get_market_rates() -> Tuple[float, float]:
    """
    Получает курсы RUB/USD и KZT/USD с OpenExchangeRates.org
    Возвращает: (rub_per_usd, kzt_per_usd)
    """
    # Проверяем кэш
    cached = rate_cache.get()
    if cached:
        return cached['rub_per_usd'], cached['kzt_per_usd']
    
    api_key = os.getenv("OPENEXCHANGE_API_KEY")
    if not api_key:
        raise ValueError("OPENEXCHANGE_API_KEY не найден в .env")

    url = "https://openexchangerates.org/api/latest.json"
    params = {
        "app_id": api_key,
        "symbols": "RUB,KZT"
    }

    response = requests.get(url, params=params, timeout=10)
    data = response.json()

    # Защита от ошибок API
    if "rates" not in data:
        raise ValueError(f"Некорректный ответ OpenExchangeRates: {data}")

    rub_per_usd = data["rates"]["RUB"]  # Сколько рублей за 1 USD
    kzt_per_usd = data["rates"]["KZT"]  # Сколько тенге за 1 USD

    # Сохраняем в кэш
    rate_cache.set({
        'rub_per_usd': rub_per_usd,
        'kzt_per_usd': kzt_per_usd
    })

    return rub_per_usd, kzt_per_usd


def calculate_rates() -> Tuple[float, float]:
    """
    Рассчитывает клиентские курсы обмена с учетом спреда 4%
    Возвращает: (rate_rub_to_kzt, rate_kzt_to_rub)
    """
    rub_per_usd, kzt_per_usd = get_market_rates()
    
    # Базовый рыночный курс: 1 RUB = base_rate KZT
    base_rate = kzt_per_usd / rub_per_usd
    
    # Клиентские курсы с учетом спреда -4% (клиент получает меньше)
    rate_rub_to_kzt = base_rate * 0.96  # RUB → KZT
    rate_kzt_to_rub = (1 / base_rate) * 0.96  # KZT → RUB
    
    return rate_rub_to_kzt, rate_kzt_to_rub


# ---------- ХЕНДЛЕРЫ ----------

async def start_handler(message: Message):
    """Обработчик команды /start"""
    # Приветственное сообщение с изображением
    photo = FSInputFile("ChatGPT Image 22 янв. 2026 г., 16_23_08.png")
    await message.answer_photo(
        photo=photo,
        caption="Здравствуйте! 👋\n\n🏦 Здесь вы можете быстро совершить обмен РУБЛИ на ТЕНГЕ или ТЕНГЕ на РУБЛИ.",
        has_spoiler=False
    )
    
    # Показываем выбор направления обмена
    await message.answer(
        "Выберите направление обмена:",
        reply_markup=exchange_direction_kb()
    )


async def direction_rub_to_kzt_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора направления RUB → KZT"""
    try:
        rate_rub_to_kzt, _ = calculate_rates()
        
        await callback.message.answer(
            f"💰 Курс обмена: 1 RUB = {rate_rub_to_kzt:.4f} KZT\n\n"
            "Введите сумму в рублях для обмена:",
            reply_markup=back_to_menu_kb()
        )
        
        # Сохраняем направление обмена в состояние
        await state.update_data(direction="rub_to_kzt", rate=rate_rub_to_kzt)
        await state.set_state(ExchangeStates.waiting_amount)
        
    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка при получении курса: {str(e)}",
            reply_markup=back_to_menu_kb()
        )
    finally:
        await callback.answer()


async def direction_kzt_to_rub_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора направления KZT → RUB"""
    try:
        _, rate_kzt_to_rub = calculate_rates()
        
        await callback.message.answer(
            f"💰 Курс обмена: 1 KZT = {rate_kzt_to_rub:.4f} RUB\n\n"
            "Введите сумму в тенге для обмена:",
            reply_markup=back_to_menu_kb()
        )
        
        # Сохраняем направление обмена в состояние
        await state.update_data(direction="kzt_to_rub", rate=rate_kzt_to_rub)
        await state.set_state(ExchangeStates.waiting_amount)
        
    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка при получении курса: {str(e)}",
            reply_markup=back_to_menu_kb()
        )
    finally:
        await callback.answer()


async def amount_handler(message: Message, state: FSMContext):
    """Обработчик ввода суммы"""
    try:
        # Пытаемся преобразовать введенный текст в число
        amount = float(message.text.replace(',', '.').strip())
        
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше нуля. Введите корректную сумму:")
            return
        
        # Получаем данные из состояния
        data = await state.get_data()
        direction = data.get('direction')
        rate = data.get('rate')
        
        if not direction or not rate:
            await message.answer("❌ Ошибка. Пожалуйста, начните заново с команды /start")
            await state.clear()
            return
        
        # Рассчитываем результат обмена
        if direction == "rub_to_kzt":
            result = amount * rate
            currency_from = "RUB"
            currency_to = "KZT"
        else:  # kzt_to_rub
            result = amount * rate
            currency_from = "KZT"
            currency_to = "RUB"
        
        # Формируем ответ
        text = (
            f"📊 Расчет обмена:\n\n"
            f"Отдаете: {amount:,.2f} {currency_from}\n"
            f"Получаете: {result:,.2f} {currency_to}\n\n"
            f"Курс: 1 {currency_from} = {rate:.4f} {currency_to}"
        )
        
        await message.answer(text, reply_markup=back_to_menu_kb())
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число (например: 1000 или 1000.50):")
    except Exception as e:
        await message.answer(f"❌ Ошибка при расчете: {str(e)}", reply_markup=back_to_menu_kb())
        await state.clear()


async def back_to_menu_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик возврата в главное меню"""
    await state.clear()
    
    # Возврат в главное меню - отправляем приветствие и изображение
    photo = FSInputFile("ChatGPT Image 22 янв. 2026 г., 16_23_08.png")
    await callback.message.answer_photo(
        photo=photo,
        caption="Здравствуйте! 👋\n\n🏦 Здесь вы можете быстро совершить обмен РУБЛИ на ТЕНГЕ или ТЕНГЕ на РУБЛИ.",
        has_spoiler=False
    )
    
    # Показываем выбор направления обмена
    await callback.message.answer(
        "Выберите направление обмена:",
        reply_markup=exchange_direction_kb()
    )
    await callback.answer()


# ---------- ЗАПУСК ----------

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден в .env")

    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрация хендлеров
    dp.message.register(start_handler, CommandStart())
    dp.message.register(amount_handler, ExchangeStates.waiting_amount)
    dp.callback_query.register(direction_rub_to_kzt_handler, F.data == "direction_rub_to_kzt")
    dp.callback_query.register(direction_kzt_to_rub_handler, F.data == "direction_kzt_to_rub")
    dp.callback_query.register(back_to_menu_handler, F.data == "back_to_menu")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
