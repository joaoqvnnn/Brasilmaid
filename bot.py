import os
import json
import html
import logging
from math import radians, sin, cos, sqrt, atan2

import httpx
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from openai import OpenAI

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN: raise RuntimeError("❌ TELEGRAM_TOKEN não configurado!")
if not OPENAI_API_KEY: raise RuntimeError("❌ OPENAI_API_KEY não configurado!")
if not WEBHOOK_URL: raise RuntimeError("❌ WEBHOOK_URL não configurado!")

client = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────────────────────────────────
# CATEGORIAS
# ─────────────────────────────────────────
CATEGORIAS = [
    ("🍽️ Restaurante", "restaurante"),
    ("🍕 Pizzaria", "pizzaria"),
    ("🍔 Lanchonete", "fast_food"),
    ("☕ Café", "cafe"),
    ("🍺 Bar", "bar"),
    ("💊 Farmácia", "farmacia"),
    ("🏥 Hospital", "hospital"),
    ("🦷 Dentista", "dentista"),
    ("🛒 Supermercado", "supermercado"),
    ("🥖 Padaria", "padaria"),
    ("🏦 Banco", "banco"),
    ("💳 Caixa Eletrônico", "atm"),
    ("⛽ Posto", "posto"),
    ("🏨 Hotel", "hotel"),
    ("🏫 Escola", "escola"),
    ("🎓 Universidade", "universidade"),
    ("💪 Academia", "academia"),
    ("🌳 Parque", "parque"),
    ("🎬 Cinema", "cinema"),
    ("🛍️ Shopping", "shopping"),
    ("💇 Salão de Beleza", "salao"),
    ("🔧 Mecânica", "mecanica"),
    ("🚓 Delegacia", "delegacia"),
    ("📮 Correios", "correios"),
]

TAGS_MAP = {
    "restaurante": 'node["amenity"="restaurant"]',
    "pizzaria": 'node["amenity"="restaurant"]["cuisine"="pizza"]',
    "fast_food": 'node["amenity"="fast_food"]',
    "cafe": 'node["amenity"="cafe"]',
    "bar": 'node["amenity"="bar"]',
    "farmacia": 'node["amenity"="pharmacy"]',
    "hospital": 'node["amenity"="hospital"]',
    "dentista": 'node["amenity"="dentist"]',
    "supermercado": 'node["shop"="supermarket"]',
    "padaria": 'node["shop"="bakery"]',
    "banco": 'node["amenity"="bank"]',
    "atm": 'node["amenity"="atm"]',
    "posto": 'node["amenity"="fuel"]',
    "hotel": 'node["tourism"="hotel"]',
    "escola": 'node["amenity"="school"]',
    "universidade": 'node["amenity"="university"]',
    "academia": 'node["leisure"="fitness_centre"]',
    "parque": 'node["leisure"="park"]',
    "cinema": 'node["amenity"="cinema"]',
    "shopping": 'node["shop"="mall"]',
    "salao": 'node["shop"="beauty"]',
    "mecanica": 'node["shop"="car_repair"]',
    "delegacia": 'node["amenity"="police"]',
    "correios": 'node["amenity"="post_office"]',
}

ESTADOS = {
    "acre": "AC", "alagoas": "AL", "amapá": "AP", "amapa": "AP",
    "amazonas": "AM", "bahia": "BA", "ceará": "CE", "ceara": "CE",
    "distrito federal": "DF", "espírito santo": "ES", "espirito santo": "ES",
    "goiás": "GO", "goias": "GO", "maranhão": "MA", "maranhao": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG",
    "pará": "PA", "para": "PA", "paraíba": "PB", "paraiba": "PB",
    "paraná": "PR", "parana": "PR", "pernambuco": "PE", "piauí": "PI",
    "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondônia": "RO", "rondonia": "RO",
    "roraima": "RR", "santa catarina": "SC", "são paulo": "SP",
    "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO",
}

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def formatar_dist(m):
    if m < 1000:
        return f"{m} m"
    return f"{m/1000:.1f} km"


def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return int(R * 2 * atan2(sqrt(a), sqrt(1 - a)))


# ─────────────────────────────────────────
# APIs
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
                    "endereco": f"{data.get('street', '')} - {data.get('neighborhood', '')}".strip(" -"),
                    "cidade": data.get("city", ""),
                    "uf": data.get("state", ""),
                    "lat": float(lat) if lat else None,
                    "lon": float(lon) if lon else None,
                }
    except Exception as e:
        logger.error(f"Erro CEP: {e}")
    return None


