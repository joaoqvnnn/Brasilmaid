import os
import re
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
    "https://overpass.osm.ch/api/interpreter",
]

# ─────────────────────────────────────────
# ESTADOS DE NAVEGAÇÃO
# ─────────────────────────────────────────
ESTADO_INICIO = "inicio"
ESTADO_CATEGORIAS = "categorias"
ESTADO_RESULTADOS = "resultados"
ESTADO_ROTA_INPUT = "rota_input"

# ─────────────────────────────────────────
# CATEGORIAS
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
    "restaurante":  ['nwr["amenity"="restaurant"]'],
    "pizzaria":     ['nwr["amenity"="restaurant"]["cuisine"~"pizza"]'],
    "fast_food":    ['nwr["amenity"="fast_food"]'],
    "cafe":         ['nwr["amenity"="cafe"]'],
    "bar":          ['nwr["amenity"="bar"]'],
    "farmacia":     ['nwr["amenity"="pharmacy"]', 'nwr["healthcare"="pharmacy"]'],
    "hospital":     ['nwr["amenity"="hospital"]', 'nwr["healthcare"="hospital"]'],
    "dentista":     ['nwr["amenity"="dentist"]'],
    "supermercado": ['nwr["shop"="supermarket"]'],
    "padaria":      ['nwr["shop"="bakery"]'],
    "banco":        ['nwr["amenity"="bank"]'],
    "atm":          ['nwr["amenity"="atm"]'],
    "posto":        ['nwr["amenity"="fuel"]'],
    "hotel":        ['nwr["tourism"="hotel"]'],
    "escola":       ['nwr["amenity"="school"]', 'nwr["amenity"="kindergarten"]'],
    "universidade": ['nwr["amenity"="university"]', 'nwr["amenity"="college"]'],
    "academia":     ['nwr["leisure"="fitness_centre"]', 'nwr["leisure"="sports_centre"]'],
    "parque":       ['nwr["leisure"="park"]'],
    "cinema":       ['nwr["amenity"="cinema"]'],
    "shopping":     ['nwr["shop"="mall"]'],
    "salao":        ['nwr["shop"="beauty"]', 'nwr["shop"="hairdresser"]'],
    "mecanica":     ['nwr["shop"="car_repair"]'],
    "delegacia":    ['nwr["amenity"="police"]'],
    "correios":     ['nwr["amenity"="post_office"]'],
}

