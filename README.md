# ITubeP

A YouTube-like service running on top of the [I2P](https://geti2p.net/) network, with no centralized video hosting — delivery happens over BitTorrent (via `i2psnark`), while the site only stores metadata (channels, video manifests, search index).

## Concept and architecture

The project consists of two parts:

- **Site (`site/`)** — a web application built with FastAPI + PostgreSQL. Publicly available as a regular eepsite (a `.i2p` domain). It only stores metadata: registered channels (public key + signature), video manifests (title, description, torrent info-hashes per quality), and serves the `.torrent` files themselves. The actual video is never stored on the site.
- **Bridge (`bridge/`)** — a client application that runs locally on the author's/viewer's machine. It's responsible for:
  - publishing: splitting a video into HLS segments (`ffmpeg`), building the `.torrent`, and sending the manifest to the site;
  - seeding your own published videos via `i2psnark` (the publisher immediately becomes a seeder);
  - downloading and watching other people's videos (on the site's request, via the bridge's local HTTP API, `http://127.0.0.1:9080`);
  - managing pairing — which sites (origins) are even allowed to ask the bridge to download/seed anything.

Roughly speaking:
- the **site** is something you can spin up and keep running permanently (that's what the guide below is for — i2pd + nginx + an always-on machine);
- the **bridge** is the client app for the general audience. It's not perfect yet, but it's the main interface for people who just want to watch and publish videos without running their own site.

The player on the video page doesn't pull data directly from the site — instead it talks to the viewer's local bridge (`localhost:9080`), which downloads the segments over BitTorrent and feeds them to the player (`hls.js`) as they arrive. If the bridge isn't installed or isn't running, the site falls back to letting you download the `.torrent` files manually and watch them in any BitTorrent client with I2P support.

## Requirements

Linux only (`bridge/install.sh` supports Debian/Ubuntu, Fedora/RHEL/CentOS/Rocky/Alma, Arch/Manjaro, openSUSE).

You'll need:
- a running I2P router (`i2pd` or Java I2P) — for both the site and the bridge;
- Python 3;
- PostgreSQL — for the site only;
- `ffmpeg` — for the bridge only (splitting video into segments).

## Installing the bridge (client — publishing and watching)

```bash
git clone https://github.com/i2ptuber/itubep.git
cd itubep/bridge
chmod +x install.sh
./install.sh
```

The script is idempotent (re-running it skips steps already done; `--rebuild` forces `i2psnark`/RPC to be rebuilt) and will automatically:

- detect your package manager and install base dependencies (`ffmpeg`, a JDK, etc.);
- look for an already-installed I2P router (i2pd or Java I2P), or offer to install one;
- build standalone `i2psnark` + I2PSnark-RPC **from the `i2p.i2p` source** (no dependency on third-party builds);
- set up the bridge's Python environment (venv + dependencies);
- configure autostart — `systemd --user` if available, otherwise `cron @reboot` + pid files.

After installation, the `itubep-ctl` command becomes available:

```bash
itubep-ctl start-all      # start i2psnark + the bridge
itubep-ctl status         # check status
itubep-ctl stop-all       # stop everything
itubep-ctl settings       # settings window / pairing with sites
itubep-ctl pairings       # manage already-granted pairings
```

The bridge listens on `http://127.0.0.1:9080` — site pages (the publishing flow and the player) talk to it through the browser.

## Installing the site (running your own eepsite)

The site is a regular FastAPI application, deployed like any other Python service, and exposed to I2P through your I2P router (a tunnel) — for example, via `nginx` in front of `uvicorn`, with an i2pd tunnel pointed at that `nginx`.

1. PostgreSQL (see `site/important.txt`):

```bash
sudo apt install postgresql postgresql-contrib
sudo -u postgres createuser --pwprompt itubep
sudo -u postgres createdb -O itubep itubep
```

2. Python environment and dependencies:

```bash
cd itubep/site
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Environment variables:

```bash
export ITUBEP_DATABASE_URL="postgresql+asyncpg://itubep:PASSWORD@127.0.0.1:5432/itubep"
export ITUBEP_SITE_ORIGIN="http://itubep.i2p"
```

`ITUBEP_DATABASE_URL` isn't strictly required (a placeholder default with the password `PASSWORD` is used if it's unset, which certainly won't match your database) — but in practice the site won't connect to PostgreSQL without it.

`ITUBEP_SITE_ORIGIN` **is required**. Set it to exactly how your site is seen from the outside (`http://` + either the b32 address or a "friendly" `.i2p` name registered in an addressbook, no trailing `/`) — the same value a browser sends as the `Origin` header when it reaches the site over I2P. Without it (or with even a small mismatch — protocol, case, trailing slash) the site **deliberately** rejects every signed request coming from the bridge (likes/dislikes, comments, studio updates — HTTP 403, "audience_origin записи не совпадает с адресом этого сайта"). This is intentional fail-closed behavior, not a bug: without the variable, the site would rather refuse than accept a signature that might have been issued for a different site. If these actions still don't work after setting it, compare `ITUBEP_SITE_ORIGIN` against what actually arrives in the `Origin` header (visible in the bridge's logs or in your browser's DevTools).

4. Run it (tables are created automatically on startup — prototype mode, no Alembic yet):

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

This is a one-off run to confirm everything starts cleanly. For real deployment, the site needs to restart automatically on crash and on reboot — see "Running the site on boot" below.

5. Publishing to I2P — set up a separate eepsite tunnel (i2pd/Java I2P) pointing directly at `127.0.0.1:8000`, or (recommended for real-world use) put `nginx` in front of `uvicorn` and point the tunnel at `nginx` instead.

### Running the site on boot

The section below is for systemd-based distros (Debian, Ubuntu, most modern server OSes). If you don't have systemd (Alpine, minimal systems, some container setups), see the alternatives further down.

**systemd**

Create `/etc/systemd/system/itubep-site.service` (adjust paths and the user to your setup — don't run this as root):

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

Important: `Environment=` lines in the unit are **not** the same as `export`-ing variables in your own shell when you run things by hand. systemd doesn't read your interactive session's environment — variables must be declared explicitly in the unit (as above), or via `EnvironmentFile=/etc/itubep/site.env` pointing at a plain `KEY=value` file (handy once you have more variables, or if you'd rather not keep the database password in the unit file itself).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now itubep-site
sudo systemctl status itubep-site         # confirm it started without errors
journalctl -u itubep-site -f              # live logs
```

After any edit to the unit or its `EnvironmentFile`, `daemon-reload` followed by `restart` is required — a plain `restart` alone doesn't always pick up unit-file changes.

**Without systemd**

- **Docker**: if you already run PostgreSQL and an i2pd/I2P router separately, the simplest option is wrapping `uvicorn app.main:app --host 0.0.0.0 --port 8000` in a `Dockerfile` (base image `python:3.x-slim`, `pip install -r requirements.txt`, `COPY`, `CMD`), and passing `ITUBEP_DATABASE_URL`/`ITUBEP_SITE_ORIGIN` via `docker run -e ...` or `environment:` in `docker-compose.yml`. For restart-on-crash, use `restart: unless-stopped` in compose or `--restart unless-stopped` with `docker run`.
- **supervisord**: a section in `/etc/supervisor/conf.d/itubep-site.conf`:
  ```ini
  [program:itubep-site]
  directory=/home/itubep/itubep/site
  command=/home/itubep/itubep/site/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
  environment=ITUBEP_DATABASE_URL="postgresql+asyncpg://itubep:PASSWORD@127.0.0.1:5432/itubep",ITUBEP_SITE_ORIGIN="http://itubep.i2p"
  autostart=true
  autorestart=true
  user=itubep
  ```
  then `supervisorctl reread && supervisorctl update`.
- **OpenRC** (Alpine etc.): an init script at `/etc/init.d/itubep-site` using `supervise-daemon`, with variables either `export`-ed in the init script itself before invoking `command`, or set in `/etc/conf.d/itubep-site` (the OpenRC equivalent of `EnvironmentFile`).
- **runit/s6**: a `run` script starting with `#!/bin/sh` that does `exec env ITUBEP_DATABASE_URL=... ITUBEP_SITE_ORIGIN=... /path/to/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000` (or a separate `env/` directory with one file per variable, depending on the implementation).
- **screen/tmux** — fine for one-off testing only, NOT for production: without a supervisor the process won't survive a crash or reboot.

The general rule for any of these: the environment variables need to be set **for the `uvicorn` process itself**, not just in your terminal session — the supervisor (systemd/supervisord/docker/...) launches the process independently and won't see your shell's `export`ed values unless you pass them through its own environment mechanism.

### Scaling for more users

The site doesn't serve the video itself (each viewer's i2psnark does that over P2P) — HTTP load on the site is light (HTML/JSON/`.torrent` metadata), so the usual bottleneck isn't CPU/RAM but the number of `uvicorn` workers and the PostgreSQL connection pool.

**1. Multiple `uvicorn` workers.** By default the instructions above run a single process. The `--workers N` flag (as in the systemd unit example) spawns N processes sharing one listen socket, giving you parallelism across cores instead of a single asyncio event loop. A reasonable starting point is your CPU's core count (4 workers for a 12th-gen i3 is typical).

**2. PostgreSQL connection pool.** Previously `create_async_engine` got no pool parameters, so SQLAlchemy silently used its default `pool_size=5 + max_overflow=10 = 15` connections **per process**. This is now configurable via environment variables (also set via `Environment=` in the unit, or an `EnvironmentFile`):

```bash
ITUBEP_DB_POOL_SIZE=10       # base pool size per process
ITUBEP_DB_MAX_OVERFLOW=20    # how many extra connections are allowed during a burst
ITUBEP_DB_POOL_TIMEOUT=10    # seconds to wait for a free connection before raising
ITUBEP_DB_POOL_RECYCLE=1800  # recycle connections older than 30 min (avoids "server closed the connection unexpectedly")
```

**Important — size these against the total, not just the per-process numbers:**

```
(ITUBEP_DB_POOL_SIZE + ITUBEP_DB_MAX_OVERFLOW) × number of uvicorn workers
    should be comfortably BELOW max_connections in postgresql.conf
```

With the example above (`pool_size=10`, `max_overflow=20`, `--workers 4`) that's `(10+20)×4 = 120` — already **above** PostgreSQL's default `max_connections=100`, so some connection requests would start failing on `ITUBEP_DB_POOL_TIMEOUT`. Either lower `ITUBEP_DB_POOL_SIZE`/`ITUBEP_DB_MAX_OVERFLOW`, or raise `max_connections` in `postgresql.conf` (`/etc/postgresql/*/main/postgresql.conf`) and restart PostgreSQL — leaving headroom for the maintenance `scripts/*` and the superuser, e.g. `max_connections = 150`.

Check the current limit and how it's being used:

```bash
sudo -u postgres psql -c "SHOW max_connections;"
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"   # currently in use
```

6. Site maintenance CLI scripts (run from `site/`, with the venv active):

```bash
python3 -m scripts.configure_limits list          # view/configure rate-limit budgets
python3 -m scripts.moderate list-videos           # moderation: list videos / ban a channel / remove content
```

Both scripts work directly against the database (no HTTP, no tokens) — run them on the same host the site runs on, or over an SSH tunnel to the database.

7. Trackers to help seeding start quickly — a list of announce URLs for live I2P BT trackers is set in the site's settings (`get_trackers`/`set_trackers`, stored in the database) and gets added to every published `.torrent`. Seeding still works without trackers (via i2psnark's DHT/PEX), but the first peer for a freshly published video takes noticeably longer to find.

### Admin panel (moderation)

`site/admin/` is a **separate** web app from the public site (`app/main.py`) — its own `uvicorn` process, its own port, sharing only the database and the moderation logic in `app/moderation_service.py` (the same functions used by `scripts/moderate.py`). It lets you browse/search videos and channels, remove content, and ban channels through a UI instead of the CLI.

It has no login or password of its own — access control is simply "can you reach the port at all", so it **must never** be exposed to I2P or the public internet. Run it bound to loopback only, from `site/` with the same venv/environment variables as the main site:

```bash
cd itubep/site
source venv/bin/activate
python3 -m uvicorn admin.app:app --host 127.0.0.1 --port 8877
```

Reach it from your own machine over an SSH tunnel (never open the port itself, and never point an i2pd/nginx tunnel at it):

```bash
ssh -L 8877:127.0.0.1:8877 you@your-server
```

then open `http://127.0.0.1:8877` in your local browser.

For a permanent setup, run it under the same kind of supervisor as the main site (see "Running the site on boot" above) — e.g. a second systemd unit, `itubep-admin.service`, identical to `itubep-site.service` but with `ExecStart=.../venv/bin/uvicorn admin.app:app --host 127.0.0.1 --port 8877` and no `Environment=ITUBEP_SITE_ORIGIN` needed (the admin app doesn't check it). Keep `--host 127.0.0.1` in the unit regardless — the app logs a startup warning if `UVICORN_HOST` is set to anything else, but that's a backstop, not a substitute for the correct flag.

## Project status

Already working: channel registration, publishing videos from the bridge, seeding/downloading via `i2psnark`, watching through the in-browser HLS player with a fallback to downloading the `.torrent`.

Still being worked on: the client bridge (GUI stability, handling I2P network errors), segment prioritization on seek, site database migrations (currently just `create_all`, no Alembic yet).

Ideas, bug reports, and pull requests are welcome.