async def geocodificar(consulta: str):
    """Geocodifica cidade OU endereço."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": consulta, "format": "json", "limit": 1, "countrycodes": "br"},
                headers={"User-Agent": "Brasilmaid/1.0"},
            )
            if r.status_code == 200 and r.json():
                d = r.json()[0]
                return {"lat": float(d["lat"]), "lon": float(d["lon"])}
    except Exception as e:
        logger.error(f"Erro geo: {e}")
    return None


async def listar_cidades_uf(uf: str):
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(
                f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
            )
            if r.status_code == 200:
                return [c["nome"] for c in r.json()]
    except Exception as e:
        logger.error(f"Erro IBGE: {e}")
    return []


async def buscar_lugares(lat, lon, categoria, raio=50000):
    tag = TAGS_MAP.get(categoria, TAGS_MAP["restaurante"])
    query = f"""
    [out:json][timeout:25];
    (
      {tag}(around:{raio},{lat},{lon});
    );
    out body 30;
    """
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post("https://overpass-api.de/api/interpreter", data={"data": query})
            if r.status_code == 200:
                lugares = []
                for el in r.json().get("elements", []):
                    nome = el.get("tags", {}).get("name", "Sem nome")
                    el_lat, el_lon = el.get("lat"), el.get("lon")
                    if el_lat and el_lon:
                        lugares.append({
                            "nome": nome,
                            "lat": el_lat,
                            "lon": el_lon,
                            "dist": calcular_distancia(lat, lon, el_lat, el_lon),
                        })
                lugares.sort(key=lambda x: x["dist"])
                return lugares
    except Exception as e:
        logger.error(f"Erro Overpass: {e}")
    return []


# ─────────────────────────────────────────
# IA
# ─────────────────────────────────────────
async def interpretar_com_ia(texto: str) -> dict:
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extraia informações de mensagens em português. Retorne APENAS JSON: "
                        '{"tipo": "cep"|"estado"|"cidade"|"categoria"|"desconhecido", "valor": "..."}. '
                        "cep: 8 dígitos. "
                        "estado: nome do estado brasileiro (ex: Paraná, São Paulo). "
                        "cidade: nome de cidade/município. "
                        "categoria: restaurante, pizzaria, lanchonete, cafe, bar, farmacia, "
                        "hospital, dentista, supermercado, padaria, banco, atm, posto, hotel, "
                        "escola, universidade, academia, parque, cinema, shopping, salao, "
                        "mecanica, delegacia, correios. "
                        "Senão: desconhecido."
                    ),
                },
                {"role": "user", "content": texto},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        logger.error(f"Erro IA: {e}")
        return {"tipo": "desconhecido", "valor": ""}


# ─────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────
async def enviar_categorias(bot, chat_id):
    keyboard = []
    for i in range(0, len(CATEGORIAS), 2):
        row = [
            InlineKeyboardButton(nome, callback_data=f"cat_{valor}")
            for nome, valor in CATEGORIAS[i:i + 2]
        ]
        keyboard.append(row)
    await bot.send_message(
        chat_id=chat_id,
        text="🔍 <b>O que você procura perto de você?</b>\n\n<i>Escolha uma categoria:</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def enviar_cidades(bot, chat_id, context, editar_msg=None):
    cidades = context.user_data.get("cidades", [])
    page = context.user_data.get("cidades_page", 0)
    estado = context.user_data.get("estado", "")

    per_page = 12
    total = len(cidades)
    start = page * per_page
    page_cities = cidades[start:start + per_page]
    total_pages = (total + per_page - 1) // per_page

    keyboard = []
    for i in range(0, len(page_cities), 2):
        row = [
            InlineKeyboardButton(cidade, callback_data=f"cid_{cidade}")
            for cidade in page_cities[i:i + 2]
        ]
        keyboard.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data="cid_prev"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("Próximas ➡️", callback_data="cid_next"))
    if nav:
        keyboard.append(nav)

    texto = (
        f"🏙️ <b>Cidades do {estado}</b>\n\n"
        f"Página {page + 1}/{total_pages} — escolha sua cidade:"
    )

    if editar_msg:
        await editar_msg.edit_text(
            texto, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await bot.send_message(
            chat_id, texto, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def buscar_e_mostrar(bot, chat_id, context, categoria):
    lat = context.user_data.get("lat")
    lon = context.user_data.get("lon")
    if not lat or not lon:
        await bot.send_message(chat_id, "❌ Localização não definida. Use /start.")
        return

    msg = await bot.send_message(
        chat_id,
        f"🔎 <b>Buscando {categoria.title()}...</b>\n\n<i>Procurando os mais próximos (até 50 km)...</i>",
        parse_mode="HTML",
    )

    lugares = await buscar_lugares(lat, lon, categoria, raio=50000)

    if not lugares:
        await msg.edit_text(
            f"😕 Nenhum <b>{categoria.title()}</b> encontrado num raio de 50 km.",
            parse_mode="HTML",
        )
        return

    top = lugares[:5]
    texto = f"📍 <b>{categoria.title()} mais próximos</b>\n\n"
    for i, lugar in enumerate(top, 1):
        texto += (
            f"<b>{i}. {html.escape(lugar['nome'])}</b>\n"
            f"   📏 {formatar_dist(lugar['dist'])}\n\n"
        )

    keyboard = []
    for i, lugar in enumerate(top, 1):
        nome_curto = lugar["nome"][:28]
        keyboard.append([InlineKeyboardButton(
            f"🧭 Rota até {i}. {nome_curto}",
            url=f"https://www.google.com/maps/dir/?api=1&destination={lugar['lat']},{lugar['lon']}"
        )])
    keyboard.append([InlineKeyboardButton("🔄 Nova busca", callback_data="nova_busca")])

    await msg.edit_text(
        texto,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ─────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [
        [KeyboardButton("📍 Compartilhar minha localização", request_location=True)],
        [KeyboardButton("📮 Digitar CEP"), KeyboardButton("🏙️ Digitar cidade/estado")],
    ]
    await update.message.reply_text(
        "👋 <b>Olá! Sou o Brasilmaid!</b>\n\n"
        "Eu encontro lugares próximos a você usando inteligência artificial.\n\n"
        "<b>Como quer informar sua localização?</b>\n\n"
        "📍 Compartilhe pelo botão abaixo\n"
        "📮 Digite seu CEP\n"
        "🏙️ Digite cidade ou estado",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    context.user_data["lat"] = loc.latitude
    context.user_data["lon"] = loc.longitude
    context.user_data["esperando"] = None
    await update.message.reply_text(
        "✅ <b>Localização recebida!</b>",
        parse_mode="HTML",
    )
    await enviar_categorias(context.bot, update.effective_chat.id)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (update.message.text or "").strip()

    if texto == "📮 Digitar CEP":
        context.user_data["esperando"] = "cep"
        await update.message.reply_text("📮 <b>Digite seu CEP</b> (apenas 8 números):", parse_mode="HTML")
        return

    if texto == "🏙️ Digitar cidade/estado":
        context.user_data["esperando"] = "cidade"
        await update.message.reply_text(
            "🏙️ <b>Digite o nome da cidade ou do estado</b>\n\n"
            "<i>Ex: Curitiba, Paraná, São Paulo, Bahia...</i>",
            parse_mode="HTML",
        )
        return

    esperando = context.user_data.get("esperando")

    # CEP
    if esperando == "cep":
        resultado = await buscar_cep(texto)
        if resultado and resultado["lat"]:
            context.user_data.update({
                "lat": resultado["lat"],
                "lon": resultado["lon"],
                "esperando": None,
                "cidade_atual": resultado["cidade"],
                "uf_atual": resultado["uf"],
            })
            await update.message.reply_text(
                f"✅ <b>Localização encontrada!</b>\n\n"
                f"📍 {html.escape(resultado['endereco'])}\n"
                f"🏙️ <b>{resultado['cidade']} - {resultado['uf']}</b>",
                parse_mode="HTML",
            )
            await enviar_categorias(context.bot, update.effective_chat.id)
        else:
            await update.message.reply_text("❌ CEP não encontrado. Digite novamente:")
        return

    # Cidade / Estado
    if esperando == "cidade":
        info = await interpretar_com_ia(texto)
        tipo, valor = info.get("tipo"), info.get("valor", "")

        if tipo == "estado":
            uf = ESTADOS.get(valor.lower())
            if not uf:
                await update.message.reply_text("❌ Estado não reconhecido. Tente novamente.")
                return
            cidades = await listar_cidades_uf(uf)
            if not cidades:
                await update.message.reply_text("❌ Não consegui carregar as cidades. Tente novamente.")
                return
            context.user_data.update({
                "cidades": cidades,
                "cidades_page": 0,
                "estado": valor.title(),
                "esperando": None,
            })
            await enviar_cidades(context.bot, update.effective_chat.id, context)
            return

        if tipo == "cidade":
            coords = await geocodificar(valor)
            if coords:
                context.user_data.update({
                    "lat": coords["lat"],
                    "lon": coords["lon"],
                    "esperando": None,
                    "cidade_atual": valor.title(),
                })
                await update.message.reply_text(
                    f"📍 <b>{html.escape(valor.title())}</b>",
                    parse_mode="HTML",
                )
                await enviar_categorias(context.bot, update.effective_chat.id)
            else:
                await update.message.reply_text("❌ Cidade não encontrada. Tente novamente:")
            return

        if tipo == "cep":
            resultado = await buscar_cep(valor)
            if resultado and resultado["lat"]:
                context.user_data.update({
                    "lat": resultado["lat"], "lon": resultado["lon"],
                    "esperando": None,
                    "cidade_atual": resultado["cidade"],
                })
                await update.message.reply_text(
                    f"✅ {resultado['cidade']} - {resultado['uf']}",
                    parse_mode="HTML",
                )
                await enviar_categorias(context.bot, update.effective_chat.id)
            else:
                await update.message.reply_text("❌ Não encontrado. Tente novamente.")
            return

        await update.message.reply_text("❌ Não entendi. Digite cidade ou estado:")
        return

    # Texto livre com localização já definida → IA detecta categoria
    if context.user_data.get("lat"):
        info = await interpretar_com_ia(texto)
        if info.get("tipo") == "categoria":
            await buscar_e_mostrar(context.bot, update.effective_chat.id, context, info["valor"])
        else:
            await enviar_categorias(context.bot, update.effective_chat.id)
    else:
        await update.message.reply_text("Use /start para começar.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Paginação de cidades
    if data == "cid_next":
        context.user_data["cidades_page"] = context.user_data.get("cidades_page", 0) + 1
        await enviar_cidades(context.bot, query.message.chat_id, context, editar_msg=query)
        return
    if data == "cid_prev":
        context.user_data["cidades_page"] = max(0, context.user_data.get("cidades_page", 0) - 1)
        await enviar_cidades(context.bot, query.message.chat_id, context, editar_msg=query)
        return

    # Cidade selecionada
    if data.startswith("cid_"):
        cidade = data.replace("cid_", "")
        await query.edit_message_text(f"🔎 Localizando <b>{html.escape(cidade)}</b>...", parse_mode="HTML")
        coords = await geocodificar(cidade)
        if coords:
            context.user_data.update({
                "lat": coords["lat"], "lon": coords["lon"],
                "cidade_atual": cidade,
            })
            await query.edit_message_text(f"📍 <b>{html.escape(cidade)}</b>", parse_mode="HTML")
            await enviar_categorias(context.bot, query.message.chat_id)
        else:
            await query.edit_message_text("❌ Cidade não encontrada.")
        return

    # Categoria selecionada
    if data.startswith("cat_"):
        categoria = data.replace("cat_", "")
        await query.edit_message_text(f"🔎 Buscando <b>{categoria}</b>...", parse_mode="HTML")
        await buscar_e_mostrar(context.bot, query.message.chat_id, context, categoria)
        return

    # Nova busca
    if data == "nova_busca":
        await query.edit_message_reply_markup(reply_markup=None)
        await enviar_categorias(context.bot, query.message.chat_id)
        return


# ─────────────────────────────────────────
# APLICAÇÃO
# ─────────────────────────────────────────
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.LOCATION, handle_location))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(CallbackQueryHandler(handle_callback))


async def health(request):
    return PlainTextResponse("OK")


async def telegram_webhook(request):
    try:
        data = await request.json()
        upd = Update.de_json(data, application.bot)
        await application.process_update(upd)
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
    return PlainTextResponse("OK")


async def on_startup():
    await application.initialize()
    await application.start()
    url = f"{WEBHOOK_URL}/telegram/{TELEGRAM_TOKEN}"
    await application.bot.set_webhook(url=url)
    logger.info(f"✅ Webhook configurado: {url}")


async def on_shutdown():
    await application.stop()
    await application.shutdown()


routes = [
    Route("/", health),
    Route("/health", health),
    Route(f"/telegram/{TELEGRAM_TOKEN}", telegram_webhook, methods=["POST"]),
]

starlette_app = Starlette(routes=routes, on_startup=[on_startup], on_shutdown=[on_shutdown])


if __name__ == "__main__":
    logger.info(f"🚀 Iniciando na porta {PORT}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