NOMES_AMIGAVEIS = {
    "restaurante": "Restaurantes", "pizzaria": "Pizzarias", "fast_food": "Lanchonetes",
    "cafe": "Cafés", "bar": "Bares", "farmacia": "Farmácias", "hospital": "Hospitais",
    "dentista": "Dentistas", "supermercado": "Supermercados", "padaria": "Padarias",
    "banco": "Bancos", "atm": "Caixas Eletrônicos", "posto": "Postos",
    "hotel": "Hotéis", "escola": "Escolas", "universidade": "Universidades",
    "academia": "Academias", "parque": "Parques", "cinema": "Cinemas",
    "shopping": "Shoppings", "salao": "Salões de Beleza", "mecanica": "Mecânicas",
    "delegacia": "Delegacias", "correios": "Correios",
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
    return f"{m} m" if m < 1000 else f"{m/1000:.1f} km"


def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return int(R * 2 * atan2(sqrt(a), sqrt(1 - a)))


# ─────────────────────────────────────────
# GERENCIAMENTO DE MENSAGENS (limpeza automática)
# ─────────────────────────────────────────
async def limpar(bot, chat_id, context, apagar_user: bool = True):
    """Apaga as mensagens antigas para não acumular."""
    ids = context.user_data.get("_msg_ids", [])
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass
    context.user_data["_msg_ids"] = []

    if apagar_user:
        uids = context.user_data.get("_user_msg_ids", [])
        for mid in uids:
            try:
                await bot.delete_message(chat_id, mid)
            except Exception:
                pass
        context.user_data["_user_msg_ids"] = []


def rastrear_msg(context, msg_id):
    """Guarda o ID de uma mensagem enviada pelo bot."""
    ids = context.user_data.setdefault("_msg_ids", [])
    ids.append(msg_id)


def rastrear_user(context, msg_id):
    """Guarda o ID de uma mensagem enviada pelo usuário."""
    ids = context.user_data.setdefault("_user_msg_ids", [])
    ids.append(msg_id)


async def enviar(bot, chat_id, context, texto, reply_markup=None, parse_mode="HTML",
                 disable_preview=True, limpar_antes=False):
    """Envia mensagem rastreando o ID. Opcionalmente limpa as anteriores."""
    if limpar_antes:
        await limpar(bot, chat_id, context)
    msg = await bot.send_message(
        chat_id, texto, parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_preview,
    )
    rastrear_msg(context, msg.message_id)
    return msg


# ─────────────────────────────────────────
# BOTÃO VOLTAR UNIVERSAL
# ─────────────────────────────────────────
async def voltar_para_categorias(bot, chat_id, context):
    """Volta ao menu de categorias."""
    await limpar(bot, chat_id, context)
    context.user_data["estado"] = ESTADO_CATEGORIAS
    await enviar_categorias(bot, chat_id, context)


async def voltar_para_inicio(bot, chat_id, context):
    """Volta ao menu inicial."""
    await limpar(bot, chat_id, context)
    context.user_data["estado"] = ESTADO_INICIO
    await enviar_inicio(bot, chat_id, context)


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
    """Geocodifica com contexto implícito do Brasil."""
    for q in [consulta if "brasil" in consulta.lower() else f"{consulta}, Brasil", consulta]:
        try:
            async with httpx.AsyncClient(timeout=12) as http:
                r = await http.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": q, "format": "json", "limit": limite,
                        "countrycodes": "br", "addressdetails": 1,
                    },
                    headers={"User-Agent": "Brasilmaid/1.0"},
                )
                if r.status_code == 200 and r.json():
                    resultados = []
                    for d in r.json():
                        addr = d.get("address", {})
                        cidade = (
                            addr.get("city") or addr.get("town") or
                            addr.get("village") or addr.get("municipality") or
                            addr.get("suburb") or d.get("display_name", "").split(",")[0]
                        )
                        uf_code = addr.get("ISO3166-2-lvl4", "")
                        uf = uf_code.replace("BR-", "") if uf_code else ""
                        if not uf and addr.get("state"):
                            uf = addr["state"][:2].upper()

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
    res = await geocodificar_multiplos(consulta, limite=1)
    return res[0] if res else None


async def geocodificar_com_contexto(consulta: str, cidade_ctx: str = None, uf_ctx: str = None):
    """Tenta geocodificar adicionando a cidade/UF como contexto."""
    if cidade_ctx:
        sufixo = f"{cidade_ctx}"
        if uf_ctx:
            sufixo += f", {uf_ctx}"
        # Tenta 1: consulta + cidade + UF
        res = await geocodificar_multiplos(f"{consulta}, {sufixo}, Brasil", limite=1)
        if res:
            return res[0]
        # Tenta 2: consulta + cidade
        res = await geocodificar_multiplos(f"{consulta}, {cidade_ctx}, Brasil", limite=1)
        if res:
            return res[0]
    # Tenta 3: consulta sozinha
    return await geocodificar(consulta)


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
    """Tenta cada espelho com retry."""
    for tentativa in range(2):
        for mirror in OVERPASS_MIRRORS:
            try:
                async with httpx.AsyncClient(timeout=50) as http:
                    r = await http.post(
                        mirror, data={"data": query},
                        headers={"User-Agent": "Brasilmaid/1.0"},
                    )
                    if r.status_code == 200:
                        logger.info(f"✅ Overpass via {mirror}")
                        return r.json()
                    logger.warning(f"⚠️ {mirror} → {r.status_code}")
            except Exception as e:
                logger.warning(f"⚠️ {mirror} falhou: {str(e)[:80]}")
            await asyncio.sleep(0.8)
        await asyncio.sleep(1.5)
    return None


