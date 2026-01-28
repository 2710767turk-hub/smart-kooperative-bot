import asyncio
import os
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile, Update
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

# Глобальные переменные для хранения рассчитанных курсов
calculated_rates = {
    'rub_to_kzt': None,
    'kzt_to_rub': None
}


# ---------- КЛАВИАТУРЫ ----------

def request_rate_kb():
    """Кнопка запроса курса"""
    kb = InlineKeyboardBuilder()
    kb.button(text="Запросить курс", callback_data="request_rate")
    return kb.as_markup()


def rates_menu_kb():
    """Меню после показа курсов"""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить курс", callback_data="request_rate")
    kb.button(text="💸 Калькулятор RUB ➡️ KZT", callback_data="calc_rub_to_kzt")
    kb.button(text="💸 Калькулятор KZT ➡️ RUB", callback_data="calc_kzt_to_rub")
    kb.adjust(1)  # По одной кнопке в ряд
    return kb.as_markup()


def rub_to_kzt_calc_choice_kb():
    """Выбор способа расчета для RUB → KZT"""
    kb = InlineKeyboardBuilder()
    kb.button(text="Введу сумму в рублях", callback_data="rub_to_kzt_input_rub")
    kb.button(text="Введу сумму в тенге", callback_data="rub_to_kzt_input_kzt")
    kb.adjust(1)
    return kb.as_markup()


def kzt_to_rub_calc_choice_kb():
    """Выбор способа расчета для KZT → RUB"""
    kb = InlineKeyboardBuilder()
    kb.button(text="Введу сумму в рублях", callback_data="kzt_to_rub_input_rub")
    kb.button(text="Введу сумму в тенге", callback_data="kzt_to_rub_input_kzt")
    kb.adjust(1)
    return kb.as_markup()


def back_to_rates_kb():
    """Кнопка возврата к курсам"""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить курс", callback_data="request_rate")
    return kb.as_markup()


# ---------- СОСТОЯНИЯ FSM ----------

