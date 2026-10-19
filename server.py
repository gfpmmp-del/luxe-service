"""
Luxe Service — присутствие друзей и каталог плащей.

Одно приложение на aiohttp, по той же схеме, что ваш телеграм-бот:
Render -> Web Service, порт берётся из PORT.

Кто что дёргает:

  ЛАУНЧЕР
    POST /api/login          получить сессию: по токену Minecraft или офлайн по нику
    POST /api/heartbeat      "я в лаунчере" (раз в 30 секунд)
    GET  /api/friends        мои друзья и кто из них онлайн
    POST /api/friends/add    добавить друга по нику
    POST /api/friends/del    убрать друга
    GET  /api/capes          каталог плащей (?sort=popular|new|name)
    POST /api/cape/set       надеть плащ (или снять: cape = "")
    POST /api/cape/upload    залить свой плащ (PNG 64x32, base64)
    GET  /api/capes/mine     что доступно лично мне
    POST /api/skin/set       сохранить свой скин (PNG 64x64, base64) —
                             для офлайн-игроков, без лицензии
    POST /api/skin/clear     убрать свой скин
    GET  /api/skin/mine      мой скин (url, model) или пусто

  МОД ПЛАЩЕЙ И СКИНОВ (внутри игры)
    GET  /api/capes/bulk?u=uuid1,uuid2,...   какие плащи у этих игроков
    GET  /api/skins/bulk?u=uuid1,uuid2,...   какие Luxe-скины у этих игроков
    GET  /capes/<id>.png                     сама картинка плаща
    GET  /skins/<uuid>.png                   сама картинка скина
    GET  /api/mods                           какие есть сборки мода и для каких версий
    GET  /mods/<файл>.jar                    сам мод (лаунчер кладёт его в mods/ игры)

  АДМИН
    POST /api/admin/cape     добавить плащ в общий каталог (нужен ADMIN_TOKEN)
    POST /api/admin/del      убрать плащ из каталога

Почему сессия, а не просто ник: статус и плащи видит весь сервер,
поэтому никто не должен иметь возможности назваться чужим ником.
Если игрок вошёл в лаунчер с лицензией, uuid подтверждает Mojang.
Офлайн-режим тоже разрешён, но такой игрок помечается offline.
"""

import asyncio
import hashlib
import json
import os
import secrets
import time
import uuid as uuidlib
from pathlib import Path

from aiohttp import ClientSession, web

