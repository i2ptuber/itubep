# ITubeP

> ⚠️ **Work in progress.** The project is under active development, breaking changes can happen at any time, and some functionality is rough or incomplete. Use at your own risk — bug reports and PRs are welcome.

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

3. Environment variables — at minimum, the database connection string:

```bash
export ITUBEP_DATABASE_URL="postgresql+asyncpg://itubep:PASSWORD@127.0.0.1:5432/itubep"
```

4. Run it (tables are created automatically on startup — prototype mode, no Alembic yet):

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

5. Publishing to I2P — set up a separate eepsite tunnel (i2pd/Java I2P) pointing directly at `127.0.0.1:8000`, or (recommended for real-world use) put `nginx` in front of `uvicorn` and point the tunnel at `nginx` instead.

6. Site maintenance CLI scripts (run from `site/`, with the venv active):

```bash
python3 -m scripts.configure_limits list          # view/configure rate-limit budgets
python3 -m scripts.moderate list-videos           # moderation: list videos / ban a channel / remove content
```

Both scripts work directly against the database (no HTTP, no tokens) — run them on the same host the site runs on, or over an SSH tunnel to the database.

7. Trackers to help seeding start quickly — a list of announce URLs for live I2P BT trackers is set in the site's settings (`get_trackers`/`set_trackers`, stored in the database) and gets added to every published `.torrent`. Seeding still works without trackers (via i2psnark's DHT/PEX), but the first peer for a freshly published video takes noticeably longer to find.

## Project status

Already working: channel registration, publishing videos from the bridge, seeding/downloading via `i2psnark`, watching through the in-browser HLS player with a fallback to downloading the `.torrent`.

Still being worked on: the client bridge (GUI stability, handling I2P network errors), segment prioritization on seek, site database migrations (currently just `create_all`, no Alembic yet).

Ideas, bug reports, and pull requests are welcome.