class ExchangeStates(StatesGroup):
    # Для RUB → KZT
    rub_to_kzt_waiting_rub = State()  # Ожидаем сумму в рублях
    rub_to_kzt_waiting_kzt = State()  # Ожидаем желаемую сумму в тенге
    
    # Для KZT → RUB
    kzt_to_rub_waiting_kzt = State()  # Ожидаем сумму в тенге
    kzt_to_rub_waiting_rub = State()  # Ожидаем желаемую сумму в рублях


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
    
    RUB → KZT: вычитаем 4% (умножаем на 0.96)
    KZT → RUB: прибавляем 4% (1 / base_rate * 1.04)
    """
    rub_per_usd, kzt_per_usd = get_market_rates()
    
    # Базовый рыночный курс: 1 RUB = base_rate KZT
    # base_rate = KZT/USD / RUB/USD
    base_rate = kzt_per_usd / rub_per_usd
    
    # Клиентские курсы с учетом спреда
    rate_rub_to_kzt = base_rate * 0.96  # RUB → KZT (вычитаем 4%)
    
    # KZT → RUB: прибавляем 4% к базовому курсу
    rate_kzt_to_rub = base_rate * 1.04  # KZT → RUB (прибавляем 4%)
    
    # Сохраняем в глобальные переменные
    calculated_rates['rub_to_kzt'] = rate_rub_to_kzt
    calculated_rates['kzt_to_rub'] = rate_kzt_to_rub
    
    return rate_rub_to_kzt, rate_kzt_to_rub


# ---------- ХЕНДЛЕРЫ ----------

async def start_handler(message: Message):
    """Обработчик команды /start - Блок 1"""
    text = (
        "Здравствуйте! 👋\n\n"
        "🏦 Здесь вы можете быстро совершить обмен РУБЛИ на ТЕНГЕ и обратно."
    )
    
    await message.answer(text)
    
    # Блок 2
    text2 = (
        "💹 Поскольку на бирже непрерывно меняется курс, мы обновляем его каждые 20 минут.\n\n"
        "🛎️ Нажми чтоб запросить актуальный курс 👇"
    )
    
    await message.answer(text2, reply_markup=request_rate_kb())


async def request_rate_handler(callback: CallbackQuery):
    """Обработчик запроса курса - Блок 3"""
    try:
        await callback.answer("Запрашиваю актуальный курс...")
        
        # Блок 3: Запрос курса
        rate_rub_to_kzt, rate_kzt_to_rub = calculate_rates()
        
        # Пример для 1000 рублей
        example_rub = 1000
        example_kzt_result = example_rub * rate_rub_to_kzt
        
        # Пример для 1000 тенге
        example_kzt = 1000
        example_rub_result = example_kzt * rate_kzt_to_rub
        
        # Блок 4: Курс RUB → KZT
        text_rub_to_kzt = (
            f"📈 Обменный курс РУБЛИ на ТЕНГЕ\n"
            f"<b>{rate_rub_to_kzt:.2f}</b>\n\n"
            f"🏧 Это значит что если вы меняете 1000 рублей, то получите на счёт <b>{int(round(example_kzt_result))}</b> тенге"
        )
        
        # Блок 5: Курс KZT → RUB
        # Используем рассчитанный курс rate_kzt_to_rub напрямую
        text_kzt_to_rub = (
            f"📈 Обменный курс ТЕНГЕ на РУБЛИ\n"
            f"<b>{rate_kzt_to_rub:.2f}</b>\n\n"
            f"🏧 Это значит что если вы меняете 1000 тенге, то получите на счёт <b>{int(round(example_rub_result))}</b> рублей ➡️"
        )
        
        await callback.message.answer(text_rub_to_kzt, parse_mode="HTML")
        await callback.message.answer(text_kzt_to_rub, reply_markup=rates_menu_kb(), parse_mode="HTML")
        
    except Exception as e:
        error_text = f"❌ Ошибка при получении курса: {str(e)}"
        await callback.message.answer(error_text, reply_markup=request_rate_kb())


# ---------- КАЛЬКУЛЯТОР RUB → KZT ----------

async def calc_rub_to_kzt_handler(callback: CallbackQuery):
    """Обработчик выбора калькулятора RUB → KZT - Блок 8"""
    text = (
        "Вы можете указать сумму в РУБЛЯХ 🇷🇺, которую хотите обменять, либо введите сумму в ТЕНГЕ 🇰🇿, "
        "которую вы хотите получить на Казахстанский счёт."
    )
    
    await callback.message.answer(text, reply_markup=rub_to_kzt_calc_choice_kb())
    await callback.answer()


async def rub_to_kzt_input_rub_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора ввода суммы в рублях - Блок 12"""
    await state.set_state(ExchangeStates.rub_to_kzt_waiting_rub)
    await callback.message.answer("Введите сумму в рублях 👇")
    await callback.answer()


