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
    GET  /api/capes          каталог плащей
    POST /api/cape/set       надеть плащ (или снять: cape = "")
    POST /api/cape/upload    залить свой плащ (PNG 64x32, base64)
    GET  /api/capes/mine     что доступно лично мне

  МОД ПЛАЩЕЙ (внутри игры)
    GET  /api/capes/bulk?u=uuid1,uuid2,...   какие плащи у этих игроков
    GET  /capes/<id>.png                     сама картинка

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
DB_PATH = DATA_DIR / "data.json"

CAPES_DIR.mkdir(parents=True, exist_ok=True)

# Онлайн держим в памяти: присутствие по своей природе недолговечно,
# переживать перезапуск сервиса ему незачем.
ONLINE = {}
ONLINE_TTL = 75          # секунд без heartbeat — считаем, что вышел
MAX_CAPE_BYTES = 256 * 1024
ALLOWED_CAPE_SIZE = (64, 32)

# Плащи, которые есть у всех с самого начала. Картинки положите
# рядом в data/capes/ с такими же именами.
DEFAULT_CAPES = [
    {"id": "luxe", "name": "Luxe", "author": "Luxe Visuals"},
    {"id": "flame", "name": "Пламя", "author": "Luxe Visuals"},
    {"id": "ice", "name": "Лёд", "author": "Luxe Visuals"},
    {"id": "gold", "name": "Золото", "author": "Luxe Visuals"},
]


def now() -> float:
    return time.time()


def load_db() -> dict:
    if not DB_PATH.exists():
        # Первый запуск: раскладываем плащи, которые есть у всех.
        # Их можно будет удалить, и они не вернутся — сидируем только один раз.
        db = {"players": {}, "capes": {}}
        for c in DEFAULT_CAPES:
            db["capes"][c["id"]] = {"name": c["name"], "author": c["author"], "builtin": True}
        return db
    try:
        with DB_PATH.open("r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("players", {})
        d.setdefault("capes", {})
        return d
    except Exception:
        return {"players": {}, "capes": {}}


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
        p = {"name": "", "cape": "", "friends": [], "offline": True}
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


def cape_list() -> list:
    """Единственный источник правды — DB['capes']. owner пустой = плащ для всех."""
    out = []
    for cid, c in DB["capes"].items():
        out.append({
            "id": cid,
            "name": c.get("name", cid),
            "author": c.get("author", ""),
            "owner": c.get("owner", ""),
            "builtin": bool(c.get("builtin")),
        })
    out.sort(key=lambda x: (not x["builtin"], x["name"].lower()))
    return out


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
    if name and p["name"] != name:
        p["name"] = name
        save_db(DB)
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
        fu = offline_uuid(nick)
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
    return ok(capes=cape_list())


async def api_capes_mine(request: web.Request):
    uid, e = await me(request)
    if e:
        return e
    p = player(uid)
    mine = [c for c in cape_list() if not c.get("owner") or c.get("owner") == uid]
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
    DB["capes"][cid] = {"name": name, "author": p.get("name", ""), "owner": uid}
    p["cape"] = cid
    save_db(DB)
    return ok(id=cid, url=cape_url(cid))


async def cape_file(request: web.Request):
    cid = clean_id(request.match_info["cid"])
    if not cid:
        raise web.HTTPNotFound()
    path = CAPES_DIR / (cid + ".png")
    if not path.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=600"})


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
    DB["capes"][cid] = {"name": name, "author": (d.get("author") or "Luxe Visuals")}
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
        web.post("/api/admin/cape", api_admin_cape),
        web.post("/api/admin/del", api_admin_del),
    ])
    return app


if __name__ == "__main__":
    print("Luxe Service на порту", PORT, flush=True)
    web.run_app(make_app(), port=PORT)
