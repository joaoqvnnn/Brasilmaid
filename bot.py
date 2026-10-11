import os
import json
import logging
from math import radians, sin, cos, sqrt, atan2

import httpx
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from openai import OpenAI

# ─────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # ex: https://seu-bot.onrender.com
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN não configurado!")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ OPENAI_API_KEY não configurado!")
if not WEBHOOK_URL:
    raise RuntimeError("❌ WEBHOOK_URL não configurado!")

client = OpenAI(api_key=OPENAI_API_KEY)


# ─────────────────────────────────────────
# 1. CONSULTAR CEP (BrasilAPI)
# ─────────────────────────────────────────
async def buscar_cep(cep: str):
    cep = "".join(filter(str.isdigit, cep))
    if len(cep) != 8:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"https://brasilapi.com.br/api/cep/v2/{cep}")
            if r.status_code == 200:
                data = r.json()
                coords = data.get("location", {}).get("coordinates", {}) or {}
                lat = coords.get("latitude")
                lon = coords.get("longitude")
                return {
                    "endereco": f"{data.get('street', '')}, {data.get('neighborhood', '')}",
                    "cidade": data.get("city", ""),
                    "uf": data.get("state", ""),
                    "lat": float(lat) if lat else None,
                    "lon": float(lon) if lon else None,
                }
    except Exception as e:
        logger.error(f"Erro CEP: {e}")
    return None


# ─────────────────────────────────────────
# 2. GEOCODIFICAR CIDADE (Nominatim)
# ─────────────────────────────────────────
async def geocodificar_cidade(nome: str):
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": nome,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "br",
                },
                headers={"User-Agent": "MeuBotLocal/1.0 (contato@exemplo.com)"},
            )
            if r.status_code == 200 and r.json():
                data = r.json()[0]
                return {"lat": float(data["lat"]), "lon": float(data["lon"])}
    except Exception as e:
        logger.error(f"Erro geocodificação: {e}")
    return None


# ─────────────────────────────────────────
# 3. BUSCAR LUGARES PRÓXIMOS (Overpass)
# ─────────────────────────────────────────
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return int(R * 2 * atan2(sqrt(a), sqrt(1 - a)))


async def buscar_lugares(lat: float, lon: float, categoria: str, raio: int = 2000):
    tags_map = {
        "restaurante": 'node["amenity"="restaurant"]',
        "farmacia": 'node["amenity"="pharmacy"]',
        "hospital": 'node["amenity"="hospital"]',
        "supermercado": 'node["shop"="supermarket"]',
        "banco": 'node["amenity"="bank"]',
        "posto": 'node["amenity"="fuel"]',
        "cafe": 'node["amenity"="cafe"]',
        "hotel": 'node["tourism"="hotel"]',
        "padaria": 'node["shop"="bakery"]',
        "escola": 'node["amenity"="school"]',
    }
    tag = tags_map.get(categoria, tags_map["restaurante"])

    query = f"""
    [out:json][timeout:15];
    (
      {tag}(around:{raio},{lat},{lon});
    );
    out body 15;
    """

    try:
        async with httpx.AsyncClient(timeout=25) as http:
            r = await http.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query},
            )
            if r.status_code == 200:
                elementos = r.json().get("elements", [])
                lugares = []
                for el in elementos:
                    nome = el.get("tags", {}).get("name", "Sem nome")
                    el_lat = el.get("lat")
                    el_lon = el.get("lon")
                    if el_lat and el_lon:
                        lugares.append({
                            "nome": nome,
                            "lat": el_lat,
                            "lon": el_lon,
                            "dist": calcular_distancia(lat, lon, el_lat, el_lon),
                        })
                lugares.sort(key=lambda x: x["dist"])
                return lugares[:8]
    except Exception as e:
        logger.error(f"Erro Overpass: {e}")
    return []


# ─────────────────────────────────────────
# 4. IA — Interpretar mensagem
# ─────────────────────────────────────────
async def interpretar_com_ia(texto: str) -> dict:
    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você extrai informações de mensagens de usuários em português. "
                        "Retorne APENAS um JSON com os campos: "
                        '{"tipo": "cep"|"cidade"|"categoria"|"desconhecido", "valor": "..."}. '
                        "Se for CEP, valor tem 8 dígitos. "
                        "Se for cidade, valor é o nome. "
                        "Se for categoria (restaurante, farmacia, hospital, supermercado, "
                        "banco, posto, cafe, hotel, padaria, escola), valor é a categoria. "
                        "Caso contrário, tipo=desconhecido e valor=''."
                    ),
                },
                {"role": "user", "content": texto},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(resposta.choices[0].message.content)
    except Exception as e:
        logger.error(f"Erro IA: {e}")
        return {"tipo": "desconhecido", "valor": ""}


