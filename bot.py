import os
import logging
import sqlite3
import datetime

import requests
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

WMO_DESCRIPTIONS = {
    0: ("Ясно", "☀️"),
    1: ("Преимущественно ясно", "🌤"),
    2: ("Переменная облачность", "⛅"),
    3: ("Пасмурно", "☁️"),
    45: ("Туман", "🌫"),
    48: ("Изморозь", "🌫❄️"),
    51: ("Лёгкая морось", "🌦"),
    53: ("Морось", "🌧"),
    55: ("Сильная морось", "🌧"),
    56: ("Ледяная морось", "🌧❄️"),
    57: ("Сильная ледяная морось", "🌧❄️"),
    61: ("Небольшой дождь", "🌦"),
    63: ("Дождь", "🌧"),
    65: ("Сильный дождь", "🌧💧"),
    66: ("Ледяной дождь", "🧊🌧"),
    67: ("Сильный ледяной дождь", "🧊🌧"),
    71: ("Небольшой снег", "🌨"),
    73: ("Снег", "❄️"),
    75: ("Сильный снег", "❄️❄️"),
    77: ("Снежные зёрна", "🌨"),
    80: ("Небольшой ливень", "🌦"),
    81: ("Ливень", "⛈"),
    82: ("Сильный ливень", "⛈💧"),
    85: ("Снежный ливень", "🌨❄️"),
    86: ("Сильный снежный ливень", "🌨❄️❄️"),
    95: ("Гроза", "⛈⚡"),
    96: ("Гроза с градом", "⛈🧊"),
    99: ("Сильная гроза с градом", "⛈🧊⚡"),
}

# ── Database ────────────────────────────────────────────────────────────────


