import os
import httpx
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from openai import OpenAI

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)

# ─────────────────────────────────────────
# 1. CONSULTAR CEP (BrasilAPI)
# ─────────────────────────────────────────
async def buscar_cep(cep: str):
    cep = cep.replace("-", "").replace(".", "").strip()
    if len(cep) != 8 or not cep.isdigit():
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"https://brasilapi.com.br/api/cep/v2/{cep}")
            if r.status_code == 200:
                data = r.json()
                coords = data.get("location", {}).get("coordinates", {})
                return {
                    "endereco": f"{data.get('street', '')}, {data.get('neighborhood', '')}",
                    "cidade": data.get("city", ""),
                    "uf": data.get("state", ""),
                    "lat": coords.get("latitude"),
                    "lon": coords.get("longitude"),
                }
    except Exception as e:
        logging.error(f"Erro CEP: {e}")
    return None

# ─────────────────────────────────────────
# 2. BUSCAR LUGARES PRÓXIMOS (Overpass API)
# ─────────────────────────────────────────
async def buscar_lugares(lat: float, lon: float, categoria: str, raio: int = 2000):
    # Mapeamento de categorias para tags do OpenStreetMap
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

    tag = tags_map.get(categoria, 'node["amenity"="restaurant"]')

    query = f"""
    [out:json][timeout:15];
    (
      {tag}(around:{raio},{lat},{lon});
    );
    out body 10;
    """

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query}
            )
            if r.status_code == 200:
                elementos = r.json().get("elements", [])
                lugares = []
                for el in elementos[:8]:
                    nome = el.get("tags", {}).get("name", "Sem nome")
                    el_lat = el.get("lat")
                    el_lon = el.get("lon")
                    if el_lat and el_lon:
                        lugares.append({
                            "nome": nome,
                            "lat": el_lat,
                            "lon": el_lon,
                            "dist": calcular_distancia(lat, lon, el_lat, el_lon)
                        })
                lugares.sort(key=lambda x: x["dist"])
                return lugares
    except Exception as e:
        logging.error(f"Erro Overpass: {e}")
    return []

def calcular_distancia(lat1, lon1, lat2, lon2):
    """Fórmula de Haversine simplificada (retorna metros)."""
    from math import radians, sin, cos, sqrt, atan2
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return int(R * 2 * atan2(sqrt(a), sqrt(1-a)))

# ─────────────────────────────────────────
# 3. IA — Interpretar mensagem do usuário
# ─────────────────────────────────────────
async def interpretar_com_ia(texto: str) -> dict:
    """Usa OpenAI para extrair CEP, cidade ou categoria da mensagem."""
    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você extrai informações de mensagens de usuários. "
                        "Retorne APENAS um JSON com os campos: "
                        '{"tipo": "cep"|"cidade"|"categoria", "valor": "..."}. '
                        "Se for CEP, valor deve ter 8 dígitos. "
                        "Se for cidade, valor é o nome da cidade. "
                        "Se for categoria de lugar (restaurante, farmacia, etc.), "
                        "valor é a categoria. Se não identificar nada, retorne "
                        '{"tipo": "desconhecido", "valor": ""}.'
                    )
                },
                {"role": "user", "content": texto}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        import json
        return json.loads(resposta.choices[0].message.content)
    except Exception as e:
        logging.error(f"Erro IA: {e}")
        return {"tipo": "desconhecido", "valor": ""}