async def rub_to_kzt_input_kzt_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора ввода желаемой суммы в тенге - Блок 13"""
    await state.set_state(ExchangeStates.rub_to_kzt_waiting_kzt)
    text = (
        "Введите сумму в ТЕНГЕ, которую вы хотите получить на казахстанский счёт, "
        "а мы рассчитаем, сколько для этого Вам нужно рублей 👇"
    )
    await callback.message.answer(text)
    await callback.answer()


async def rub_to_kzt_amount_rub_handler(message: Message, state: FSMContext):
    """Обработчик ввода суммы в рублях - Блок 17 → Блок 19"""
    try:
        if not message.text or not message.text.strip():
            await message.answer("❌ Пожалуйста, введите сумму числом:")
            return
        
        amount_rub = float(message.text.replace(',', '.').strip())
        
        if amount_rub <= 0:
            await message.answer("❌ Сумма должна быть больше нуля. Введите корректную сумму:")
            return
        
        rate = calculated_rates.get('rub_to_kzt')
        if not rate:
            rate, _ = calculate_rates()
        
        result_kzt = amount_rub * rate
        
        text = (
            f"💰 Если вы отправите <b>{int(round(amount_rub))}</b> руб., то\n"
            f"получите на Казахстанский счёт\n"
            f"<b>{int(round(result_kzt))}</b> тенге"
        )
        
        await message.answer(text, reply_markup=back_to_rates_kb(), parse_mode="HTML")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число (например: 1000 или 1000.50):")
    except Exception as e:
        await message.answer(f"❌ Ошибка при расчете: {str(e)}", reply_markup=back_to_rates_kb())
        await state.clear()


async def rub_to_kzt_amount_kzt_handler(message: Message, state: FSMContext):
    """Обработчик ввода желаемой суммы в тенге - Блок 18 → Блок 21"""
    try:
        if not message.text or not message.text.strip():
            await message.answer("❌ Пожалуйста, введите сумму числом:")
            return
        
        desired_kzt = float(message.text.replace(',', '.').strip())
        
        if desired_kzt <= 0:
            await message.answer("❌ Сумма должна быть больше нуля. Введите корректную сумму:")
            return
        
        rate = calculated_rates.get('rub_to_kzt')
        if not rate:
            rate, _ = calculate_rates()
        
        required_rub = desired_kzt / rate
        
        text = (
            f"📝 Вам нужно сделать перевод на сумму\n"
            f"🇷🇺 <b>{int(round(required_rub))}</b> рублей, чтоб получить <b>{int(round(desired_kzt))}</b> тенге 🇰🇿 на счёт"
        )
        
        await message.answer(text, reply_markup=back_to_rates_kb(), parse_mode="HTML")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число (например: 1000 или 1000.50):")
    except Exception as e:
        await message.answer(f"❌ Ошибка при расчете: {str(e)}", reply_markup=back_to_rates_kb())
        await state.clear()


# ---------- КАЛЬКУЛЯТОР KZT → RUB ----------

async def calc_kzt_to_rub_handler(callback: CallbackQuery):
    """Обработчик выбора калькулятора KZT → RUB - Блок 9"""
    text = (
        "💶 Вы можете указать сумму в ТЕНГЕ 🇰🇿, которую хотите обменять, либо введите сумму в РУБЛЯХ 🇷🇺, "
        "которую вы хотите получить на карту РФ."
    )
    
    await callback.message.answer(text, reply_markup=kzt_to_rub_calc_choice_kb())
    await callback.answer()


async def kzt_to_rub_input_rub_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора ввода желаемой суммы в рублях - Блок 15"""
    await state.set_state(ExchangeStates.kzt_to_rub_waiting_rub)
    text = "🇷🇺 Введите сумму в рублях, которую вы хотите получить 👇"
    await callback.message.answer(text)
    await callback.answer()