# ─────────────────────────────────────────
# 5. HANDLERS DO TELEGRAM
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📍 Enviar minha localização", request_location=True)],
        [KeyboardButton("Digitar CEP"), KeyboardButton("Digitar cidade")],
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "👋 Olá! Eu encontro lugares próximos a você.\n\n"
        "Como quer informar sua localização?",
        reply_markup=reply_markup,
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    context.user_data["lat"] = loc.latitude
    context.user_data["lon"] = loc.longitude
    context.user_data["esperando"] = None
    await update.message.reply_text("✅ Localização recebida!")
    await mostrar_categorias(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text or ""

    if texto == "Digitar CEP":
        context.user_data["esperando"] = "cep"
        await update.message.reply_text("📮 Digite seu CEP (8 números):")
        return

    if texto == "Digitar cidade":
        context.user_data["esperando"] = "cidade"
        await update.message.reply_text("🏙️ Digite o nome da sua cidade:")
        return

    esperando = context.user_data.get("esperando")

    if esperando == "cep":
        resultado = await buscar_cep(texto)
        if resultado and resultado["lat"]:
            context.user_data["lat"] = resultado["lat"]
            context.user_data["lon"] = resultado["lon"]
            context.user_data["esperando"] = None
            await update.message.reply_text(
                f"✅ Encontrei: {resultado['endereco']}, "
                f"{resultado['cidade']}-{resultado['uf']}"
            )
            await mostrar_categorias(update, context)
        else:
            await update.message.reply_text(
                "❌ CEP não encontrado ou sem coordenadas. Tente novamente:"
            )
        return

    if esperando == "cidade":
        coords = await geocodificar_cidade(texto)
        if coords:
            context.user_data["lat"] = coords["lat"]
            context.user_data["lon"] = coords["lon"]
            context.user_data["esperando"] = None
            await update.message.reply_text(f"📍 Localização definida: {texto}")
            await mostrar_categorias(update, context)
        else:
            await update.message.reply_text(
                "❌ Cidade não encontrada. Digite novamente:"
            )
        return

    if context.user_data.get("lat"):
        info = await interpretar_com_ia(texto)
        if info.get("tipo") == "categoria":
            await buscar_e_mostrar(update, context, info["valor"])
        else:
            await mostrar_categorias(update, context)
    else:
        await update.message.reply_text("Use /start para começar.")


async def mostrar_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categorias = [
        ("🍽️ Restaurante", "restaurante"),
        ("💊 Farmácia", "farmacia"),
        ("🏥 Hospital", "hospital"),
        ("🛒 Supermercado", "supermercado"),
        ("🏦 Banco", "banco"),
        ("⛽ Posto", "posto"),
        ("☕ Café", "cafe"),
        ("🏨 Hotel", "hotel"),
        ("🥖 Padaria", "padaria"),
        ("🏫 Escola", "escola"),
    ]
    keyboard = []
    for i in range(0, len(categorias), 2):
        linha = [
            InlineKeyboardButton(nome, callback_data=f"cat_{valor}")
            for nome, valor in categorias[i : i + 2]
        ]
        keyboard.append(linha)
    await update.message.reply_text(
        "🔍 O que você procura perto de você?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def buscar_e_mostrar(update: Update, context: ContextTypes.DEFAULT_TYPE, categoria: str):
    lat = context.user_data.get("lat")
    lon = context.user_data.get("lon")
    if not lat or not lon:
        await update.message.reply_text("❌ Localização não definida. Use /start.")
        return

    msg = await update.message.reply_text(f"🔎 Buscando {categoria}...")
    lugares = await buscar_lugares(lat, lon, categoria)

    if not lugares:
        await msg.edit_text(
            f"😕 Nenhum resultado para *{categoria}* em 2 km.",
            parse_mode="Markdown",
        )
        return

    texto = f"📍 *{categoria.title()}* próximos:\n\n"
    for i, lugar in enumerate(lugares, 1):
        texto += f"{i}. *{lugar['nome']}* — {lugar['dist']}m\n"

    await msg.edit_text(texto, parse_mode="Markdown")

    primeiro = lugares[0]
    await context.bot.send_location(
        chat_id=update.effective_chat.id,
        latitude=primeiro["lat"],
        longitude=primeiro["lon"],
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("cat_"):
        categoria = query.data.replace("cat_", "")
        lat = context.user_data.get("lat")
        lon = context.user_data.get("lon")

        if not lat or not lon:
            await query.edit_message_text("❌ Localização não definida. Use /start.")
            return

        await query.edit_message_text(f"🔎 Buscando {categoria}...")
        lugares = await buscar_lugares(lat, lon, categoria)

        if not lugares:
            await query.edit_message_text(
                f"😕 Nenhum resultado para *{categoria}* em 2 km.",
                parse_mode="Markdown",
            )
            return

        texto = f"📍 *{categoria.title()}* próximos:\n\n"
        for i, lugar in enumerate(lugares, 1):
            texto += f"{i}. *{lugar['nome']}* — {lugar['dist']}m\n"

        await query.edit_message_text(texto, parse_mode="Markdown")

        primeiro = lugares[0]
        await context.bot.send_location(
            chat_id=query.message.chat_id,
            latitude=primeiro["lat"],
            longitude=primeiro["lon"],
        )


# ─────────────────────────────────────────
# 6. APLICAÇÃO TELEGRAM (sem polling)
# ─────────────────────────────────────────
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.LOCATION, handle_location))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(CallbackQueryHandler(handle_callback))


# ─────────────────────────────────────────
# 7. WEBHOOK (Starlette)
# ─────────────────────────────────────────
async def health(request):
    return PlainTextResponse("OK")


async def telegram_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error(f"Erro no webhook: {e}")
    return PlainTextResponse("OK")


async def on_startup():
    await application.initialize()
    await application.start()
    webhook_full_url = f"{WEBHOOK_URL}/telegram/{TELEGRAM_TOKEN}"
    await application.bot.set_webhook(url=webhook_full_url)
    logger.info(f"✅ Webhook configurado: {webhook_full_url}")


async def on_shutdown():
    await application.stop()
    await application.shutdown()


routes = [
    Route("/", health),
    Route("/health", health),
    Route(f"/telegram/{TELEGRAM_TOKEN}", telegram_webhook, methods=["POST"]),
]

starlette_app = Starlette(
    routes=routes,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)


if __name__ == "__main__":
    logger.info(f"🚀 Iniciando servidor na porta {PORT}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
