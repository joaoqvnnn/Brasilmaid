import os
import json
import html
import asyncio
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

def _env(key, default=None):
    v = os.getenv(key, default)
    if isinstance(v, str):
        v = v.strip().replace("\n", "").replace("\r", "").replace("\t", "")
    return v

TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
WEBHOOK_URL = _env("WEBHOOK_URL")
PORT = int(_env("PORT", "8080") or "8080")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN: raise RuntimeError("❌ TELEGRAM_TOKEN não configurado!")
if not OPENAI_API_KEY: raise RuntimeError("❌ OPENAI_API_KEY não configurado!")
if not WEBHOOK_URL: raise RuntimeError("❌ WEBHOOK_URL não configurado!")

logger.info(f"🔑 Token: {TELEGRAM_TOKEN[:10]}...{TELEGRAM_TOKEN[-4:]} (len={len(TELEGRAM_TOKEN)})")
logger.info(f"🌐 Webhook URL: [{WEBHOOK_URL}]")

client = OpenAI(api_key=OPENAI_API_KEY)

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# ─────────────────────────────────────────
# CATEGORIAS E TAGS
# ─────────────────────────────────────────
CATEGORIAS = [
    ("🍽️ Restaurante", "restaurante"), ("🍕 Pizzaria", "pizzaria"),
    ("🍔 Lanchonete", "fast_food"), ("☕ Café", "cafe"),
    ("🍺 Bar", "bar"), ("💊 Farmácia", "farmacia"),
    ("🏥 Hospital", "hospital"), ("🦷 Dentista", "dentista"),
    ("🛒 Supermercado", "supermercado"), ("🥖 Padaria", "padaria"),
    ("🏦 Banco", "banco"), ("💳 Caixa Eletrônico", "atm"),
    ("⛽ Posto", "posto"), ("🏨 Hotel", "hotel"),
    ("🏫 Escola", "escola"), ("🎓 Universidade", "universidade"),
    ("💪 Academia", "academia"), ("🌳 Parque", "parque"),
    ("🎬 Cinema", "cinema"), ("🛍️ Shopping", "shopping"),
    ("💇 Salão de Beleza", "salao"), ("🔧 Mecânica", "mecanica"),
    ("🚓 Delegacia", "delegacia"), ("📮 Correios", "correios"),
]