def init_db():
    conn = sqlite3.connect("favorites.db")
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorite_cities (
            user_id INTEGER,
            city_name TEXT,
            latitude REAL,
            longitude REAL,
            PRIMARY KEY (user_id, city_name)
        )
        """
    )
    conn.commit()
    conn.close()


def add_favorite(user_id: int, city: str, lat: float, lon: float):
    conn = sqlite3.connect("favorites.db")
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO favorite_cities VALUES (?, ?, ?, ?)",
        (user_id, city, lat, lon),
    )
    conn.commit()
    conn.close()


def remove_favorite(user_id: int, city: str):
    conn = sqlite3.connect("favorites.db")
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM favorite_cities WHERE user_id = ? AND city_name = ?",
        (user_id, city),
    )
    conn.commit()
    conn.close()


def get_favorites(user_id: int) -> list[dict]:
    conn = sqlite3.connect("favorites.db")
    cur = conn.cursor()
    cur.execute(
        "SELECT city_name, latitude, longitude FROM favorite_cities WHERE user_id = ?",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"city": r[0], "lat": r[1], "lon": r[2]} for r in rows]


# ── API helpers ─────────────────────────────────────────────────────────────


def geocode(city_name: str) -> dict | None:
    resp = requests.get(
        GEOCODING_URL,
        params={"name": city_name, "count": 1, "language": "ru", "format": "json"},
        timeout=10,
    )
    data = resp.json()
    results = data.get("results")
    if not results:
        return None
    r = results[0]
    return {
        "name": r.get("name", city_name),
        "country": r.get("country", ""),
        "lat": r["latitude"],
        "lon": r["longitude"],
    }


def fetch_weather(lat: float, lon: float) -> dict:
    resp = requests.get(
        WEATHER_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max,sunrise,sunset,uv_index_max",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=10,
    )
    return resp.json()


# ── Outfit & activity logic ────────────────────────────────────────────────


def clothing_advice(temp: float, precip: float, wind: float) -> str:
    items = []
    if temp <= -15:
        items.append("🧥 Тёплый пуховик, шапка-ушанка, термобельё")
    elif temp <= -5:
        items.append("🧥 Зимняя куртка, шапка, перчатки, шарф")
    elif temp <= 5:
        items.append("🧥 Демисезонная куртка, свитер, шапка")
    elif temp <= 15:
        items.append("🧶 Лёгкая куртка или ветровка, джинсы")
    elif temp <= 22:
        items.append("👕 Лёгкая одежда, можно кофту на вечер")
    else:
        items.append("🩳 Шорты, футболка, панама")

    if precip > 0.5:
        items.append("☂️ Обязательно возьми зонт!")
    elif precip > 0:
        items.append("🌂 На всякий случай захвати зонт")

    if wind > 40:
        items.append("💨 Очень сильный ветер — одевайся плотнее")
    elif wind > 20:
        items.append("🍃 Ветрено — ветровка не помешает")

    return "\n".join(items)


def activity_advice(temp: float, weather_code: int, wind: float) -> str:
    rain_codes = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
    snow_codes = {71, 73, 75, 77, 85, 86}
    storm_codes = {95, 96, 99}

    if weather_code in storm_codes:
        return "🏠 Лучше остаться дома — на улице гроза!\n🎬 Кино, книга или сериал — идеальный план"
    if weather_code in rain_codes:
        return (
            "☕ Отличный повод для уютного кафе\n"
            "🎮 Настолки или видеоигры с друзьями\n"
            "📚 Хороший день для чтения"
        )
    if weather_code in snow_codes:
        if temp < -10:
            return "⛷ Можно на лыжи или каток\n🍵 Или горячий чай дома у окна"
        return "⛷ Отличная погода для зимних развлечений!\n☃️ Можно слепить снеговика"

    if temp > 25 and wind < 20:
        return (
            "🏖 Идеально для пляжа или бассейна\n"
            "🚴 Велопрогулка или роликовые коньки\n"
            "🍦 Не забудь мороженое!"
        )
    if 15 < temp <= 25:
        return (
            "🏃 Отличная погода для пробежки\n"
            "🧺 Пикник в парке\n"
            "📸 Фотопрогулка по городу"
        )
    if 5 < temp <= 15:
        return "🚶 Прогулка в парке\n🏛 Хороший день для музея или выставки"
    if temp <= 5:
        return "🏛 Музей, выставка или кино\n☕ Уютное кафе с горячим какао"

    return "🌤 Хороший день — наслаждайся!"


# ── Formatters ──────────────────────────────────────────────────────────────


def format_current(city: str, country: str, data: dict) -> str:
    cur = data["current"]
    temp = cur["temperature_2m"]
    feels = cur["apparent_temperature"]
    humidity = cur["relative_humidity_2m"]
    wind = cur["wind_speed_10m"]
    precip = cur["precipitation"]
    code = cur["weather_code"]

    desc, emoji = WMO_DESCRIPTIONS.get(code, ("Неизвестно", "❓"))
    clothes = clothing_advice(temp, precip, wind)
    activities = activity_advice(temp, code, wind)

    sunrise_raw = data.get("daily", {}).get("sunrise", [""])[0]
    sunset_raw = data.get("daily", {}).get("sunset", [""])[0]
    uv_index = data.get("daily", {}).get("uv_index_max", [None])[0]

    sunrise_str = sunrise_raw[11:16] if sunrise_raw else "—"
    sunset_str = sunset_raw[11:16] if sunset_raw else "—"

    uv_line = ""
    if uv_index is not None:
        if uv_index >= 8:
            uv_line = f"☀️ UV-индекс: <b>{uv_index}</b> (высокий — нужен крем SPF50!)\n"
        elif uv_index >= 5:
            uv_line = f"🔆 UV-индекс: <b>{uv_index}</b> (средний — стоит нанести крем)\n"
        else:
            uv_line = f"🌤 UV-индекс: <b>{uv_index}</b> (низкий)\n"

    return (
        f"📍 <b>{city}</b>, {country}\n"
        f"{emoji} {desc}\n\n"
        f"🌡 Температура: <b>{temp}°C</b> (ощущается как {feels}°C)\n"
        f"💧 Влажность: {humidity}%\n"
        f"💨 Ветер: {wind} км/ч\n"
        f"🌧 Осадки: {precip} мм\n"
        f"{uv_line}"
        f"🌅 Восход: {sunrise_str}  🌇 Закат: {sunset_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👗 <b>Что надеть:</b>\n{clothes}\n\n"
        f"🎯 <b>Чем заняться:</b>\n{activities}"
    )


def format_forecast(city: str, data: dict) -> str:
    daily = data["daily"]
    lines = [f"📅 <b>Прогноз для {city} на 3 дня:</b>\n"]

    day_names = {
        0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс",
    }

    for i in range(len(daily["time"])):
        date = datetime.date.fromisoformat(daily["time"][i])
        day_label = day_names[date.weekday()]
        code = daily["weather_code"][i]
        t_min = daily["temperature_2m_min"][i]
        t_max = daily["temperature_2m_max"][i]
        precip = daily["precipitation_sum"][i]
        wind = daily["wind_speed_10m_max"][i]
        desc, emoji = WMO_DESCRIPTIONS.get(code, ("Неизвестно", "❓"))
        avg_temp = (t_min + t_max) / 2
        clothes_hint = clothing_advice(avg_temp, precip, wind)
        first_hint = clothes_hint.split("\n")[0]

        lines.append(
            f"{emoji} <b>{day_label}, {date.strftime('%d.%m')}</b> — {desc}\n"
            f"   🌡 {t_min}°…{t_max}°C  🌧 {precip} мм  💨 {wind} км/ч\n"
            f"   {first_hint}"
        )

    return "\n".join(lines)


# ── Handlers ────────────────────────────────────────────────────────────────

def format_compare(city1: str, country1: str, data1: dict,
                    city2: str, country2: str, data2: dict) -> str:
    def _row(data):
        cur = data["current"]
        code = cur["weather_code"]
        _, emoji = WMO_DESCRIPTIONS.get(code, ("", "❓"))
        return {
            "temp": cur["temperature_2m"],
            "feels": cur["apparent_temperature"],
            "wind": cur["wind_speed_10m"],
            "precip": cur["precipitation"],
            "humidity": cur["relative_humidity_2m"],
            "emoji": emoji,
        }

    a, b = _row(data1), _row(data2)

    def winner(val1, val2, higher_better=True):
        if val1 == val2:
            return "—", "—"
        if higher_better:
            return ("👑" if val1 > val2 else ""), ("👑" if val2 > val1 else "")
        return ("👑" if val1 < val2 else ""), ("👑" if val2 < val1 else "")

    tw = winner(a["temp"], b["temp"])

    return (
        f"⚖️ <b>Сравнение погоды</b>\n\n"
        f"{'':─<30}\n"
        f"📍 <b>{city1}</b> {a['emoji']}\n"
        f"   🌡 {a['temp']}°C (ощущается {a['feels']}°C)\n"
        f"   💨 {a['wind']} км/ч  🌧 {a['precip']} мм  💧 {a['humidity']}%\n\n"
        f"📍 <b>{city2}</b> {b['emoji']}\n"
        f"   🌡 {b['temp']}°C (ощущается {b['feels']}°C)\n"
        f"   💨 {b['wind']} км/ч  🌧 {b['precip']} мм  💧 {b['humidity']}%\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Где теплее:</b> {'одинаково' if a['temp'] == b['temp'] else (city1 if a['temp'] > b['temp'] else city2)}\n"
        f"🌧 <b>Где суше:</b> {'одинаково' if a['precip'] == b['precip'] else (city1 if a['precip'] < b['precip'] else city2)}\n"
        f"💨 <b>Где тише:</b> {'одинаково' if a['wind'] == b['wind'] else (city1 if a['wind'] < b['wind'] else city2)}"
    )


MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🌤 Погода сейчас", "📅 Прогноз 3 дня"],
        ["⚖️ Сравнить города", "⭐ Избранные города"],
        ["ℹ️ Помощь"],
    ],
    resize_keyboard=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я — <b>Погодный Советник</b>.\n\n"
        "Я не только покажу погоду, но и подскажу <b>что надеть</b> "
        "и <b>чем заняться</b>!\n\n"
        "Просто напиши мне название города 🏙\n"
        "Или используй кнопки ниже 👇",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )
    context.user_data["mode"] = None


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Как пользоваться:</b>\n\n"
        "🔹 Напиши название города — получишь погоду + советы\n"
        "🔹 /forecast Город — прогноз на 3 дня\n"
        "🔹 /compare Город1, Город2 — сравнить погоду\n"
        "🔹 /save Город — добавить в избранное\n"
        "🔹 /favorites — список избранных городов\n"
        "🔹 /remove Город — убрать из избранного\n\n"
        "Или используй кнопки в меню 👇",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "ℹ️ Помощь":
        await help_command(update, context)
        return

    if text == "⭐ Избранные города":
        await favorites_command(update, context)
        return

    if text == "🌤 Погода сейчас":
        context.user_data["mode"] = "current"
        await update.message.reply_text(
            "🏙 Напиши название города:",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if text == "📅 Прогноз 3 дня":
        context.user_data["mode"] = "forecast"
        await update.message.reply_text(
            "🏙 Напиши название города для прогноза:",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if text == "⚖️ Сравнить города":
        context.user_data["mode"] = "compare"
        await update.message.reply_text(
            "🏙 Напиши два города через запятую:\n"
            "Например: <i>Москва, Сочи</i>",
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD,
        )
        return


async def forecast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        context.user_data["mode"] = "forecast"
        await update.message.reply_text("🏙 Напиши название города для прогноза:")
        return
    city_name = " ".join(context.args)
    await send_forecast(update, city_name)


async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        context.user_data["mode"] = "compare"
        await update.message.reply_text(
            "🏙 Напиши два города через запятую:\n"
            "Например: <i>Москва, Сочи</i>",
            parse_mode="HTML",
        )
        return
    raw = " ".join(context.args)
    await send_compare(update, raw)


async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /save Название города")
        return
    city_name = " ".join(context.args)
    geo = geocode(city_name)
    if not geo:
        await update.message.reply_text(f"😕 Город «{city_name}» не найден")
        return
    add_favorite(update.effective_user.id, geo["name"], geo["lat"], geo["lon"])
    await update.message.reply_text(
        f"⭐ <b>{geo['name']}</b> добавлен в избранное!",
        parse_mode="HTML",
    )


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /remove Название города")
        return
    city_name = " ".join(context.args)
    remove_favorite(update.effective_user.id, city_name)
    await update.message.reply_text(f"🗑 «{city_name}» удалён из избранного")


async def favorites_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    favs = get_favorites(update.effective_user.id)
    if not favs:
        await update.message.reply_text(
            "У тебя пока нет избранных городов.\n"
            "Добавь командой: /save Название города"
        )
        return

    buttons = [
        [InlineKeyboardButton(f"🌤 {f['city']}", callback_data=f"fav_current|{f['city']}|{f['lat']}|{f['lon']}"),
         InlineKeyboardButton(f"📅 {f['city']}", callback_data=f"fav_forecast|{f['city']}|{f['lat']}|{f['lon']}")]
        for f in favs
    ]
    await update.message.reply_text(
        "⭐ <b>Избранные города</b>\n"
        "Нажми 🌤 для текущей погоды или 📅 для прогноза:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def favorite_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    action, city, lat, lon = parts[0], parts[1], float(parts[2]), float(parts[3])

    try:
        weather = fetch_weather(lat, lon)
    except Exception as e:
        logger.error("Weather API error: %s", e)
        await query.message.reply_text("😕 Не удалось получить погоду, попробуй позже")
        return

    if action == "fav_current":
        text = format_current(city, "", weather)
    else:
        text = format_forecast(city, weather)

    await query.message.reply_text(text, parse_mode="HTML")


async def city_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    mode = context.user_data.get("mode", "current")

    if mode == "compare":
        context.user_data["mode"] = None
        await send_compare(update, text)
    elif mode == "forecast":
        context.user_data["mode"] = None
        await send_forecast(update, text)
    else:
        context.user_data["mode"] = None
        await send_current(update, text)


async def send_current(update: Update, city_name: str):
    geo = geocode(city_name)
    if not geo:
        await update.message.reply_text(
            f"😕 Город «{city_name}» не найден. Попробуй ещё раз."
        )
        return

    try:
        weather = fetch_weather(geo["lat"], geo["lon"])
    except Exception as e:
        logger.error("Weather API error: %s", e)
        await update.message.reply_text("😕 Не удалось получить погоду, попробуй позже")
        return

    text = format_current(geo["name"], geo["country"], weather)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


async def send_compare(update: Update, raw_text: str):
    parts = [p.strip() for p in raw_text.split(",")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await update.message.reply_text(
            "⚖️ Напиши два города через запятую:\n"
            "Например: <i>Москва, Сочи</i>",
            parse_mode="HTML",
        )
        return

    geo1 = geocode(parts[0])
    geo2 = geocode(parts[1])
    if not geo1:
        await update.message.reply_text(f"😕 Город «{parts[0]}» не найден")
        return
    if not geo2:
        await update.message.reply_text(f"😕 Город «{parts[1]}» не найден")
        return

    try:
        w1 = fetch_weather(geo1["lat"], geo1["lon"])
        w2 = fetch_weather(geo2["lat"], geo2["lon"])
    except Exception as e:
        logger.error("Weather API error: %s", e)
        await update.message.reply_text("😕 Не удалось получить погоду, попробуй позже")
        return

    text = format_compare(
        geo1["name"], geo1["country"], w1,
        geo2["name"], geo2["country"], w2,
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


async def send_forecast(update: Update, city_name: str):
    geo = geocode(city_name)
    if not geo:
        await update.message.reply_text(
            f"😕 Город «{city_name}» не найден. Попробуй ещё раз."
        )
        return

    try:
        weather = fetch_weather(geo["lat"], geo["lon"])
    except Exception as e:
        logger.error("Weather API error: %s", e)
        await update.message.reply_text("😕 Не удалось получить погоду, попробуй позже")
        return

    text = format_forecast(geo["name"], weather)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("forecast", forecast_command))
    app.add_handler(CommandHandler("compare", compare_command))
    app.add_handler(CommandHandler("save", save_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("favorites", favorites_command))

    app.add_handler(
        MessageHandler(
            filters.Regex(r"^(🌤 Погода сейчас|📅 Прогноз 3 дня|⚖️ Сравнить города|⭐ Избранные города|ℹ️ Помощь)$"),
            button_handler,
        )
    )

    app.add_handler(CallbackQueryHandler(favorite_callback, pattern=r"^fav_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, city_message))

    app.add_error_handler(error_handler)

    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
