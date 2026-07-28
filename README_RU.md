# ITubeP

> ⚠️ **Work in progress.** Проект в активной разработке, ломающие изменения возможны в любой момент, часть функциональности сырая или недоделанная. Используйте на свой страх и риск, багрепорты и PR приветствуются.

Аналог YouTube поверх сети [I2P](https://geti2p.net/), без централизованного хостинга видео — раздача идёт по BitTorrent (через `i2psnark`), сайт хранит только метаданные (каналы, манифесты видео, поисковый индекс).

## Идея и архитектура

Проект состоит из двух частей:

- **Сайт (`site/`)** — веб-приложение на FastAPI + PostgreSQL. Публично доступен как обычный eepsite (`.i2p`-домен). Хранит только метаданные: зарегистрированные каналы (публичный ключ + подпись), манифесты видео (название, описание, инфо-хэши торрентов по качествам) и раздаёт сами `.torrent`-файлы. Само видео на сайте не лежит.
- **Мост (`bridge/`)** — клиентское приложение, работает локально у автора/зрителя. Отвечает за:
  - публикацию: нарезку видео на HLS-сегменты (`ffmpeg`), сборку `.torrent` и отправку манифеста на сайт;
  - раздачу собственных опубликованных видео через `i2psnark` (автор публикации сразу становится сидером);
  - докачку и просмотр чужих видео (по запросу сайта через локальный HTTP API моста, `http://127.0.0.1:9080`);
  - управление сопряжением (pairing) — какие сайты (origin) вообще имеют право попросить мост что-то скачать/раздать.

Условно:
- **сайт** — то, что можно поднять и держать постоянно включённым (для этого и написан гайд ниже, i2pd + nginx + постоянно работающая машина);
- **мост** — клиентское приложение массовой аудитории. Оно пока не идеально, но это основной интерфейс для тех, кто просто хочет смотреть и публиковать видео, не поднимая свой сайт.

Плеер на странице видео тянет данные не с сайта напрямую, а обращается к локальному мосту зрителя (`localhost:9080`) — именно мост скачивает сегменты по BitTorrent и отдаёт их плееру (`hls.js`) по мере докачки. Если моста нет или он не запущен — сайт предлагает fallback: скачать `.torrent`-файлы руками и смотреть в любом BitTorrent-клиенте с поддержкой I2P.

## Требования

Только Linux (`bridge/install.sh` поддерживает Debian/Ubuntu, Fedora/RHEL/CentOS/Rocky/Alma, Arch/Manjaro, openSUSE).

Понадобятся:
- работающий I2P-роутер (`i2pd` или Java I2P) — для сайта и для моста;
- Python 3;
- PostgreSQL — только для сайта;
- `ffmpeg` — только для моста (нарезка видео на сегменты).

## Установка моста (клиент — публикация и просмотр)

```bash
git clone https://github.com/i2ptuber/itubep.git
cd itubep/bridge
chmod +x install.sh
./install.sh
```

Скрипт идемпотентен (повторный запуск пропускает уже сделанные шаги, `--rebuild` форсирует пересборку `i2psnark`/RPC) и сам:

- определяет пакетный менеджер и ставит базовые зависимости (`ffmpeg`, JDK и т.д.);
- находит уже установленный I2P-роутер (i2pd или Java I2P) либо предлагает поставить один из них;
- собирает `i2psnark` standalone + I2PSnark-RPC **из исходников** `i2p.i2p` (не зависит от сторонних сборок);
- ставит Python-окружение моста (venv + зависимости);
- настраивает автозапуск — `systemd --user`, если доступен, иначе `cron @reboot` + pid-файлы.

После установки доступна команда `itubep-ctl`:

```bash
itubep-ctl start-all      # запустить i2psnark + мост
itubep-ctl status         # проверить статус
itubep-ctl stop-all       # остановить всё
itubep-ctl settings       # окно настроек / сопряжение с сайтами
itubep-ctl pairings       # управление уже выданными сопряжениями
```

Мост слушает `http://127.0.0.1:9080` — с ним общаются страницы сайта (публикация и плеер) через браузер.

## Установка сайта (свой eepsite)

Сайт — это обычное FastAPI-приложение, разворачивается как любой Python-сервис, а наружу в I2P смотрит через ваш I2P-роутер (туннель) — например, через `nginx` перед `uvicorn` и i2pd-туннель на этот `nginx`.

1. PostgreSQL (см. `site/important.txt`):

```bash
sudo apt install postgresql postgresql-contrib
sudo -u postgres createuser --pwprompt itubep
sudo -u postgres createdb -O itubep itubep
```

2. Python-окружение и зависимости:

```bash
cd itubep/site
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Переменные окружения:

```bash
export ITUBEP_DATABASE_URL="postgresql+asyncpg://itubep:PASSWORD@127.0.0.1:5432/itubep"
export ITUBEP_SITE_ORIGIN="http://itubep.i2p"
```

`ITUBEP_DATABASE_URL` не обязателен (без него используется дефолт с паролем-плейсхолдером `PASSWORD`, который заведомо не подойдёт к вашей БД) — но фактически без него сайт просто не подключится к PostgreSQL.

`ITUBEP_SITE_ORIGIN` **строго обязателен** и должен быть указан **точно** так, как ваш сайт виден снаружи — тот же адрес (`http://` + b32-адрес или заведённое в адресной книге "дружественное" `.i2p`-имя, без хвостового `/`), который браузер отправляет как заголовок `Origin`, обращаясь к сайту через I2P. Без него (или при малейшем расхождении — протокол, регистр, хвостовой слэш) сайт **осознанно** отклоняет вообще все подписанные запросы моста (лайки/дизлайки, комментарии, обновления студии — код 403, "audience_origin записи не совпадает с адресом этого сайта") — это защита от переиспользования подписи, отправленной мостом для одного сайта, на другом. Это не баг, а fail-closed по умолчанию: без переменной сайт скорее откажет в приёме, чем примет что-то не то. Если после настройки эти действия всё равно не работают — сверьте `ITUBEP_SITE_ORIGIN` с тем, что реально приходит в заголовке `Origin` (это видно в логах моста или в DevTools браузера).

4. Запуск (таблицы создаются автоматически при старте — прототип-режим, без Alembic):

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Это разовый запуск для проверки, что всё поднимается. Для постоянной эксплуатации сайт должен автоматически перезапускаться при падении и при перезагрузке сервера — см. раздел "Автозагрузка сайта" ниже.

5. Публикация в I2P — заведите отдельный eepsite-туннель (i2pd/Java I2P) на `127.0.0.1:8000` напрямую, либо (рекомендуется для реальной эксплуатации) поставьте `nginx` перед `uvicorn` и наведите туннель уже на `nginx`.

### Автозагрузка сайта

Вариант ниже — для дистрибутивов с systemd (Debian, Ubuntu, большинство современных серверных ОС). Если у вас его нет (Alpine, старые/минималистичные системы, некоторые контейнерные окружения) — см. альтернативы после.

**systemd**

Создайте `/etc/systemd/system/itubep-site.service` (пути и пользователя поправьте под себя — сервис не должен работать от root):

```ini
[Unit]
Description=ITubeP site (FastAPI)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=itubep
Group=itubep
WorkingDirectory=/home/itubep/itubep/site
Environment=ITUBEP_DATABASE_URL=postgresql+asyncpg://itubep:PASSWORD@127.0.0.1:5432/itubep
Environment=ITUBEP_SITE_ORIGIN=http://itubep.i2p
Environment=ITUBEP_DB_POOL_SIZE=10
Environment=ITUBEP_DB_MAX_OVERFLOW=20
ExecStart=/home/itubep/itubep/site/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Важно: значения `Environment=` в юните — это **не** то же самое, что `export` в вашем shell при ручном запуске. systemd не читает переменные из вашей интерактивной сессии — их нужно прописать в юните явно (как выше) либо через `EnvironmentFile=/etc/itubep/site.env` с обычным `KEY=value` построчно в этом файле (удобнее, если переменных станет больше, и не хочется хранить пароль от БД в самом юните).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now itubep-site
sudo systemctl status itubep-site         # убедиться, что стартовал без ошибок
journalctl -u itubep-site -f              # логи в реальном времени
```

После любой правки юнита или `EnvironmentFile` — `daemon-reload` и `restart` обязательны, простой `restart` без `daemon-reload` подхватит правки самого юнита не всегда.

**Без systemd**

- **Docker**: если у вас уже есть PostgreSQL и i2pd/I2P-роутер отдельно, самый простой вариант — обернуть `uvicorn app.main:app --host 0.0.0.0 --port 8000` в `Dockerfile` (базовый образ `python:3.x-slim`, `pip install -r requirements.txt`, `COPY`, `CMD`) и передавать `ITUBEP_DATABASE_URL`/`ITUBEP_SITE_ORIGIN` через `docker run -e ...` или `environment:` в `docker-compose.yml`. Перезапуск при падении — `restart: unless-stopped` в compose или `--restart unless-stopped` в `docker run`.
- **supervisord**: секция в `/etc/supervisor/conf.d/itubep-site.conf`:
  ```ini
  [program:itubep-site]
  directory=/home/itubep/itubep/site
  command=/home/itubep/itubep/site/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
  environment=ITUBEP_DATABASE_URL="postgresql+asyncpg://itubep:PASSWORD@127.0.0.1:5432/itubep",ITUBEP_SITE_ORIGIN="http://itubep.i2p"
  autostart=true
  autorestart=true
  user=itubep
  ```
  затем `supervisorctl reread && supervisorctl update`.
- **OpenRC** (Alpine и т.п.): скрипт в `/etc/init.d/itubep-site` со `supervise-daemon`, переменные — через `export` в самом init-скрипте перед вызовом `command`, либо `/etc/conf.d/itubep-site` (аналог `EnvironmentFile`).
- **runit/s6**: `run`-скрипт, начинающийся с `#!/bin/sh` и `exec env ITUBEP_DATABASE_URL=... ITUBEP_SITE_ORIGIN=... /path/to/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000` (или отдельный `env/`-каталог с файлами-значениями, в зависимости от реализации).
- **screen/tmux** — подходит только для разовых тестов, НЕ для боевой эксплуатации: без супервизора процесс не переживёт падение или перезагрузку сервера.

Общий принцип для любого из вариантов: переменные окружения должны быть выставлены **для процесса `uvicorn`**, а не только в вашей терминальной сессии — супервизор (systemd/supervisord/docker/...) запускает процесс отдельно и вашего `export` не увидит, если явно не передать их через его собственный механизм переменных окружения.

### Масштабирование под рост числа пользователей

Сайт не раздаёт само видео (это делает i2psnark каждого зрителя по P2P) — HTTP-нагрузка на сайт лёгкая (HTML/JSON/`.torrent`-метаданные), поэтому обычно узкое место — не CPU/RAM, а число воркеров `uvicorn` и пул соединений к PostgreSQL.

**1. Несколько воркеров `uvicorn`.** По умолчанию в инструкции выше — один процесс. Флаг `--workers N` (как в примере systemd-юнита) поднимает N процессов, разделяющих один listen-socket — это даёт параллелизм на несколько ядер вместо одного asyncio event loop. Разумная отправная точка — число ядер CPU (для i3 12-го поколения это обычно 4 воркера).

**2. Пул соединений к PostgreSQL.** Раньше `create_async_engine` не получал никаких параметров пула, и SQLAlchemy молча брала дефолт `pool_size=5 + max_overflow=10 = 15` соединений **на один процесс**. Теперь это настраивается через переменные окружения (тоже в `Environment=` юнита или в `EnvironmentFile`):

```bash
ITUBEP_DB_POOL_SIZE=10       # базовый размер пула на процесс
ITUBEP_DB_MAX_OVERFLOW=20    # сколько сверх пула допустимо при всплеске
ITUBEP_DB_POOL_TIMEOUT=10    # секунд ждать свободное соединение, прежде чем вернуть ошибку
ITUBEP_DB_POOL_RECYCLE=1800  # пересоздавать соединения старше 30 минут (защита от "server closed the connection unexpectedly")
```

**Важно — считайте общий итог, а не только цифры на процесс:**

```
(ITUBEP_DB_POOL_SIZE + ITUBEP_DB_MAX_OVERFLOW) × число воркеров uvicorn
    должно быть заметно МЕНЬШЕ max_connections в postgresql.conf
```

С примером выше (`pool_size=10`, `max_overflow=20`, `--workers 4`) это `(10+20)×4 = 120` — уже **больше** дефолтного `max_connections=100` в PostgreSQL, и часть запросов на соединение будет падать по `ITUBEP_DB_POOL_TIMEOUT`. Либо уменьшите `ITUBEP_DB_POOL_SIZE`/`ITUBEP_DB_MAX_OVERFLOW`, либо увеличьте `max_connections` в `postgresql.conf` (`/etc/postgresql/*/main/postgresql.conf`) и перезапустите PostgreSQL — с запасом под служебные `scripts/*` и суперпользователя, например `max_connections = 150`.

Проверить текущий лимит и как он используется:

```bash
sudo -u postgres psql -c "SHOW max_connections;"
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"   # сколько занято прямо сейчас
```

6. Служебные CLI-скрипты сайта (запускать из `site/`, с активным venv):

```bash
python3 -m scripts.configure_limits list          # посмотреть/настроить rate-limit бюджеты
python3 -m scripts.moderate list-videos           # модерация: список видео / бан канала / удаление
```

Оба скрипта работают напрямую с БД (без HTTP и токенов) — запускать нужно на том же хосте, где крутится сайт, или через SSH-туннель к БД.

7. Трекеры для быстрого старта раздачи — список announce-URL живых I2P BT-трекеров задаётся в настройках сайта (`get_trackers`/`set_trackers`, хранится в БД) и подставляется в каждый публикуемый `.torrent`. Без трекеров раздача тоже работает (через DHT/PEX i2psnark), но первый пир для только что опубликованного видео находится заметно дольше.

### Админ-панель (модерация)

`site/admin/` — это **отдельное** веб-приложение от публичного сайта (`app/main.py`): свой процесс `uvicorn`, свой порт, общие с сайтом только база данных и логика модерации из `app/moderation_service.py` (те же функции, что использует `scripts/moderate.py`). Через неё можно искать и просматривать видео и каналы, удалять контент и банить каналы через UI вместо CLI.

У неё нет своего логина/пароля — контроль доступа сводится к тому, дотягивается ли кто-то до порта вообще, поэтому она **никогда не должна** быть доступна из I2P или из интернета. Запускать только на loopback, из `site/`, с тем же venv и теми же переменными окружения, что и основной сайт:

```bash
cd itubep/site
source venv/bin/activate
python3 -m uvicorn admin.app:app --host 127.0.0.1 --port 8877
```

Доступ со своей машины — через SSH-туннель (сам порт наружу не открывать, и не заводить на него i2pd/nginx-туннель):

```bash
ssh -L 8877:127.0.0.1:8877 you@your-server
```

затем открыть `http://127.0.0.1:8877` в своём браузере.

Для постоянной работы запускайте её под тем же супервизором, что и основной сайт (см. «Автозагрузка сайта» выше) — например, отдельный systemd-юнит `itubep-admin.service`, идентичный `itubep-site.service`, но с `ExecStart=.../venv/bin/uvicorn admin.app:app --host 127.0.0.1 --port 8877` и без `Environment=ITUBEP_SITE_ORIGIN` (админка его не проверяет). Флаг `--host 127.0.0.1` в юните нужен в любом случае — приложение выводит предупреждение при старте, если `UVICORN_HOST` выставлен во что-то другое, но это подстраховка, а не замена правильного флага.

## Статус проекта

Уже работает: регистрация каналов, публикация видео с моста, раздача/докачка через `i2psnark`, просмотр через HLS-плеер в браузере с фолбэком на скачивание `.torrent`.

В процессе допиливания: клиентский мост (стабильность GUI, обработка ошибок сети I2P), приоритизация сегментов при перемотке, миграции БД сайта (сейчас — только `create_all`, без Alembic).

Идеи, баг-репорты и пул-реквесты приветствуются.