TAGS_MAP = {
    "restaurante":  ['node["amenity"="restaurant"]', 'way["amenity"="restaurant"]'],
    "pizzaria":     ['node["amenity"="restaurant"]["cuisine"~"pizza"]', 'way["amenity"="restaurant"]["cuisine"~"pizza"]'],
    "fast_food":    ['node["amenity"="fast_food"]', 'way["amenity"="fast_food"]'],
    "cafe":         ['node["amenity"="cafe"]', 'way["amenity"="cafe"]'],
    "bar":          ['node["amenity"="bar"]', 'way["amenity"="bar"]'],
    "farmacia":     ['node["amenity"="pharmacy"]', 'way["amenity"="pharmacy"]', 'node["healthcare"="pharmacy"]'],
    "hospital":     ['node["amenity"="hospital"]', 'way["amenity"="hospital"]', 'node["healthcare"="hospital"]'],
    "dentista":     ['node["amenity"="dentist"]', 'way["amenity"="dentist"]'],
    "supermercado": ['node["shop"="supermarket"]', 'way["shop"="supermarket"]'],
    "padaria":      ['node["shop"="bakery"]', 'way["shop"="bakery"]'],
    "banco":        ['node["amenity"="bank"]', 'way["amenity"="bank"]'],
    "atm":          ['node["amenity"="atm"]', 'way["amenity"="atm"]'],
    "posto":        ['node["amenity"="fuel"]', 'way["amenity"="fuel"]'],
    "hotel":        ['node["tourism"="hotel"]', 'way["tourism"="hotel"]'],
    "escola":       ['node["amenity"="school"]', 'way["amenity"="school"]'],
    "universidade": ['node["amenity"="university"]', 'way["amenity"="university"]', 'node["amenity"="college"]'],
    "academia":     ['node["leisure"="fitness_centre"]', 'way["leisure"="fitness_centre"]', 'node["leisure"="sports_centre"]'],
    "parque":       ['node["leisure"="park"]', 'way["leisure"="park"]'],
    "cinema":       ['node["amenity"="cinema"]', 'way["amenity"="cinema"]'],
    "shopping":     ['node["shop"="mall"]', 'way["shop"="mall"]'],
    "salao":        ['node["shop"="beauty"]', 'way["shop"="beauty"]', 'node["shop"="hairdresser"]'],
    "mecanica":     ['node["shop"="car_repair"]', 'way["shop"="car_repair"]'],
    "delegacia":    ['node["amenity"="police"]', 'way["amenity"="police"]'],
    "correios":     ['node["amenity"="post_office"]', 'way["amenity"="post_office"]'],
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

NOMES_ESTADOS = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo",
    "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina",
    "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins",
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


async def geocodificar_multiplos(consulta: str, limite: int = 8):
    """Retorna VÁRIOS lugares para uma consulta (cidades, endereços, etc)."""
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            r = await http.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": consulta if "brasil" in consulta.lower() else f"{consulta}, Brasil",
                    "format": "json",
                    "limit": limite,
                    "countrycodes": "br",
                    "addressdetails": 1,
                },
                headers={"User-Agent": "Brasilmaid/1.0 (contato@brasilmaid.app)"},
            )
            if r.status_code == 200:
                resultados = []
                for d in r.json():
                    addr = d.get("address", {})
                    # Tenta descobrir a cidade
                    cidade = (
                        addr.get("city") or addr.get("town") or
                        addr.get("village") or addr.get("municipality") or
                        d.get("display_name", "").split(",")[0]
                    )
                    uf_code = addr.get("ISO3166-2-lvl4", "")  # ex: BR-PR
                    uf = uf_code.replace("BR-", "") if uf_code else ""
                    if not uf:
                        uf = addr.get("state", "")[:2].upper() if addr.get("state") else ""

                    resultados.append({
                        "lat": float(d["lat"]),
                        "lon": float(d["lon"]),
                        "display": d.get("display_name", ""),
                        "cidade": cidade,
                        "uf": uf,
                    })
                return resultados
    except Exception as e:
        logger.error(f"Erro geo: {e}")
    return []


async def geocodificar(consulta: str):
    """Retorna o melhor match único."""
    res = await geocodificar_multiplos(consulta, limite=1)
    return res[0] if res else None


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


async def overpass_query(query: str):
    for mirror in OVERPASS_MIRRORS:
        try:
            async with httpx.AsyncClient(timeout=45) as http:
                r = await http.post(
                    mirror, data={"data": query},
                    headers={"User-Agent": "Brasilmaid/1.0"},
                )
                if r.status_code == 200:
                    logger.info(f"✅ Overpass via {mirror}")
                    return r.json()
                logger.warning(f"⚠️ {mirror} → {r.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ {mirror} falhou: {e}")
        await asyncio.sleep(1)
    return None


async def buscar_lugares(lat, lon, categoria, raio=50000):
    tags = TAGS_MAP.get(categoria, TAGS_MAP["restaurante"])
    partes = "\n".join(f"  {t}(around:{raio},{lat},{lon});" for t in tags)
    query = f"""
[out:json][timeout:40];
(
{partes}
);
out center 60;
    """.strip()

    logger.info(f"🔎 Overpass ({categoria}, raio={raio}m)")
    data = await overpass_query(query)
    if not data:
        return []

    lugares = []
    for el in data.get("elements", []):
        nome = el.get("tags", {}).get("name")
        if not nome:
            continue
        el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        el_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if el_lat and el_lon:
            lugares.append({
                "nome": nome, "lat": el_lat, "lon": el_lon,
                "dist": calcular_distancia(lat, lon, el_lat, el_lon),
            })

    vistos = set()
    unicos = []
    for l in sorted(lugares, key=lambda x: x["dist"]):
        chave = l["nome"].lower().strip()
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(l)
    return unicos