async def buscar_lugares(lat, lon, categoria, raio=50000):
    tags = TAGS_MAP.get(categoria, ['nwr["amenity"="restaurant"]'])
    partes = "\n".join(f"  {t}(around:{raio},{lat},{lon});" for t in tags)
    query = f"""
[out:json][timeout:45];
(
{partes}
);
out center 80;
    """.strip()

    logger.info(f"🔎 Overpass ({categoria}, raio={raio}m)")
    data = await overpass_query(query)
    if not data:
        return []

    elementos = data.get("elements", [])
    logger.info(f"📦 {len(elementos)} elementos")

    lugares = []
    for el in elementos:
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
# ROTAS
# ─────────────────────────────────────────
async def calcular_rota(lat1, lon1, lat2, lon2):
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
        async with httpx.AsyncClient(timeout=25) as http:
            r = await http.get(url, params={"overview": "false", "steps": "true"})
            if r.status_code == 200:
                data = r.json()
                if data.get("routes"):
                    rota = data["routes"][0]
                    passos = []
                    for leg in rota.get("legs", []):
                        for step in leg.get("steps", []):
                            m = step.get("maneuver", {})
                            tipo = m.get("type", "")
                            modifier = m.get("modifier", "")
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
# DETECÇÃO LOCAL DE ROTA
# ─────────────────────────────────────────
PADROES_ROTA = [
    r"estou\s+(?:em|na|no)\s+(.+?)\s+(?:e\s+)?(?:quero|queria|preciso|gostaria)\s+(?:ir|chegar)\s+(?:para|pra|pro|no|na|ao|à|a)\s+(.+)",
    r"estou\s+(?:em|na|no)\s+(.+?)[,;]\s*(?:quero|queria|preciso)\s+(?:ir|chegar)\s+(?:para|pra|pro|no|na|ao|à|a)\s+(.+)",
    r"(?:de|saindo de)\s+(.+?)\s+(?:para|pra|pro|até|ao|à|a)\s+(.+)",
    r"(?:ir|chegar|sair)\s+(?:de|do|da)\s+(.+?)\s+(?:para|pra|pro|até|ao|à|a)\s+(.+)",
    r"como\s+(?:chego|vou)\s+(?:de|do|da)\s+(.+?)\s+(?:a|ao|à|para|pra|até)\s+(.+)",
    r"^(.+?)\s+(?:até|->|→)\s+(.+)$",
]


def detectar_rota_local(texto: str):
    t = texto.strip().lower()
    for p in PADROES_ROTA:
        m = re.search(p, t)
        if m:
            o = m.group(1).strip(" .,;:!?")
            d = m.group(2).strip(" .,:;!?")
            if len(o) > 3 and len(d) > 3:
                return o, d
    return None


# ─────────────────────────────────────────
# IA
# ─────────────────────────────────────────
PROMPT_IA = """Você classifica mensagens em português brasileiro. Retorne APENAS um JSON.

TIPOS:
1. CEP: {"tipo": "cep", "valor": "87708721"}
2. ESTADO: {"tipo": "estado", "valor": "Paraná"}
3. CIDADE/bairro/rua: {"tipo": "cidade", "valor": "Sarandi"}
4. CATEGORIA: {"tipo": "categoria", "valor": "farmacia"}
5. ROTA: {"tipo": "rota", "origem": "...", "destino": "..."}
6. DESCONHECIDO: {"tipo": "desconhecido", "valor": ""}

ROTA - regras:
- SEMPRE use "rota" se menciona DOIS lugares + intenção de ir de um ao outro.
- Palavras-chave: "estou em", "quero ir", "queria ir", "como chego", "de X para Y", "até".
- Ex: "estou na rua X quero ir no centro de Paranavai" → {"tipo": "rota", "origem": "rua X", "destino": "centro de Paranavai"}
- Se um lado menciona cidade, inclua ela no outro lado para contexto.

Categorias válidas: restaurante, pizzaria, fast_food, cafe, bar, farmacia, hospital, dentista, supermercado, padaria, banco, atm, posto, hotel, escola, universidade, academia, parque, cinema, shopping, salao, mecanica, delegacia, correios.

Na dúvida entre cidade e desconhecido, escolha "cidade".
"""


async def interpretar_com_ia(texto: str) -> dict:
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": PROMPT_IA},
                {"role": "user", "content": texto},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        res = json.loads(r.choices[0].message.content)
        logger.info(f"🧠 IA: {res}")
        return res
    except Exception as e:
        logger.error(f"Erro IA: {e}")
        return {"tipo": "desconhecido", "valor": ""}