# ─────────────────────────────────────────
# 4. HANDLERS DO TELEGRAM
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📍 Enviar minha localização", request_location=True)],
        [KeyboardButton("Digitar CEP")],
        [KeyboardButton("Digitar cidade")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "👋 Olá! Eu encontro lugares próximos a você.\n\n"
        "Como quer informar sua localização?",
        reply_markup=reply_markup
    )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    context.user_data["lat"] = loc.latitude
    context.user_data["lon"] = loc.longitude
    await mostrar_categorias(update, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text

    # ── Se o usuário clicou em "Digitar CEP"
    if texto == "Digitar CEP":
        await update.message.reply_text("📮 Digite seu CEP (8 números):")
        context.user_data["esperando"] = "cep"
        return

    # ── Se o usuário clicou em "Digitar cidade"
    if texto == "Digitar cidade":
        await update.message.reply_text("🏙️ Digite o nome da sua cidade:")
        context.user_data["esperando"] = "cidade"
        return

    esperando = context.user_data.get("esperando")

    # ── Se está esperando CEP
    if esperando == "cep":
        cep_limpo = "".join(filter(str.isdigit, texto))
        if len(cep_limpo) == 8:
            resultado = await buscar_cep(cep_limpo)
            if resultado and resultado["lat"]:
                context.user_data["lat"] = float(resultado["lat"])
                context.user_data["lon"] = float(resultado["lon"])
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
        else:
            await update.message.reply_text("❌ CEP inválido. Digite 8 números:")
        return

    # ── Se está esperando cidade
    if esperando == "cidade":
        # Usa IA para interpretar
        info = await interpretar_com_ia(texto)
        if info["tipo"] == "cidade":
            # Geocodifica via Nominatim (OpenStreetMap)
            coords = await geocodificar_cidade(info["valor"])
            if coords:
                context.user_data["lat"] = coords["lat"]
                context.user_data["lon"] = coords["lon"]
                context.user_data["esperando"] = None
                await update.message.reply_text(f"📍 Localização definida: {info['valor']}")
                await mostrar_categorias(update, context)
            else:
                await update.message.reply_text("❌ Cidade não encontrada. Tente novamente:")
        else:
            await update.message.reply_text("❌ Não entendi. Digite o nome da cidade:")
        return

    # ── Se já tem localização, usa IA para interpretar categoria
    if context.user_data.get("lat"):
        info = await interpretar_com_ia(texto)
        if info["tipo"] == "categoria":
            await buscar_e_mostrar(update, context, info["valor"])
        else:
            await mostrar_categorias(update, context)
    else:
        await update.message.reply_text("Use /start para começar.")

async def geocodificar_cidade(nome: str):
    """Geocodifica nome de cidade via Nominatim (OpenStreetMap)."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": nome, "format": "json", "limit": 1, "countrycodes": "br"},
                headers={"User-Agent": "MeuBotLocal/1.0"}
            )
            if r.status_code == 200 and r.json():
                data = r.json()[0]
                return {"lat": float(data["lat"]), "lon": float(data["lon"])}
    except Exception as e:
        logging.error(f"Erro geocodificação: {e}")
    return None

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
        linha = []
        for nome, valor in categorias[i:i+2]:
            linha.append(InlineKeyboardButton(nome, callback_data=f"cat_{valor}"))
        keyboard.append(linha)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔍 O que você procura perto de você?",
        reply_markup=reply_markup
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

        await query.edit_message_text(f"🔎 Buscando {categoria} próximo a você...")

        lugares = await buscar_lugares(lat, lon, categoria)

        if not lugares:
            await query.edit_message_text(
                f"😕 Nenhum resultado encontrado para **{categoria}** num raio de 2 km.\n"
                "Tente outra categoria.",
                parse_mode="Markdown"
            )
            return

        # Monta resposta
        texto = f"📍 **{categoria.title()}** próximos:\n\n"
        for i, lugar in enumerate(lugares, 1):
            texto += f"{i}. **{lugar['nome']}** — {lugar['dist']}m\n"

        await query.edit_message_text(texto, parse_mode="Markdown")

        # Envia localização no mapa do primeiro resultado
        if lugares:
            primeiro = lugares[0]
            await context.bot.send_location(
                chat_id=query.message.chat_id,
                latitude=primeiro["lat"],
                longitude=primeiro["lon"]
            )

# ─────────────────────────────────────────
# 5. MAIN
# ─────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logging.info("🤖 Bot iniciado!")
    app.run_polling()

if __name__ == "__main__":
    main()