PORT = int(os.getenv("PORT", "10000"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CAPES_DIR = DATA_DIR / "capes"
SKINS_DIR = DATA_DIR / "skins"
PRESETS_DIR = BASE_DIR / "presets"
MODS_DIR = BASE_DIR / "mods"
DB_PATH = DATA_DIR / "data.json"

# Сборки мода плащей: файл (лежит в mods/) и для каких версий Minecraft он собран.
# Внутри джарника прописан тот же диапазон версий, так что несовместимая
# сборка просто не загрузится — но лаунчер и так берёт нужную.
MODS = [
    ("luxecape-1.1.0-1.21-1.21.1.jar", ["1.21", "1.21.1"]),
    ("luxecape-1.1.0-1.21.2-1.21.8.jar", ["1.21.2", "1.21.3", "1.21.4", "1.21.5",
                                           "1.21.6", "1.21.7", "1.21.8"]),
    ("luxecape-1.1.0-1.21.9-1.21.11.jar", ["1.21.9", "1.21.10", "1.21.11"]),
]

CAPES_DIR.mkdir(parents=True, exist_ok=True)
SKINS_DIR.mkdir(parents=True, exist_ok=True)

# Онлайн держим в памяти: присутствие по своей природе недолговечно,
# переживать перезапуск сервиса ему незачем.
ONLINE = {}
ONLINE_TTL = 75          # секунд без heartbeat — считаем, что вышел
MAX_CAPE_BYTES = 256 * 1024
ALLOWED_CAPE_SIZE = (64, 32)
MAX_SKIN_BYTES = 256 * 1024
ALLOWED_SKIN_SIZE = (64, 64)

# Плащи, которые есть у всех с самого начала. Картинки лежат рядом
# в presets/ с такими же именами (см. docstring выше: каталог «из интернета»
# — это как раз этот список, который лаунчер забирает по /api/capes).
#
# created  — когда плащ появился: по нему сортировка «Новые»
# installs — сколько раз плащ надевали (растёт в /api/cape/set)
# wears    — сколько игроков носят его прямо сейчас (считается на лету)
DEFAULT_CAPES = [
    {"id": "luxe", "name": "Luxe", "author": "Luxe Visuals", "created": 1766178000},
    {"id": "flame", "name": "Пламя", "author": "Luxe Visuals", "created": 1766782800},
    {"id": "ice", "name": "Лёд", "author": "Luxe Visuals", "created": 1767646800},
    {"id": "gold", "name": "Золото", "author": "Luxe Visuals", "created": 1768683600},
    {"id": "neon", "name": "Неон", "author": "Luxe Visuals", "created": 1770670800},
    {"id": "emerald", "name": "Изумруд", "author": "Luxe Visuals", "created": 1771707600},
    {"id": "redstone", "name": "Редстоун", "author": "Luxe Visuals", "created": 1772658000},
    {"id": "amethyst", "name": "Аметист", "author": "Luxe Visuals", "created": 1773435600},
    {"id": "obsidian", "name": "Обсидиан", "author": "Luxe Visuals", "created": 1774645200},
    {"id": "slime", "name": "Слизень", "author": "Luxe Visuals", "created": 1775250000},
    {"id": "creeper", "name": "Крипер", "author": "Luxe Visuals", "created": 1775941200},
    {"id": "skull", "name": "Череп", "author": "Luxe Visuals", "created": 1776632400},
    {"id": "heart", "name": "Сердце", "author": "Luxe Visuals", "created": 1777669200},
    {"id": "star", "name": "Звезда", "author": "Luxe Visuals", "created": 1778706000},
    {"id": "moon", "name": "Луна", "author": "Luxe Visuals", "created": 1779742800},
    {"id": "sun", "name": "Солнце", "author": "Luxe Visuals", "created": 1780693200},
    {"id": "bolt", "name": "Молния", "author": "Luxe Visuals", "created": 1781643600},
    {"id": "cosmos", "name": "Космос", "author": "Luxe Visuals", "created": 1782853200},
    {"id": "nether", "name": "Нижний мир", "author": "Luxe Visuals", "created": 1784062800},
    {"id": "ender", "name": "Энд", "author": "Luxe Visuals", "created": 1785186000},
    {"id": "matrix", "name": "Матрица", "author": "Luxe Visuals", "created": 1785963600},
    {"id": "camo", "name": "Камуфляж", "author": "Luxe Visuals", "created": 1786914000},
    {"id": "checker", "name": "Шахматы", "author": "Luxe Visuals", "created": 1787950800},
    {"id": "waves", "name": "Волны", "author": "Luxe Visuals", "created": 1788642000},
    {"id": "clouds", "name": "Облака", "author": "Luxe Visuals", "created": 1789333200},
    {"id": "sunset", "name": "Закат", "author": "Luxe Visuals", "created": 1790024400},
    {"id": "rainbow", "name": "Радуга", "author": "Luxe Visuals", "created": 1790629200},
    {"id": "pixel", "name": "Пиксель", "author": "Luxe Visuals", "created": 1791061200},
    {"id": "crown", "name": "Корона", "author": "Luxe Visuals", "created": 1791406800},
    {"id": "sakura", "name": "Сакура", "author": "Luxe Visuals", "created": 1791752400},
    {"id": "panda", "name": "Панда", "author": "Luxe Visuals", "created": 1792011600},
    {"id": "turbox", "name": "Люкс Турбо", "author": "Luxe Visuals", "created": 1792270800},
]


def now() -> float:
    return time.time()


def seed_defaults(db: dict) -> None:
    """Добавляет встроенные плащи в каталог — по одному разу на каждый id.

    Так новые плащи из репозитория появляются в каталоге и после редеплоя,
    но удалённый вручную (админом) плащ больше не возвращается.
    """
    seeded = db.setdefault("seeded", [])
    for c in DEFAULT_CAPES:
        cid = c["id"]
        if cid in seeded:
            continue
        seeded.append(cid)
        cur = db["capes"].get(cid) or {}
        db["capes"][cid] = {
            "name": cur.get("name") or c["name"],
            "author": cur.get("author") or c["author"],
            "builtin": True,
            "created": cur.get("created", c["created"]),
            "installs": cur.get("installs", 0),
        }


def load_db() -> dict:
    db = {"players": {}, "capes": {}, "by_name": {}, "seeded": []}
    if DB_PATH.exists():
        try:
            with DB_PATH.open("r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                db.update(d)
        except Exception:
            pass
    db.setdefault("players", {})
    db.setdefault("capes", {})
    db.setdefault("by_name", {})
    seed_defaults(db)
    return db


def save_db(db: dict) -> None:
    tmp = DB_PATH.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=1)
        tmp.replace(DB_PATH)
    except Exception:
        pass


DB = load_db()
SESSIONS = {}            # token -> uuid


def offline_uuid(nick: str) -> str:
    """Такой же uuid, какой игра выдаёт офлайн-игроку."""
    h = bytearray(hashlib.md5(("OfflinePlayer:" + nick).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    s = h.hex()
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"


def player(uuid: str, create: bool = True) -> dict:
    uuid = uuid.lower()
    p = DB["players"].get(uuid)
    if p is None and create:
        p = {"name": "", "cape": "", "friends": [], "offline": True,
             "skin_model": "", "skin_v": 0}
        DB["players"][uuid] = p
    return p


def clean_id(cid: str) -> str:
    """Идентификатор плаща идёт в имя файла, поэтому строго ограничиваем."""
    cid = (cid or "").strip().lower()
    if not cid or len(cid) > 48:
        return ""
    for ch in cid:
        if ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_":
            return ""
    return cid


def remember_name(nick: str, uid: str) -> None:
    """Ник -> uuid. Нужно потому, что у лицензионного игрока uuid от Mojang,
    а не от ника, и найти его по нику иначе невозможно."""
    nick = (nick or "").strip().lower()
    if nick and uid:
        DB.setdefault("by_name", {})[nick] = uid.lower()


def resolve_name(nick: str) -> str:
    """Ник в uuid: сначала по тому, что запомнили, потом офлайн-расчёт.

    ВАЖНО: регистр нигде не трогаем до расчёта uuid — хеш считается
    от ника как есть, иначе для ников с заглавными буквами выйдет
    совсем другой uuid, чем в игре.
    """
    raw = (nick or "").strip()
    if not raw:
        return ""
    uid = DB.get("by_name", {}).get(raw.lower())
    if uid:
        return uid
    return offline_uuid(raw).lower()


def wears_map() -> dict:
    """Сколько игроков прямо сейчас носят каждый плащ — это и есть «популярность»."""
    out = {}
    for p in DB.get("players", {}).values():
        cid = p.get("cape") or ""
        if cid:
            out[cid] = out.get(cid, 0) + 1
    return out


def cape_list() -> list:
    """Единственный источник правды — DB['capes']. owner пустой = плащ для всех."""
    wears = wears_map()
    out = []
    for cid, c in DB["capes"].items():
        out.append({
            "id": cid,
            "name": c.get("name", cid),
            "author": c.get("author", ""),
            "owner": c.get("owner", ""),
            "builtin": bool(c.get("builtin")),
            "created": float(c.get("created", 0) or 0),
            "installs": int(c.get("installs", 0) or 0),
            "wears": wears.get(cid, 0),
        })
    out.sort(key=lambda x: (not x["builtin"], x["name"].lower()))
    return out


def sort_capes(items: list, order: str) -> list:
    """Порядок каталога: popular (по умолчанию), new, name."""
    if order == "new":
        return sorted(items, key=lambda x: (-x["created"], x["name"].lower()))
    if order == "name":
        return sorted(items, key=lambda x: x["name"].lower())
    return sorted(items, key=lambda x: (-x["wears"], -x["installs"], x["name"].lower()))


def cape_url(cid: str) -> str:
    base = PUBLIC_URL or ""
    return f"{base}/capes/{cid}.png"


def is_online(uuid: str) -> bool:
    t = ONLINE.get(uuid.lower())
    return bool(t and now() - t < ONLINE_TTL)


async def json_body(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def err(text: str, code: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": text}, status=code)


def ok(**kw) -> web.Response:
    d = {"ok": True}
    d.update(kw)
    return web.json_response(d)


async def me(request: web.Request):
    """Сессия -> uuid. Возвращает (uuid, None) или (None, ответ с ошибкой)."""
    token = request.headers.get("X-Session", "")
    u = SESSIONS.get(token)
    if not u:
        return None, err("Сессия не найдена — зайди заново", 401)
    return u, None


# ============================================================
#  ВХОД
# ============================================================

async def api_login(request: web.Request):
    d = await json_body(request)

    token = (d.get("token") or "").strip()
    if token:
        nick = ""
        uid = ""
        try:
            async with ClientSession() as s:
                async with s.get(
                    "https://api.minecraftservices.com/minecraft/profile",
                    headers={"Authorization": "Bearer " + token},
                    timeout=15,
                ) as r:
                    if r.status != 200:
                        return err("Minecraft не подтвердил токен (" + str(r.status) + ")", 401)
                    prof = await r.json()
                    uid = (prof.get("id") or "").lower()
                    nick = prof.get("name") or ""
        except Exception as e:
            return err("Не дозвонился до Mojang: " + str(e), 503)

        if not uid:
            return err("У аккаунта нет профиля Minecraft", 401)

        uid = str(uuidlib.UUID(uid)) if len(uid) == 32 else uid
        sess = secrets.token_urlsafe(24)
        SESSIONS[sess] = uid.lower()
        p = player(uid)
        p["name"] = nick
        p["offline"] = False
        remember_name(nick, uid)
        save_db(DB)
        return ok(session=sess, uuid=uid.lower(), name=nick, offline=False)

    nick = (d.get("offline") or "").strip()
    if not nick:
        return err("Нужен token или offline-ник")

    uid = offline_uuid(nick)
    sess = secrets.token_urlsafe(24)
    SESSIONS[sess] = uid.lower()
    p = player(uid)
    p["name"] = nick
    p["offline"] = True
    remember_name(nick, uid)
    save_db(DB)
    return ok(session=sess, uuid=uid.lower(), name=nick, offline=True)


# ============================================================
#  ПРИСУТСТВИЕ
# ============================================================

async def api_heartbeat(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    d = await json_body(request)
    ONLINE[uid] = now()
    p = player(uid)
    name = (d.get("name") or "").strip()
    if name:
        if p["name"] != name:
            p["name"] = name
        remember_name(name, uid)
    p["last"] = now()
    save_db(DB)
    return ok(ttl=ONLINE_TTL)


async def api_friends(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    p = player(uid)
    out = []
    for nick in p.get("friends", []):
        fu = resolve_name(nick)
        fp = DB["players"].get(fu.lower(), {})
        out.append({
            "name": nick,
            "uuid": fu.lower(),
            "online": is_online(fu),
            "last": fp.get("last", 0),
        })
    out.sort(key=lambda x: (not x["online"], x["name"].lower()))
    return ok(friends=out, me={"uuid": uid, "name": p.get("name", "")})


async def api_friends_add(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    d = await json_body(request)
    nick = (d.get("name") or "").strip()
    if not (3 <= len(nick) <= 16):
        return err("Ник: 3-16 символов")
    p = player(uid)
    if nick.lower() == (p.get("name") or "").lower():
        return err("Это ты и есть")
    if len(p.get("friends", [])) >= 100:
        return err("Больше 100 друзей не помещается")
    for f in p["friends"]:
        if f.lower() == nick.lower():
            return ok(already=True)
    p.setdefault("friends", []).append(nick)
    save_db(DB)
    return ok(friends=p["friends"])


async def api_friends_del(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    d = await json_body(request)
    nick = (d.get("name") or "").strip().lower()
    p = player(uid)
    p["friends"] = [f for f in p.get("friends", []) if f.lower() != nick]
    save_db(DB)
    return ok(friends=p["friends"])


# ============================================================
#  ПЛАЩИ
# ============================================================

async def api_capes(request: web.Request):
    order = (request.query.get("sort") or "popular").strip().lower()
    if order not in ("popular", "new", "name"):
        order = "popular"
    return ok(capes=sort_capes(cape_list(), order), sort=order)


async def api_capes_mine(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    p = player(uid)
    mine = [c for c in cape_list() if not c.get("owner") or c.get("owner") == uid]
    mine = sort_capes(mine, "popular")
    return ok(capes=mine, current=p.get("cape", ""))


async def api_cape_set(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    d = await json_body(request)
    cid = (d.get("cape") or "").strip()
    p = player(uid)

    if cid:
        known = {c["id"]: c for c in cape_list()}
        c = known.get(cid)
        if not c:
            return err("Такого плаща нет")
        if c.get("owner") and c["owner"] != uid:
            return err("Это чужой плащ")

    prev = p.get("cape", "")
    if cid and cid != prev:
        # считаем, сколько раз плащ надевали — на этом строится «Популярное»
        c2 = DB["capes"].get(cid)
        if c2 is not None:
            c2["installs"] = int(c2.get("installs", 0) or 0) + 1
    p["cape"] = cid
    save_db(DB)
    return ok(cape=cid, url=cape_url(cid) if cid else "")


async def api_capes_bulk(request: web.Request):
    """Мод спрашивает пачкой: какие плащи у этих игроков."""
    raw = request.query.get("u", "")
    ids = [x.strip().lower() for x in raw.split(",") if x.strip()]
    if not ids:
        return web.json_response({})
    out = {}
    for u in ids[:200]:
        try:
            key = str(uuidlib.UUID(u))
        except Exception:
            key = offline_uuid(u).lower()
        p = DB["players"].get(key)
        if not p:
            continue
        cid = p.get("cape", "")
        if cid:
            out[key] = cape_url(cid)
    return web.json_response(out)


async def api_cape_upload(request: web.Request):
    import base64
    import io

    uid, e = await me(request)
    if e:
        return e
    d = await json_body(request)
    raw = d.get("png") or ""
    name = (d.get("name") or "").strip()[:24] or "Свой плащ"
    if not raw:
        return err("Нужен PNG в base64")
    if "," in raw[:80]:
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw)
    except Exception:
        return err("Не разобрал base64")
    if len(blob) > MAX_CAPE_BYTES:
        return err("Файл больше 256 КБ")
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(blob))
        if im.size != ALLOWED_CAPE_SIZE:
            return err("Плащ — PNG 64x32, а тут " + str(im.size[0]) + "x" + str(im.size[1]))
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        blob = buf.getvalue()
    except ImportError:
        pass          # без Pillow просто положим как есть
    except Exception as ex:
        return err("Картинка не читается: " + str(ex))

    p = player(uid)
    cid = "u" + uid.replace("-", "")[:12] + "-" + secrets.token_hex(3)
    with (CAPES_DIR / (cid + ".png")).open("wb") as f:
        f.write(blob)
    DB["capes"][cid] = {"name": name, "author": p.get("name", ""), "owner": uid,
                        "created": now(), "installs": 0}
    p["cape"] = cid
    save_db(DB)
    return ok(id=cid, url=cape_url(cid))


async def cape_file(request: web.Request):
    cid = clean_id(request.match_info["cid"])
    if not cid:
        raise web.HTTPNotFound()
    # Сначала то, что залили или выбрали в лаунчере, потом встроенные плащи
    # из репозитория — они переживают любой редеплой.
    for path in (CAPES_DIR / (cid + ".png"), PRESETS_DIR / (cid + ".png")):
        if path.exists():
            return web.FileResponse(path, headers={"Cache-Control": "public, max-age=600"})
    raise web.HTTPNotFound()


# ============================================================
#  СКИНЫ БЕЗ ЛИЦЕНЗИИ
#
#  У лицензионного аккаунта скин живёт на серверах Mojang и виден всем.
#  У офлайн-игрока такого места нет, поэтому скин храним у себя и
#  раздаём моду: тот подставляет текстуру по uuid игрока.
#  Видят её те, у кого стоит мод Luxe (он уже ставится лаунчером).
# ============================================================

def skin_url(uid: str) -> str:
    base = PUBLIC_URL or ""
    return f"{base}/skins/{uid}.png"


def skin_state(uid: str) -> dict:
    """Скин игрока: пусто, если его нет или файл потерялся."""
    p = DB["players"].get(uid.lower()) or {}
    v = int(p.get("skin_v", 0) or 0)
    if v <= 0 or not (SKINS_DIR / (uid.lower() + ".png")).exists():
        return {}
    return {
        "url": skin_url(uid.lower()) + "?v=" + str(v),
        "model": p.get("skin_model") or "classic",
        "v": v,
    }


async def api_skin_set(request: web.Request):
    import base64
    import io

    uid, e = await me(request)
    if e:
        return e
    d = await json_body(request)
    raw = d.get("png") or ""
    model = "slim" if (d.get("model") or "").strip().lower() == "slim" else "classic"
    if not raw:
        return err("Нужен PNG в base64")
    if "," in raw[:80]:
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw)
    except Exception:
        return err("Не разобрал base64")
    if len(blob) > MAX_SKIN_BYTES:
        return err("Файл больше 256 КБ")
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(blob))
        if im.size != ALLOWED_SKIN_SIZE:
            return err("Скин — PNG 64x64, а тут " + str(im.size[0]) + "x" + str(im.size[1]))
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        blob = buf.getvalue()
    except ImportError:
        pass          # без Pillow просто положим как есть
    except Exception as ex:
        return err("Картинка не читается: " + str(ex))

    p = player(uid)
    with (SKINS_DIR / (uid + ".png")).open("wb") as f:
        f.write(blob)
    p["skin_model"] = model
    p["skin_v"] = int(now())
    save_db(DB)
    return ok(url=skin_url(uid) + "?v=" + str(p["skin_v"]), model=model, v=p["skin_v"])


async def api_skin_clear(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    p = player(uid)
    p["skin_model"] = ""
    p["skin_v"] = 0
    try:
        (SKINS_DIR / (uid + ".png")).unlink()
    except Exception:
        pass
    save_db(DB)
    return ok()


async def api_skin_mine(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    st = skin_state(uid)
    if not st:
        return ok(url="", model="", v=0)
    return ok(**st)


async def api_skins_bulk(request: web.Request):
    """Мод спрашивает пачкой: у кого из этих игроков есть Luxe-скин."""
    raw = request.query.get("u", "")
    ids = [x.strip().lower() for x in raw.split(",") if x.strip()]
    if not ids:
        return web.json_response({})
    out = {}
    for u in ids[:200]:
        try:
            key = str(uuidlib.UUID(u))
        except Exception:
            key = offline_uuid(u).lower()
        st = skin_state(key)
        if st:
            out[key] = st
    return web.json_response(out)


async def skin_file(request: web.Request):
    name = request.match_info["name"]
    try:
        key = str(uuidlib.UUID(name))
    except Exception:
        raise web.HTTPNotFound()
    path = SKINS_DIR / (key + ".png")
    if path.exists():
        return web.FileResponse(path, headers={
            "Content-Type": "image/png",
            "Cache-Control": "public, max-age=600",
        })
    raise web.HTTPNotFound()


# ============================================================
#  РАЗДАЧА МОДА ПЛАЩЕЙ
# ============================================================

async def api_mods(request: web.Request):
    """Что за сборки мода есть и для каких версий — лаунчер выберет сам."""
    base = PUBLIC_URL or ""
    items = []
    for fname, versions in MODS:
        path = MODS_DIR / fname
        if not path.exists():
            continue
        data = path.read_bytes()
        items.append({
            "file": fname,
            "url": f"{base}/mods/{fname}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "versions": versions,
        })
    return ok(mods=items)


async def mod_file(request: web.Request):
    """Jar отдаём только из своего списка — никаких путей снаружи."""
    name = request.match_info["name"]
    for fname, _versions in MODS:
        if fname == name:
            path = MODS_DIR / fname
            if not path.exists():
                break
            return web.FileResponse(path, headers={
                "Content-Type": "application/java-archive",
                "Cache-Control": "public, max-age=600",
            })
    raise web.HTTPNotFound()


# ============================================================
#  АДМИН
# ============================================================

async def api_admin_cape(request: web.Request):
    import base64

    if not ADMIN_TOKEN or request.headers.get("X-Admin", "") != ADMIN_TOKEN:
        return err("Нет доступа", 403)
    d = await json_body(request)
    cid = clean_id(d.get("id"))
    name = (d.get("name") or "").strip()[:24]
    raw = d.get("png") or ""
    if not cid or not name:
        return err("Нужны id (латиница, цифры, дефис) и name")
    if raw:
        if "," in raw[:80]:
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw)
        except Exception:
            return err("Не разобрал base64")
        with (CAPES_DIR / (cid + ".png")).open("wb") as f:
            f.write(blob)
    old = DB["capes"].get(cid) or {}
    DB["capes"][cid] = {
        "name": name,
        "author": (d.get("author") or "Luxe Visuals"),
        "created": old.get("created", now()),
        "installs": old.get("installs", 0),
    }
    save_db(DB)
    return ok(id=cid)


async def api_admin_del(request: web.Request):
    if not ADMIN_TOKEN or request.headers.get("X-Admin", "") != ADMIN_TOKEN:
        return err("Нет доступа", 403)
    d = await json_body(request)
    cid = (d.get("id") or "").strip()
    if cid in DB["capes"]:
        del DB["capes"][cid]
        try:
            (CAPES_DIR / (cid + ".png")).unlink()
        except Exception:
            pass
        save_db(DB)
    return ok()


async def health(request: web.Request):
    return web.Response(text="Luxe Service: ok, online=" + str(sum(1 for u in ONLINE if is_online(u))))


async def api_version(request: web.Request):
    return ok(service="luxe", version=1, online_ttl=ONLINE_TTL)


def make_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", health),
        web.get("/api/version", api_version),
        web.post("/api/login", api_login),
        web.post("/api/heartbeat", api_heartbeat),
        web.get("/api/friends", api_friends),
        web.post("/api/friends/add", api_friends_add),
        web.post("/api/friends/del", api_friends_del),
        web.get("/api/capes", api_capes),
        web.get("/api/capes/mine", api_capes_mine),
        web.get("/api/capes/bulk", api_capes_bulk),
        web.post("/api/cape/set", api_cape_set),
        web.post("/api/cape/upload", api_cape_upload),
        web.get("/capes/{cid}.png", cape_file),
        web.post("/api/skin/set", api_skin_set),
        web.post("/api/skin/clear", api_skin_clear),
        web.get("/api/skin/mine", api_skin_mine),
        web.get("/api/skins/bulk", api_skins_bulk),
        web.get("/skins/{name}.png", skin_file),
        web.get("/api/mods", api_mods),
        web.get("/mods/{name}", mod_file),
        web.post("/api/admin/cape", api_admin_cape),
        web.post("/api/admin/del", api_admin_del),
    ])
    return app


if __name__ == "__main__":
    print("Luxe Service на порту", PORT, flush=True)
    web.run_app(make_app(), port=PORT)
