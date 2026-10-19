# Luxe Service

Один сервис на три дела: **кто из друзей в лаунчере**, **каталог плащей**
и **скины без лицензии**. Питон на aiohttp — то же самое, на чём работает
телеграм-бот, деплой такой же.

## Что нужно сделать на Render (примерно 10 минут)

1. Завести пустой репозиторий на GitHub, например `luxe-service`.
   Положить туда всё из этой папки: `server.py`, `requirements.txt`,
   `Dockerfile`, `render.yaml`.

2. На [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**
   → подключить этот репозиторий. Render сам прочитает `render.yaml`.

3. В панели задать две переменные окружения:
   - `ADMIN_TOKEN` — любой пароль, который вы придумаете сами
     (например, длинная случайная строка). Он нужен только вам, чтобы
     добавлять плащи в общий каталог.
   - `PUBLIC_URL` — адрес, который Render выдаст сервису,
     например `https://luxe-service.onrender.com`. Вписать без слэша на конце.

4. Нажать **Apply**. Первый деплой занимает пару минут.

5. Проверить: открыть адрес сервиса в браузере — должно быть
   `Luxe Service: ok, online=0`.

Адрес будет известен после первого деплоя, поэтому `PUBLIC_URL` можно
вписать вторым заходом (после чего Render перезапустит сервис).

## Что важно знать про бесплатный тариф

- **Сервис засыпает** после ~15 минут тишины и просыпается ~минуту при
  первом обращении. Для друзей это значит, что первый запуск лаунчера
  может показать «нет связи» — второе обновление списка сработает.
- **Диск не сохраняется.** `data/data.json` и загруженные плащи живут до
  первого перезапуска/редеплоя. Онлайн-статусы это не затрагивает — они
  и так в памяти. А вот «кто какой плащ надел» сбросится.
  Если это мешает — вариантов три: подключить платный диск (диск на Render
  доступен только на платных инстансах, ~7$/мес), переехать на бесплатную
  внешнюю базу, либо смириться. Плащи из папки `presets/` и общий каталог
  перезапуск переживают всегда.

## Как заливать плащи в общий каталог

Плащ — это PNG ровно **64x32** (старый формат плаща; игра растягивает его
на современную модель). Свои плащи игроки могут заливать из лаунчера,
а общие кладутся одной ручкой:

```
POST https://<ваш-адрес>/api/admin/cape
X-Admin: <ADMIN_TOKEN>
Content-Type: application/json

{
  "id": "flame",
  "name": "Пламя",
  "author": "Luxe Visuals",
  "png": "<файл .png в base64>"
}
```

После этого плащ сразу виден в каталоге у всех. Убрать:

```
POST /api/admin/del   {"id": "flame"}
```

Плащи, которые есть у всех с самого начала, перечислены в `server.py` в
`DEFAULT_CAPES`. Их картинки лежат в папке **`presets/`** прямо в репозитории —
это значит, что они переживают любой перезапуск и редеплой. Сейчас там
32 нарисованных плаща: `luxe`, `flame`, `ice`, `gold`, `neon`, `emerald`,
`redstone`, `amethyst`, `obsidian`, `slime`, `creeper`, `skull`, `heart`,
`star`, `moon`, `sun`, `bolt`, `cosmos`, `nether`, `ender`, `matrix`, `camo`,
`checker`, `waves`, `clouds`, `sunset`, `rainbow`, `pixel`, `crown`, `sakura`,
`panda`, `turbox`.

Список отдаётся ручкой `/api/capes`, у каждого плаща есть:

| поле | смысл |
| --- | --- |
| `created` | когда плащ появился (по нему сортировка «Новые») |
| `installs` | сколько раз плащ надевали (растёт при `/api/cape/set`) |
| `wears` | сколько игроков носят прямо сейчас (по нему «Популярные») |

Порядок можно заказать серверу: `/api/capes?sort=popular` (по умолчанию),
`?sort=new`, `?sort=name`.

Если кладёте свой плащ в `presets/`, имя файла должно совпадать с `id`,
и файл должен быть PNG **64x32**. Новые id из `DEFAULT_CAPES` подхватываются
сами и на уже работающем сервисе (по одному разу на id: удалённый админом
плащ не вернётся).

## Скины без лицензии

У лицензионного аккаунта скин живёт на серверах Mojang и виден всем без
модов. У офлайн-игрока такого места нет, поэтому скин хранит этот сервис,
а показывает его мод `Luxe Cape` — тот же, что рисует плащи. Видят такие
скины те, у кого стоит мод, то есть пользователи лаунчера.

Как это выглядит для игрока: в разделе «Скин» лаунчера кнопка сама
меняется на «Сохранить скин в Luxe», «Мой скин из Luxe» и «Убрать скин
из Luxe», а рядом написано, что скин увидят в сборках лаунчера.

Что нужно сервису:

- PNG ровно **64x64** (лаунчер сам приводит старый 64x32 к этому виду);
- до 256 КБ;
- модель — `classic` или `slim`.

Файлы лежат в `data/skins/<uuid>.png`. Как и плащи, они живут до первого
редеплоя на бесплатном тарифе (см. выше).

Проверить руками:

```powershell
$r = Invoke-RestMethod -Method Post -Uri "https://<адрес>/api/login" `
     -ContentType "application/json" -Body '{"offline":"Steve"}'
$s = $r.session
$png = [Convert]::ToBase64String([IO.File]::ReadAllBytes("skin.png"))

# сохранить скин (Steve)
Invoke-RestMethod -Method Post -Uri "https://<адрес>/api/skin/set" `
     -Headers @{ "X-Session" = $s } -ContentType "application/json" `
     -Body (@{ png = $png; model = "classic" } | ConvertTo-Json)

# посмотреть, что сохранено
Invoke-RestMethod -Uri "https://<адрес>/api/skin/mine" -Headers @{ "X-Session" = $s }

# что видит мод про этих игроков
Invoke-RestMethod -Uri "https://<адрес>/api/skins/bulk?u=$($r.uuid)"

# убрать скин
Invoke-RestMethod -Method Post -Uri "https://<адрес>/api/skin/clear" `
     -Headers @{ "X-Session" = $s }
```

## Проверка руками (без лаунчера)

```powershell
# вход офлайн-ником
$r = Invoke-RestMethod -Method Post -Uri "https://<адрес>/api/login" `
     -ContentType "application/json" -Body '{"offline":"Steve"}'
$s = $r.session

# сказать "я онлайн"
Invoke-RestMethod -Method Post -Uri "https://<адрес>/api/heartbeat" `
     -Headers @{ "X-Session" = $s } -ContentType "application/json" `
     -Body '{"name":"Steve"}'

# список плащей
Invoke-RestMethod -Uri "https://<адрес>/api/capes"

# добавить друга
Invoke-RestMethod -Method Post -Uri "https://<адрес>/api/friends/add" `
     -Headers @{ "X-Session" = $s } -ContentType "application/json" `
     -Body '{"name":"Alex"}'

# кто у меня в друзьях и кто онлайн
Invoke-RestMethod -Uri "https://<адрес>/api/friends" -Headers @{ "X-Session" = $s }
```

## Все ручки

| Метод | Путь | Кто дёргает | Что делает |
|---|---|---|---|
| GET | `/` | Render | проверка живости |
| GET | `/api/version` | лаунчер | версия сервиса |
| POST | `/api/login` | лаунчер | сессия по токену Minecraft или офлайн-нику |
| POST | `/api/heartbeat` | лаунчер | «я онлайн», раз в 30 секунд |
| GET | `/api/friends` | лаунчер | друзья и кто из них онлайн |
| POST | `/api/friends/add` | лаунчер | добавить друга по нику |
| POST | `/api/friends/del` | лаунчер | убрать друга |
| GET | `/api/capes` | лаунчер | каталог плащей (`?sort=popular\|new\|name`) |
| GET | `/api/capes/mine` | лаунчер | что доступно лично мне |
| POST | `/api/cape/set` | лаунчер | надеть плащ |
| POST | `/api/cape/upload` | лаунчер | залить свой плащ |
| GET | `/api/capes/bulk` | **мод** | какие плащи у этих игроков |
| GET | `/capes/<id>.png` | **мод** | картинка плаща |
| POST | `/api/skin/set` | лаунчер | сохранить скин без лицензии (base64, 64x64) |
| POST | `/api/skin/clear` | лаунчер | убрать скин без лицензии |
| GET | `/api/skin/mine` | лаунчер | мой скин (url, model) |
| GET | `/api/skins/bulk` | **мод** | какие скины у этих игроков |
| GET | `/skins/<uuid>.png` | **мод** | картинка скина |
| POST | `/api/admin/cape` | админ | добавить плащ в общий каталог |
| POST | `/api/admin/del` | админ | убрать плащ из каталога |

## Почему нужна сессия, а не просто ник

Плащи и статус видны всем на сервере, поэтому назваться чужим ником нельзя.
Если игрок вошёл в лаунчер с лицензией — uuid подтверждает сам Mojang
(сервис спрашивает `api.minecraftservices.com/minecraft/profile`), и тогда
поставить чужой ник невозможно. Офлайн-вход тоже работает: uuid считается
из ника так же, как это делает сама игра, но в профиле остаётся пометка
`offline`.