# ─────────────────────────────────────────
# UI - TELAS
# ─────────────────────────────────────────
def teclado_principal():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📍 Compartilhar localização", request_location=True)],
            [KeyboardButton("📮 Digitar CEP"), KeyboardButton("🏙️ Cidade/estado")],
            [KeyboardButton("🚗 Traçar rota"), KeyboardButton("🔄 Reiniciar")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def botao_voltar(destino: str):
    return InlineKeyboardButton("⬅️ Voltar", callback_data=f"voltar_{destino}")


async def enviar_inicio(bot, chat_id, context):
    context.user_data["estado"] = ESTADO_INICIO
    texto = (
        "👋 <b>Olá! Sou o Brasilmaid!</b>\n\n"
        "Encontro lugares perto de você e traço rotas.\n\n"
        "<b>Como quer começar?</b>\n\n"
        "📍 Compartilhe sua localização\n"
        "📮 Digite seu CEP\n"
        "🏙️ Digite cidade ou estado\n"
        "🚗 Peça uma rota"
    )
    await enviar(bot, chat_id, context, texto,
                 reply_markup=teclado_principal(), limpar_antes=True)


async def enviar_categorias(bot, chat_id, context):
    context.user_data["estado"] = ESTADO_CATEGORIAS
    cidade = context.user_data.get("cidade_atual", "")
    uf = context.user_data.get("uf_atual", "")
    local = f"{cidade} - {uf}" if cidade else "sua região"

    keyboard = []
    for i in range(0, len(CATEGORIAS), 2):
        row = [InlineKeyboardButton(n, callback_data=f"cat_{v}")
               for n, v in CATEGORIAS[i:i + 2]]
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🚗 Traçar rota", callback_data="abrir_rota")])
    keyboard.append([botao_voltar("inicio")])

    await enviar(
        bot, chat_id, context,
        f"📍 <b>Local: {html.escape(local)}</b>\n\n"
        f"🔍 <b>O que você procura por perto?</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        limpar_antes=True,
    )


async def enviar_cidades_uf(bot, chat_id, context):
    cidades = context.user_data.get("cidades", [])
    page = context.user_data.get("cidades_page", 0)
    estado = context.user_data.get("estado_nome", "")

    per_page = 12
    total = len(cidades)
    start = page * per_page
    pg = cidades[start:start + per_page]
    tot_pgs = (total + per_page - 1) // per_page

    keyboard = []
    for i in range(0, len(pg), 2):
        row = [InlineKeyboardButton(c, callback_data=f"ciduf_{c}")
               for c in pg[i:i + 2]]
        keyboard.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Ant.", callback_data="cidprev"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("Próx. ➡️", callback_data="cidnext"))
    if nav:
        keyboard.append(nav)
    keyboard.append([botao_voltar("inicio")])

    await enviar(
        bot, chat_id, context,
        f"🏙️ <b>Cidades do {html.escape(estado)}</b>\n\n"
        f"Página {page + 1}/{tot_pgs} — escolha sua cidade:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        limpar_antes=True,
    )


# ─────────────────────────────────────────
# BUSCA E RESULTADOS
# ─────────────────────────────────────────
async def mostrar_resultados(bot, chat_id, context, categoria):
    lat = context.user_data.get("lat")
    lon = context.user_data.get("lon")
    if not lat or not lon:
        await enviar_inicio(bot, chat_id, context)
        return

    context.user_data["estado"] = ESTADO_RESULTADOS
    context.user_data["ultima_categoria"] = categoria
    nome_amig = NOMES_AMIGAVEIS.get(categoria, categoria.title())

    msg = await enviar(
        bot, chat_id, context,
        f"🔎 <b>Buscando {nome_amig}...</b>\n\n<i>Consultando mapa...</i>",
        limpar_antes=True,
    )

    lugares = await buscar_lugares(lat, lon, categoria, raio=30000)
    if not lugares:
        await msg.edit_text(f"🔎 <b>Ampliando busca (80 km)...</b>", parse_mode="HTML")
        lugares = await buscar_lugares(lat, lon, categoria, raio=80000)
    if not lugares:
        await msg.edit_text(f"🔎 <b>Última tentativa (200 km)...</b>", parse_mode="HTML")
        lugares = await buscar_lugares(lat, lon, categoria, raio=200000)

    if not lugares:
        cat_q = categoria.replace("_", " ")
        gmaps = f"https://www.google.com/maps/search/{cat_q}/@{lat},{lon},12z"
        keyboard = [
            [InlineKeyboardButton("🗺️ Ver no Google Maps", url=gmaps)],
            [botao_voltar("categorias")],
        ]
        await msg.edit_text(
            f"😕 <b>Não encontrei {nome_amig} no OpenStreetMap.</b>\n\n"
            f"O servidor gratuito pode estar instável, ou a região tem poucos dados.\n\n"
            f"Tente novamente em 1 minuto ou veja direto no Google:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    top = lugares[:8]
    texto = f"📍 <b>{nome_amig} mais próximos</b>\n\n"
    for i, l in enumerate(top, 1):
        texto += f"<b>{i}. {html.escape(l['nome'])}</b>\n   📏 {formatar_dist(l['dist'])}\n\n"

    keyboard = []
    for i, l in enumerate(top[:5], 1):
        nome_curto = l["nome"][:26]
        keyboard.append([InlineKeyboardButton(
            f"🧭 {i}. {nome_curto}",
            url=f"https://www.google.com/maps/dir/?api=1&destination={l['lat']},{l['lon']}&travelmode=driving"
        )])
    keyboard.append([
        InlineKeyboardButton("🔄 Atualizar", callback_data=f"cat_{categoria}"),
        botao_voltar("categorias"),
    ])

    await msg.edit_text(texto, parse_mode="HTML",
                       reply_markup=InlineKeyboardMarkup(keyboard),
                       disable_web_page_preview=True)


# ─────────────────────────────────────────
# ROTA COM CONTEXTO
# ─────────────────────────────────────────
async def mostrar_rota(bot, chat_id, context, origem_txt, destino_txt):
    msg = await enviar(
        bot, chat_id, context,
        f"🗺️ <b>Calculando rota...</b>\n\n"
        f"📍 De: <i>{html.escape(origem_txt)}</i>\n"
        f"🏁 Para: <i>{html.escape(destino_txt)}</i>",
        limpar_antes=True,
    )

    # Geocodifica DESTINO primeiro (geralmente tem cidade)
    dest_geo = await geocodificar(destino_txt)
    if not dest_geo:
        await msg.edit_text(
            f"❌ Não encontrei o destino: <b>{html.escape(destino_txt)}</b>\n\n"
            f"Tente incluir a cidade, ex: <i>centro de Paranavaí, PR</i>",
            parse_mode="HTML",
        )
        return

    cidade_ctx = dest_geo.get("cidade")
    uf_ctx = dest_geo.get("uf")

    # Geocodifica ORIGEM usando a cidade do destino como contexto
    orig_geo = await geocodificar_com_contexto(origem_txt, cidade_ctx, uf_ctx)

    # Verifica se a origem ficou muito longe (mais de 500 km) — provavelmente errado
    if orig_geo:
        dist_reta = calcular_distancia(
            orig_geo["lat"], orig_geo["lon"], dest_geo["lat"], dest_geo["lon"]
        )
        if dist_reta > 500_000:  # > 500 km
            # Tenta de novo com contexto forçado
            orig_geo2 = await geocodificar_com_contexto(origem_txt, cidade_ctx, uf_ctx)
            if orig_geo2:
                orig_geo = orig_geo2

    if not orig_geo:
        await msg.edit_text(
            f"❌ Não encontrei a origem: <b>{html.escape(origem_txt)}</b>\n\n"
            f"Tente incluir a cidade, ex: <i>rua X, Paranavaí</i>",
            parse_mode="HTML",
        )
        return

    rota = await calcular_rota(orig_geo["lat"], orig_geo["lon"], dest_geo["lat"], dest_geo["lon"])
    if not rota:
        await msg.edit_text("❌ Não consegui calcular a rota.")
        return

    dist_km = rota["distancia"] / 1000
    dur_min = int(rota["duracao"] / 60)

    # Extrai só cidade/rua pra mostrar bonito
    orig_curto = f"{orig_geo['cidade']}, {NOMES_ESTADOS.get(orig_geo['uf'], orig_geo['uf'])}"
    dest_curto = f"{dest_geo['cidade']}, {NOMES_ESTADOS.get(dest_geo['uf'], dest_geo['uf'])}"

    texto = (
        f"🗺️ <b>Rota pronta!</b>\n\n"
        f"📍 <b>De:</b> {html.escape(origem_txt.title())}\n"
        f"   <i>({html.escape(orig_curto)})</i>\n\n"
        f"🏁 <b>Para:</b> {html.escape(destino_txt.title())}\n"
        f"   <i>({html.escape(dest_curto)})</i>\n\n"
        f"📏 <b>{dist_km:.1f} km</b> • ⏱️ <b>{dur_min} min</b>\n\n"
        f"<b>🧭 Passo a passo:</b>\n"
    )

    for i, p in enumerate(rota["passos"][:10], 1):
        texto += f"{i}. {p['texto']} <i>({formatar_dist(int(p['dist']))})</i>\n"

    if len(rota["passos"]) > 10:
        texto += f"\n<i>... +{len(rota['passos']) - 10} passos — abra no Maps pra ver tudo.</i>"

    gmaps = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={orig_geo['lat']},{orig_geo['lon']}"
        f"&destination={dest_geo['lat']},{dest_geo['lon']}"
        f"&travelmode=driving"
    )

    keyboard = [
        [InlineKeyboardButton("🧭 Abrir navegação", url=gmaps)],
        [botao_voltar("categorias")],
    ]

    await msg.edit_text(texto, parse_mode="HTML",
                       reply_markup=InlineKeyboardMarkup(keyboard),
                       disable_web_page_preview=True)


# ─────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        rastrear_user(context, update.message.message_id)
    await enviar_inicio(context.bot, update.effective_chat.id, context)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rastrear_user(context, update.message.message_id)
    loc = update.message.location
    context.user_data["lat"] = loc.latitude
    context.user_data["lon"] = loc.longitude
    context.user_data["cidade_atual"] = "Sua localização"
    context.user_data["uf_atual"] = ""
    context.user_data["estado"] = ESTADO_CATEGORIAS
    await enviar_categorias(context.bot, update.effective_chat.id, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rastrear_user(context, update.message.message_id)
    texto = (update.message.text or "").strip()
    estado = context.user_data.get("estado", ESTADO_INICIO)

    # Botões do teclado (sempre disponíveis)
    if texto == "🔄 Reiniciar" or texto == "/start":
        await start(update, context)
        return
    if texto == "📮 Digitar CEP":
        context.user_data["esperando"] = "cep"
        await enviar(
            context.bot, update.effective_chat.id, context,
            "📮 <b>Digite seu CEP</b> (8 números):\n\n"
            "Envie apenas os números, ex: <code>87708721</code>",
            reply_markup=InlineKeyboardMarkup([[botao_voltar("inicio")]]),
            limpar_antes=True,
        )
        return
    if texto == "🏙️ Cidade/estado":
        context.user_data["esperando"] = "cidade"
        await enviar(
            context.bot, update.effective_chat.id, context,
            "🏙️ <b>Digite o nome da cidade, bairro ou estado:</b>\n\n"
            "Ex: <i>Sarandi, Curitiba, Paraná...</i>",
            reply_markup=InlineKeyboardMarkup([[botao_voltar("inicio")]]),
            limpar_antes=True,
        )
        return
    if texto == "🚗 Traçar rota":
        context.user_data["esperando"] = "rota"
        context.user_data["estado"] = ESTADO_ROTA_INPUT
        await enviar(
            context.bot, update.effective_chat.id, context,
            "🚗 <b>Vamos traçar uma rota!</b>\n\n"
            "Me diga onde você está e pra onde quer ir.\n\n"
            "Ex: <i>estou na Rua X, Paranavaí, e quero ir para o Centro</i>",
            reply_markup=InlineKeyboardMarkup([[botao_voltar("categorias" if context.user_data.get("lat") else "inicio")]]),
            limpar_antes=True,
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
            await enviar_categorias(context.bot, update.effective_chat.id, context)
        else:
            await enviar(
                context.bot, update.effective_chat.id, context,
                "❌ CEP não encontrado. Digite novamente (só os 8 números):",
                reply_markup=InlineKeyboardMarkup([[botao_voltar("inicio")]]),
            )
        return

    # ── Cidade / Estado ──
    if esperando == "cidade":
        info = await interpretar_com_ia(texto)
        tipo = info.get("tipo")
        valor = info.get("valor", "") or texto

        if tipo == "estado":
            uf = ESTADOS.get(valor.lower())
            if not uf:
                await enviar(context.bot, update.effective_chat.id, context,
                            "❌ Estado não reconhecido. Tente novamente:")
                return
            cidades = await listar_cidades_uf(uf)
            if not cidades:
                await enviar(context.bot, update.effective_chat.id, context,
                            "❌ Não consegui carregar as cidades. Tente novamente:")
                return
            context.user_data.update({
                "cidades": cidades, "cidades_page": 0,
                "estado_nome": valor.title(), "esperando": None,
            })
            await enviar_cidades_uf(context.bot, update.effective_chat.id, context)
            return

        # Cidade ou desconhecido → geocodifica
        context.user_data["esperando"] = None
        msg = await enviar(context.bot, update.effective_chat.id, context,
                          "🔎 Procurando...", limpar_antes=True)
        resultados = await geocodificar_multiplos(valor, limite=8)
        if not resultados:
            await msg.edit_text(
                f"❌ Não encontrei <b>{html.escape(valor)}</b>.\n\n"
                f"Tente com mais detalhes, ex: <i>Sarandi, Paraná</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[botao_voltar("inicio")]]),
            )
            return

        context.user_data["opcoes_cidade"] = resultados
        keyboard = []
        for i, r in enumerate(resultados):
            uf_nome = NOMES_ESTADOS.get(r["uf"], r["uf"])
            label = f"📍 {r['cidade']} — {uf_nome}"[:60]
            keyboard.append([InlineKeyboardButton(label, callback_data=f"esccid_{i}")])
        keyboard.append([botao_voltar("inicio")])

        await msg.edit_text(
            f"🔎 <b>Encontrei {len(resultados)} locais para</b>\n"
            f"<i>{html.escape(valor)}</i>\n\nEscolha o correto:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # ── Rota ──
    if esperando == "rota":
        # 1. Tenta regex local primeiro (mais confiável)
        rota_local = detectar_rota_local(texto)
        if rota_local:
            context.user_data["esperando"] = None
            await mostrar_rota(context.bot, update.effective_chat.id, context,
                              rota_local[0], rota_local[1])
            return

        # 2. IA
        info = await interpretar_com_ia(texto)
        if info.get("tipo") == "rota" and info.get("origem") and info.get("destino"):
            context.user_data["esperando"] = None
            await mostrar_rota(context.bot, update.effective_chat.id, context,
                              info["origem"], info["destino"])
            return

        await enviar(
            context.bot, update.effective_chat.id, context,
            "❌ Não entendi. Tente: <i>'estou em X e quero ir para Y'</i>\n\n"
            "Seja específico com a cidade, ex:\n"
            "<i>estou na Rua X, em Paranavaí, e quero ir pro Centro</i>",
            reply_markup=InlineKeyboardMarkup([[botao_voltar("categorias" if context.user_data.get("lat") else "inicio")]]),
        )
        return

    # ── Texto livre (estado normal) ──
    # Tenta rota
    rota_local = detectar_rota_local(texto)
    if rota_local:
        await mostrar_rota(context.bot, update.effective_chat.id, context,
                          rota_local[0], rota_local[1])
        return

    info = await interpretar_com_ia(texto)
    tipo = info.get("tipo")

    if tipo == "rota" and info.get("origem") and info.get("destino"):
        await mostrar_rota(context.bot, update.effective_chat.id, context,
                          info["origem"], info["destino"])
        return

    if tipo == "categoria" and info.get("valor") in TAGS_MAP:
        if not context.user_data.get("lat"):
            await enviar(context.bot, update.effective_chat.id, context,
                        f"Entendi que você quer <b>{NOMES_AMIGAVEIS.get(info['valor'], info['valor'])}</b>.\n\n"
                        f"Mas primeiro me diga onde você está (localização, CEP ou cidade).",
                        limpar_antes=True)
            await enviar_inicio(context.bot, update.effective_chat.id, context)
            return
        await mostrar_resultados(context.bot, update.effective_chat.id, context, info["valor"])
        return

    if tipo == "cidade":
        # Reaproveita fluxo de cidade
        context.user_data["esperando"] = "cidade"
        await handle_text(update, context)
        return

    if tipo == "cep":
        resultado = await buscar_cep(info["valor"])
        if resultado and resultado["lat"]:
            context.user_data.update({
                "lat": resultado["lat"], "lon": resultado["lon"],
                "cidade_atual": resultado["cidade"], "uf_atual": resultado["uf"],
            })
            await enviar_categorias(context.bot, update.effective_chat.id, context)
            return

    # Fallback: se tem localização, mostra categorias; senão, início
    if context.user_data.get("lat"):
        await enviar_categorias(context.bot, update.effective_chat.id, context)
    else:
        await enviar_inicio(context.bot, update.effective_chat.id, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    # ── Voltar ──
    if data == "voltar_inicio":
        await voltar_para_inicio(context.bot, chat_id, context)
        return
    if data == "voltar_categorias":
        if context.user_data.get("lat"):
            await voltar_para_categorias(context.bot, chat_id, context)
        else:
            await voltar_para_inicio(context.bot, chat_id, context)
        return

    # ── Abrir rota ──
    if data == "abrir_rota":
        context.user_data["esperando"] = "rota"
        context.user_data["estado"] = ESTADO_ROTA_INPUT
        await enviar(
            context.bot, chat_id, context,
            "🚗 <b>Vamos traçar uma rota!</b>\n\n"
            "Me diga: <i>'estou em X e quero ir para Y'</i>\n\n"
            "Seja específico com a cidade.",
            reply_markup=InlineKeyboardMarkup([[botao_voltar("categorias")]]),
            limpar_antes=True,
        )
        return

    # ── Paginação ──
    if data == "cidnext":
        context.user_data["cidades_page"] = context.user_data.get("cidades_page", 0) + 1
        await enviar_cidades_uf(context.bot, chat_id, context)
        return
    if data == "cidprev":
        context.user_data["cidades_page"] = max(0, context.user_data.get("cidades_page", 0) - 1)
        await enviar_cidades_uf(context.bot, chat_id, context)
        return

    # ── Cidade do estado ──
    if data.startswith("ciduf_"):
        cidade = data.replace("ciduf_", "")
        estado = context.user_data.get("estado_nome", "")
        msg = await enviar(context.bot, chat_id, context,
                          f"🔎 Localizando <b>{html.escape(cidade)}</b>...",
                          limpar_antes=True)
        coords = await geocodificar(f"{cidade}, {estado}, Brasil")
        if coords:
            context.user_data.update({
                "lat": coords["lat"], "lon": coords["lon"],
                "cidade_atual": cidade, "uf_atual": coords.get("uf", ""),
            })
            await enviar_categorias(context.bot, chat_id, context)
        else:
            await msg.edit_text(f"❌ Não encontrei <b>{html.escape(cidade)}</b>.")
        return

    # ── Escolha de cidade ──
    if data.startswith("esccid_"):
        idx = int(data.replace("esccid_", ""))
        opcoes = context.user_data.get("opcoes_cidade", [])
        if idx >= len(opcoes):
            return
        e = opcoes[idx]
        context.user_data.update({
            "lat": e["lat"], "lon": e["lon"],
            "cidade_atual": e["cidade"], "uf_atual": e["uf"],
        })
        await enviar_categorias(context.bot, chat_id, context)
        return

    # ── Categoria ──
    if data.startswith("cat_"):
        categoria = data.replace("cat_", "")
        await mostrar_resultados(context.bot, chat_id, context, categoria)
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
    logger.info(f"✅ Webhook: {url}")


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
    logger.info(f"🚀 Porta {PORT}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