# ─────────────────────────────────────────
# ROTAS (OSRM)
# ─────────────────────────────────────────
async def calcular_rota(lat1, lon1, lat2, lon2):
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.get(url, params={"overview": "false", "steps": "true"})
            if r.status_code == 200:
                data = r.json()
                if data.get("routes"):
                    rota = data["routes"][0]
                    passos = []
                    for leg in rota.get("legs", []):
                        for step in leg.get("steps", []):
                            manobra = step.get("maneuver", {})
                            tipo = manobra.get("type", "")
                            modifier = manobra.get("modifier", "")
                            nome_rua = step.get("name", "")
                            dist = step.get("distance", 0)

                            if tipo == "depart":
                                txt = f"🚗 Parta pela {nome_rua or 'via'}"
                            elif tipo == "arrive":
                                txt = "🏁 Chegue ao destino"
                            else:
                                acao = {
                                    "turn": "Vire", "new name": "Continue",
                                    "continue": "Siga", "merge": "Entre",
                                    "fork": "Pegue", "roundabout": "Na rotatória, saia",
                                    "end of road": "No fim da rua, vire",
                                }.get(tipo, "Siga")
                                mod = {
                                    "left": "à esquerda", "right": "à direita",
                                    "slight left": "levemente à esquerda",
                                    "slight right": "levemente à direita",
                                    "sharp left": "acentuadamente à esquerda",
                                    "sharp right": "acentuadamente à direita",
                                    "straight": "em frente", "uturn": "retorne",
                                }.get(modifier, "")
                                txt = f"➡️ {acao} {mod}".strip()
                                if nome_rua:
                                    txt += f" na {nome_rua}"

                            passos.append({"texto": txt, "dist": dist})

                    return {
                        "distancia": rota.get("distance", 0),
                        "duracao": rota.get("duration", 0),
                        "passos": passos,
                    }
    except Exception as e:
        logger.error(f"Erro OSRM: {e}")
    return None


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
                        "Você classifica mensagens em português. Retorne APENAS um JSON.\n\n"
                        "Tipos possíveis:\n"
                        '{"tipo": "cep", "valor": "12345678"}\n'
                        '{"tipo": "estado", "valor": "Paraná"}  // nome de estado brasileiro\n'
                        '{"tipo": "cidade", "valor": "Sarandi"}  // QUALQUER nome de cidade/município/bairro/rua\n'
                        '{"tipo": "categoria", "valor": "farmacia"}\n'
                        '{"tipo": "rota", "origem": "...", "destino": "..."}\n'
                        '{"tipo": "desconhecido", "valor": ""}\n\n'
                        "REGRAS IMPORTANTES:\n"
                        "- Se o texto parece ser um NOME DE LUGAR (cidade, bairro, rua, endereço), "
                        "classifique como 'cidade'. Ex: 'Sarandi', 'Curitiba', 'Rua das Flores'.\n"
                        "- Só use 'categoria' quando o usuário claramente quer um TIPO de lugar "
                        "(ex: 'onde tem farmácia?', 'quero um restaurante').\n"
                        "- Categorias válidas: restaurante, pizzaria, fast_food, cafe, bar, farmacia, "
                        "hospital, dentista, supermercado, padaria, banco, atm, posto, hotel, escola, "
                        "universidade, academia, parque, cinema, shopping, salao, mecanica, delegacia, correios.\n"
                        "- Na dúvida entre 'cidade' e 'desconhecido', escolha 'cidade'."
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
# UI
# ─────────────────────────────────────────
async def enviar_categorias(bot, chat_id):
    keyboard = []
    for i in range(0, len(CATEGORIAS), 2):
        row = [
            InlineKeyboardButton(nome, callback_data=f"cat_{valor}")
            for nome, valor in CATEGORIAS[i:i + 2]
        ]
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(
        "🚗 Traçar rota (estou em X, quero ir pra Y)", callback_data="rota_info"
    )])
    await bot.send_message(
        chat_id=chat_id,
        text="🔍 <b>O que você procura perto de você?</b>\n\n<i>Escolha uma categoria ou peça uma rota:</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def enviar_cidades_uf(bot, chat_id, context, editar_msg=None):
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
            InlineKeyboardButton(cidade, callback_data=f"ciduf_{cidade}")
            for cidade in page_cities[i:i + 2]
        ]
        keyboard.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data="cidprev"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("Próximas ➡️", callback_data="cidnext"))
    if nav:
        keyboard.append(nav)

    texto = f"🏙️ <b>Cidades do {estado}</b>\n\nPágina {page + 1}/{total_pages}:"

    if editar_msg:
        await editar_msg.edit_text(texto, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await bot.send_message(chat_id, texto, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def enviar_opcoes_cidade(bot, chat_id, context, consulta: str, editar_msg=None):
    """Busca múltiplos matches de cidade e mostra em botões."""
    resultados = await geocodificar_multiplos(consulta, limite=8)

    if not resultados:
        msg = (
            f"❌ Não encontrei nenhum lugar chamado <b>{html.escape(consulta)}</b>.\n\n"
            "Tente escrever com mais detalhes, ex: <i>Sarandi, Paraná</i>"
        )
        if editar_msg:
            await editar_msg.edit_text(msg, parse_mode="HTML")
        else:
            await bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    # Salva os resultados para o callback
    context.user_data["opcoes_cidade"] = resultados

    keyboard = []
    for i, r in enumerate(resultados):
        cidade = r["cidade"]
        uf = r["uf"]
        # Tenta pegar o estado completo
        estado_nome = NOMES_ESTADOS.get(uf, uf)
        # Se display tem mais info (bairro, região), mostra um pedaço
        partes = [p.strip() for p in r["display"].split(",")]
        extra = ""
        if len(partes) > 2:
            extra = f" ({partes[1]})"

        label = f"📍 {cidade} — {estado_nome}{extra}"
        # Limita a 60 chars
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"escolhacid_{i}")])

    texto = (
        f"🔎 Encontrei <b>{len(resultados)}</b> lugares para <b>{html.escape(consulta)}</b>:\n\n"
        "Escolha o correto:"
    )

    if editar_msg:
        await editar_msg.edit_text(texto, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await bot.send_message(chat_id, texto, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def buscar_e_mostrar(bot, chat_id, context, categoria):
    lat = context.user_data.get("lat")
    lon = context.user_data.get("lon")
    if not lat or not lon:
        await bot.send_message(chat_id, "❌ Localização não definida. Use /start.")
        return

    msg = await bot.send_message(
        chat_id,
        f"🔎 <b>Buscando {categoria.title()}...</b>\n\n<i>Consultando mapa (pode levar 10-20s)...</i>",
        parse_mode="HTML",
    )

    lugares = await buscar_lugares(lat, lon, categoria, raio=50000)

    if not lugares:
        await msg.edit_text(
            f"🔎 <b>Buscando {categoria.title()}...</b>\n\n<i>Ampliando raio para 100 km...</i>",
            parse_mode="HTML",
        )
        lugares = await buscar_lugares(lat, lon, categoria, raio=100000)

    if not lugares:
        cat_q = categoria.replace("_", " ")
        gmaps_url = f"https://www.google.com/maps/search/{cat_q}/@{lat},{lon},12z"
        await msg.edit_text(
            f"😕 Não achei <b>{categoria.title()}</b> no OpenStreetMap num raio de 100 km.\n\n"
            f"O OSM tem menos lugares cadastrados que o Google. Veja no Google Maps:\n\n"
            f'🗺️ <a href="{gmaps_url}">Buscar {categoria.title()} no Google Maps</a>',
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    top = lugares[:8]
    texto = f"📍 <b>{categoria.title()} mais próximos</b>\n\n"
    for i, lugar in enumerate(top, 1):
        texto += f"<b>{i}. {html.escape(lugar['nome'])}</b>\n   📏 {formatar_dist(lugar['dist'])}\n\n"

    keyboard = []
    for i, lugar in enumerate(top[:5], 1):
        nome_curto = lugar["nome"][:28]
        keyboard.append([InlineKeyboardButton(
            f"🧭 Rota até {i}. {nome_curto}",
            url=f"https://www.google.com/maps/dir/?api=1&destination={lugar['lat']},{lugar['lon']}&travelmode=driving"
        )])
    keyboard.append([InlineKeyboardButton("🔄 Nova busca", callback_data="nova_busca")])

    await msg.edit_text(texto, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def mostrar_rota(bot, chat_id, origem_txt, destino_txt):
    msg = await bot.send_message(
        chat_id,
        f"🗺️ <b>Calculando rota...</b>\n\n"
        f"📍 De: {html.escape(origem_txt)}\n"
        f"🏁 Para: {html.escape(destino_txt)}",
        parse_mode="HTML",
    )

    origem = await geocodificar(origem_txt)
    if not origem:
        await msg.edit_text(f"❌ Não encontrei: <b>{html.escape(origem_txt)}</b>", parse_mode="HTML")
        return

    destino = await geocodificar(destino_txt)
    if not destino:
        await msg.edit_text(f"❌ Não encontrei: <b>{html.escape(destino_txt)}</b>", parse_mode="HTML")
        return

    rota = await calcular_rota(origem["lat"], origem["lon"], destino["lat"], destino["lon"])
    if not rota:
        await msg.edit_text("❌ Não consegui calcular a rota. Tente novamente.")
        return

    dist_km = rota["distancia"] / 1000
    dur_min = int(rota["duracao"] / 60)

    texto = (
        f"🗺️ <b>Rota calculada!</b>\n\n"
        f"📍 <b>Origem:</b> {html.escape(origem['display'][:80])}\n"
        f"🏁 <b>Destino:</b> {html.escape(destino['display'][:80])}\n\n"
        f"📏 <b>Distância:</b> {dist_km:.1f} km\n"
        f"⏱️ <b>Tempo:</b> {dur_min} min\n\n"
        f"<b>🧭 Passo a passo:</b>\n"
    )

    for i, p in enumerate(rota["passos"][:12], 1):
        d = formatar_dist(int(p["dist"]))
        texto += f"{i}. {p['texto']} <i>({d})</i>\n"

    if len(rota["passos"]) > 12:
        texto += f"\n<i>... +{len(rota['passos']) - 12} passos. Abra no Google Maps.</i>"

    gmaps_url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={origem['lat']},{origem['lon']}"
        f"&destination={destino['lat']},{destino['lon']}"
        f"&travelmode=driving"
    )

    keyboard = [
        [InlineKeyboardButton("🧭 Abrir no Google Maps", url=gmaps_url)],
        [InlineKeyboardButton("🔄 Nova busca", callback_data="nova_busca")],
    ]

    await msg.edit_text(
        texto, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


# ─────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [
        [KeyboardButton("📍 Compartilhar minha localização", request_location=True)],
        [KeyboardButton("📮 Digitar CEP"), KeyboardButton("🏙️ Digitar cidade/estado")],
        [KeyboardButton("🚗 Traçar rota")],
    ]
    await update.message.reply_text(
        "👋 <b>Olá! Sou o Brasilmaid!</b>\n\n"
        "Encontro lugares perto de você e traço rotas.\n\n"
        "<b>Como quer começar?</b>\n\n"
        "📍 Compartilhe sua localização\n"
        "📮 Digite seu CEP\n"
        "🏙️ Digite cidade ou estado (ex: <i>Sarandi</i>)\n"
        "🚗 Peça uma rota",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    context.user_data["lat"] = loc.latitude
    context.user_data["lon"] = loc.longitude
    context.user_data["esperando"] = None
    await update.message.reply_text("✅ <b>Localização recebida!</b>", parse_mode="HTML")
    await enviar_categorias(context.bot, update.effective_chat.id)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (update.message.text or "").strip()

    # Botões do teclado
    if texto == "📮 Digitar CEP":
        context.user_data["esperando"] = "cep"
        await update.message.reply_text("📮 <b>Digite seu CEP</b> (8 números):", parse_mode="HTML")
        return

    if texto == "🏙️ Digitar cidade/estado":
        context.user_data["esperando"] = "cidade"
        await update.message.reply_text(
            "🏙️ <b>Digite o nome da cidade, bairro ou estado:</b>\n\n"
            "<i>Ex: Sarandi, Curitiba, Paraná...</i>",
            parse_mode="HTML",
        )
        return

    if texto == "🚗 Traçar rota":
        context.user_data["esperando"] = "rota"
        await update.message.reply_text(
            "🚗 <b>Vamos traçar uma rota!</b>\n\n"
            "Me diga: <i>'estou em [endereço] e quero ir para [endereço]'</i>\n\n"
            "Ex: <i>estou na Rua Lírio dos Santos, Sarandi-PR, e quero ir para a Av. Paulista, São Paulo</i>",
            parse_mode="HTML",
        )
        return

    esperando = context.user_data.get("esperando")

    # ── CEP ──
    if esperando == "cep":
        resultado = await buscar_cep(texto)
        if resultado and resultado["lat"]:
            context.user_data.update({
                "lat": resultado["lat"], "lon": resultado["lon"], "esperando": None,
                "cidade_atual": resultado["cidade"], "uf_atual": resultado["uf"],
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

    # ── Cidade / Estado ──
    if esperando == "cidade":
        info = await interpretar_com_ia(texto)
        tipo = info.get("tipo")
        valor = info.get("valor", "") or texto  # fallback: usa o texto cru

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
                "cidades": cidades, "cidades_page": 0,
                "estado": valor.title(), "esperando": None,
            })
            await enviar_cidades_uf(context.bot, update.effective_chat.id, context)
            return

        # Se é cidade OU desconhecido → tenta geocodificar direto
        # Isso resolve o caso "Sarandi" que a IA não reconhecia
        await update.message.reply_text("🔎 Procurando...")
        context.user_data["esperando"] = None
        await enviar_opcoes_cidade(context.bot, update.effective_chat.id, context, valor)
        return

    # ── Rota ──
    if esperando == "rota":
        info = await interpretar_com_ia(texto)
        if info.get("tipo") == "rota" and info.get("origem") and info.get("destino"):
            context.user_data["esperando"] = None
            await mostrar_rota(
                context.bot, update.effective_chat.id,
                info["origem"], info["destino"],
            )
            return
        # Fallback: tenta separar por "para", "até", "->"
        partes = None
        for sep in [" para ", " até ", " -> ", " e quero ir para "]:
            if sep in texto.lower():
                partes = texto.lower().split(sep, 1)
                break
        if partes:
            context.user_data["esperando"] = None
            await mostrar_rota(context.bot, update.effective_chat.id, partes[0], partes[1])
            return

        await update.message.reply_text(
            "❌ Não entendi a rota. Tente: <i>'estou em X e quero ir para Y'</i>",
            parse_mode="HTML",
        )
        return

    # ── Texto livre (sem estado "esperando") ──
    if context.user_data.get("lat"):
        info = await interpretar_com_ia(texto)
        if info.get("tipo") == "categoria":
            await buscar_e_mostrar(context.bot, update.effective_chat.id, context, info["valor"])
        elif info.get("tipo") == "rota":
            context.user_data["esperando"] = None
            await mostrar_rota(
                context.bot, update.effective_chat.id,
                info["origem"], info["destino"],
            )
        else:
            await enviar_categorias(context.bot, update.effective_chat.id)
        return

    # Sem localização + texto livre: tenta como cidade
    info = await interpretar_com_ia(texto)

    if info.get("tipo") == "cidade":
        await enviar_opcoes_cidade(context.bot, update.effective_chat.id, context, info["valor"])

    elif info.get("tipo") == "cep":
        resultado = await buscar_cep(info["valor"])
        if resultado and resultado["lat"]:
            context.user_data.update({
                "lat": resultado["lat"], "lon": resultado["lon"],
                "cidade_atual": resultado["cidade"], "uf_atual": resultado["uf"],
            })
            await update.message.reply_text(
                f"✅ {resultado['cidade']} - {resultado['uf']}", parse_mode="HTML"
            )
            await enviar_categorias(context.bot, update.effective_chat.id)

    elif info.get("tipo") == "estado":
        uf = ESTADOS.get(info["valor"].lower())
        if uf:
            cidades = await listar_cidades_uf(uf)
            if cidades:
                context.user_data.update({
                    "cidades": cidades, "cidades_page": 0, "estado": info["valor"].title(),
                })
                await enviar_cidades_uf(context.bot, update.effective_chat.id, context)
                return
        await update.message.reply_text("❌ Estado não reconhecido. Tente novamente.")

    elif info.get("tipo") == "categoria" and info.get("valor"):
        # Usuário pediu categoria sem ter localização
        await update.message.reply_text(
            f"Entendi! Você quer um <b>{info['valor'].title()}</b>.\n\n"
            "Mas primeiro preciso da sua localização. Use /start.",
            parse_mode="HTML",
        )

    else:
        # Último recurso: tenta como cidade mesmo assim
        await enviar_opcoes_cidade(context.bot, update.effective_chat.id, context, texto)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Paginação das cidades do estado
    if data == "cidnext":
        context.user_data["cidades_page"] = context.user_data.get("cidades_page", 0) + 1
        await enviar_cidades_uf(context.bot, query.message.chat_id, context, editar_msg=query)
        return
    if data == "cidprev":
        context.user_data["cidades_page"] = max(0, context.user_data.get("cidades_page", 0) - 1)
        await enviar_cidades_uf(context.bot, query.message.chat_id, context, editar_msg=query)
        return

    # Cidade do estado selecionada (nome + estado já conhecido)
    if data.startswith("ciduf_"):
        cidade = data.replace("ciduf_", "")
        estado = context.user_data.get("estado", "")
        await query.edit_message_text(
            f"🔎 Localizando <b>{html.escape(cidade)}</b>...", parse_mode="HTML"
        )
        coords = await geocodificar(f"{cidade}, {estado}, Brasil")
        if coords:
            context.user_data.update({
                "lat": coords["lat"], "lon": coords["lon"],
                "cidade_atual": cidade, "estado_atual": estado,
            })
            await query.edit_message_text(
                f"📍 <b>{html.escape(cidade)} — {html.escape(estado)}</b>",
                parse_mode="HTML",
            )
            await enviar_categorias(context.bot, query.message.chat_id)
        else:
            await query.edit_message_text("❌ Não encontrei essa cidade.")
        return

    # Escolha de cidade (múltiplas opções)
    if data.startswith("escolhacid_"):
        idx = int(data.replace("escolhacid_", ""))
        opcoes = context.user_data.get("opcoes_cidade", [])
        if idx >= len(opcoes):
            await query.edit_message_text("❌ Opção inválida. Tente novamente.")
            return

        escolhida = opcoes[idx]
        context.user_data.update({
            "lat": escolhida["lat"],
            "lon": escolhida["lon"],
            "cidade_atual": escolhida["cidade"],
            "estado_atual": escolhida["uf"],
            "esperando": None,
        })

        await query.edit_message_text(
            f"📍 <b>{html.escape(escolhida['cidade'])} — {html.escape(NOMES_ESTADOS.get(escolhida['uf'], escolhida['uf']))}</b>",
            parse_mode="HTML",
        )
        await enviar_categorias(context.bot, query.message.chat_id)
        return

    # Categoria selecionada
    if data.startswith("cat_"):
        categoria = data.replace("cat_", "")
        await query.edit_message_text(
            f"🔎 Buscando <b>{categoria.title()}</b>...", parse_mode="HTML"
        )
        await buscar_e_mostrar(context.bot, query.message.chat_id, context, categoria)
        return

    # Info de rota
    if data == "rota_info":
        await query.edit_message_text(
            "🚗 <b>Como traçar uma rota:</b>\n\n"
            "Envie uma mensagem como:\n\n"
            "<i>'estou na Rua Lírio dos Santos, Sarandi-PR, "
            "e quero ir para a Av. Paulista, São Paulo'</i>\n\n"
            "Ou toque em <b>🚗 Traçar rota</b> no teclado e depois digite.",
            parse_mode="HTML",
        )
        return

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
        update = Update.de_json(data, application.bot)
        await application.update_queue.put(update)
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

starlette_app = Starlette(
    routes=routes,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)


if __name__ == "__main__":
    logger.info(f"🚀 Iniciando na porta {PORT}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