async def kzt_to_rub_input_kzt_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора ввода суммы в тенге - Блок 16"""
    await state.set_state(ExchangeStates.kzt_to_rub_waiting_kzt)
    text = "🇰🇿 Введите сумму в тенге, сколько вы хотите обменять на рубли 👇"
    await callback.message.answer(text)
    await callback.answer()


async def kzt_to_rub_amount_rub_handler(message: Message, state: FSMContext):
    """Обработчик ввода желаемой суммы в рублях - Блок 23 → Блок 22"""
    try:
        if not message.text or not message.text.strip():
            await message.answer("❌ Пожалуйста, введите сумму числом:")
            return
        
        desired_rub = float(message.text.replace(',', '.').strip())
        
        if desired_rub <= 0:
            await message.answer("❌ Сумма должна быть больше нуля. Введите корректную сумму:")
            return
        
        rate = calculated_rates.get('kzt_to_rub')
        if not rate:
            _, rate = calculate_rates()
        
        required_kzt = desired_rub / rate
        
        text = (
            f"💰 Вы должны перевести на Казахстанскую карту <b>{int(round(required_kzt))}</b> тенге 🇰🇿, "
            f"чтоб получить <b>{int(round(desired_rub))}</b> рублей 🇷🇺"
        )
        
        await message.answer(text, reply_markup=back_to_rates_kb(), parse_mode="HTML")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число (например: 1000 или 1000.50):")
    except Exception as e:
        await message.answer(f"❌ Ошибка при расчете: {str(e)}", reply_markup=back_to_rates_kb())
        await state.clear()


async def kzt_to_rub_amount_kzt_handler(message: Message, state: FSMContext):
    """Обработчик ввода суммы в тенге - Блок 24 → Блок 25"""
    try:
        if not message.text or not message.text.strip():
            await message.answer("❌ Пожалуйста, введите сумму числом:")
            return
        
        amount_kzt = float(message.text.replace(',', '.').strip())
        
        if amount_kzt <= 0:
            await message.answer("❌ Сумма должна быть больше нуля. Введите корректную сумму:")
            return
        
        rate = calculated_rates.get('kzt_to_rub')
        if not rate:
            _, rate = calculate_rates()
        
        result_rub = amount_kzt * rate
        
        text = (
            f"💰 Если вы переведете на Казахстанскую карту <b>{int(round(amount_kzt))}</b> тенге 🇰🇿, "
            f"вы получите <b>{int(round(result_rub))}</b> рублей 🇷🇺 на счет в РФ"
        )
        
        await message.answer(text, reply_markup=back_to_rates_kb(), parse_mode="HTML")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число (например: 1000 или 1000.50):")
    except Exception as e:
        await message.answer(f"❌ Ошибка при расчете: {str(e)}", reply_markup=back_to_rates_kb())
        await state.clear()


# ---------- ЗАПУСК ----------

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден в .env")

    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрация хендлеров
    dp.message.register(start_handler, CommandStart())
    
    # Запрос курса
    dp.callback_query.register(request_rate_handler, F.data == "request_rate")
    
    # Калькуляторы
    dp.callback_query.register(calc_rub_to_kzt_handler, F.data == "calc_rub_to_kzt")
    dp.callback_query.register(calc_kzt_to_rub_handler, F.data == "calc_kzt_to_rub")
    
    # Выбор способа ввода для RUB → KZT
    dp.callback_query.register(rub_to_kzt_input_rub_handler, F.data == "rub_to_kzt_input_rub")
    dp.callback_query.register(rub_to_kzt_input_kzt_handler, F.data == "rub_to_kzt_input_kzt")
    
    # Выбор способа ввода для KZT → RUB
    dp.callback_query.register(kzt_to_rub_input_rub_handler, F.data == "kzt_to_rub_input_rub")
    dp.callback_query.register(kzt_to_rub_input_kzt_handler, F.data == "kzt_to_rub_input_kzt")
    
    # Обработчики ввода сумм
    dp.message.register(rub_to_kzt_amount_rub_handler, ExchangeStates.rub_to_kzt_waiting_rub)
    dp.message.register(rub_to_kzt_amount_kzt_handler, ExchangeStates.rub_to_kzt_waiting_kzt)
    dp.message.register(kzt_to_rub_amount_rub_handler, ExchangeStates.kzt_to_rub_waiting_rub)
    dp.message.register(kzt_to_rub_amount_kzt_handler, ExchangeStates.kzt_to_rub_waiting_kzt)

    # Обработчик ошибок
    async def error_handler(update: Update, exception: Exception):
        """Глобальный обработчик ошибок"""
        logger.error(f"Ошибка: {exception}", exc_info=exception)
        
        error_str = str(exception).lower()
        if "no text in the message to edit" in error_str or "bad request: there is no text" in error_str:
            logger.warning("Попытка редактировать сообщение с пустым текстом - игнорируем")
            return True
        
        try:
            if update and update.message:
                await update.message.answer(
                    "❌ Произошла ошибка. Пожалуйста, попробуйте еще раз или используйте /start"
                )
            elif update and update.callback_query:
                await update.callback_query.message.answer(
                    "❌ Произошла ошибка. Пожалуйста, попробуйте еще раз или используйте /start"
                )
                await update.callback_query.answer()
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")
        
        return True
    
    dp.errors.register(error_handler)
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
