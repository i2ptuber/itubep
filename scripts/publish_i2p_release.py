#!/usr/bin/env python3
"""
publish_i2p_release.py — публикует артефакты релиза на git.community.i2p
(Gitea-инстанс в сети I2P) и обновляет секцию artifacts.i2p в
updates/latest.json.

ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ СКРИПТ, А НЕ ЧАСТЬ GitHub Actions:
GitHub Actions выполняется на раннерах GitHub в обычном clearnet — у них
нет I2P-роутера и нет маршрута до .i2p-адресов в принципе (это не вопрос
настройки firewall, это другая сеть). Поэтому автоматизировать публикацию
на git.community.i2p через GitHub Actions невозможно: должен быть процесс
на машине, у которой I2P-доступ есть — то есть на вашей.

Запускать ПОСЛЕ того, как:
  1) собраны финальные артефакты релиза (linux tar.gz, windows exe/msi);
  2) они уже опубликованы в GitHub Release (и, соответственно,
     .github/workflows/release-manifest.yml уже прогнался и заполнил
     секцию artifacts.clearnet в updates/latest.json) — скрипт сверяет
     sha256 локального файла с тем, что уже лежит в clearnet-секции, и
     предупреждает, если они разошлись (значит это НЕ тот же файл).

Требования:
  - Локально настроенный I2P-роутер (i2pd/Java I2P) с HTTP-прокси
    (обычно 127.0.0.1:4444) и туннель до git.community.i2p должен быть
    рабочим (тот же прокси, которым пользуется сам мост).
  - Токен доступа Gitea с правом на создание релизов в этом репозитории
    (Gitea -> Settings -> Applications -> Generate New Token), передаётся
    через переменную окружения GITEA_TOKEN — никогда не хардкодить в коде
    и не коммитить.

Использование:
    export GITEA_TOKEN=...
    python3 scripts/publish_i2p_release.py v0.1.0 \
        --linux dist/itubep-bridge-linux-v0.1.0.tar.gz \
        --windows dist/itubep-bridge-windows-v0.1.0.exe

Флаги --linux/--windows опциональны (можно публиковать только одну
платформу за раз, например, пока Windows-порта ещё нет).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "updates" / "latest.json"

GITEA_BASE = "http://git.community.i2p"
GITEA_OWNER = "tuber"
GITEA_REPO = "itubep"

DEFAULT_I2P_PROXY = "http://192.168.0.111:4444"

ARTIFACT_NAME_PATTERN = "itubep-bridge-{platform}-{tag}{suffix}"


def sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def gitea_session(proxy: str) -> requests.Session:
    session = requests.Session()
    session.proxies = {"http": proxy, "https": proxy}
    return session


def get_or_create_release(session: requests.Session, token: str, tag: str) -> dict:
    """Возвращает JSON релиза Gitea (создаёт, если его ещё нет для этого тэга)."""
    headers = {"Authorization": f"token {token}"}
    url = f"{GITEA_BASE}/api/v1/repos/{GITEA_OWNER}/{GITEA_REPO}/releases/tags/{tag}"
    resp = session.get(url, headers=headers, timeout=60)
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code != 404:
        resp.raise_for_status()

    # Релиза с таким тэгом ещё нет на Gitea — создаём. Тэг в git должен уже
    # существовать (тот же тэг, что вы запушили на GitHub) — Gitea сам его
    # подтянет из зеркала репозитория при условии, что git.community.i2p
    # синхронизирован с main/тэгами (см. README про push-зеркалирование).
    create_url = f"{GITEA_BASE}/api/v1/repos/{GITEA_OWNER}/{GITEA_REPO}/releases"
    resp = session.post(
        create_url,
        headers=headers,
        json={"tag_name": tag, "name": tag, "draft": False, "prerelease": False},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def upload_asset(session: requests.Session, token: str, release_id: int, path: Path) -> dict:
    headers = {"Authorization": f"token {token}"}
    url = (
        f"{GITEA_BASE}/api/v1/repos/{GITEA_OWNER}/{GITEA_REPO}"
        f"/releases/{release_id}/assets"
    )
    with open(path, "rb") as f:
        resp = session.post(
            url,
            headers=headers,
            params={"name": path.name},
            files={"attachment": (path.name, f)},
            timeout=600,
        )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", help="Тэг релиза, например v0.1.0 (должен уже существовать в git)")
    parser.add_argument("--linux", type=Path, help="Путь к собранному линукс-артефакту (.tar.gz)")
    parser.add_argument("--windows", type=Path, help="Путь к собранному windows-артефакту (.exe/.msi)")
    parser.add_argument("--proxy", default=DEFAULT_I2P_PROXY, help=f"HTTP-прокси I2P (по умолчанию {DEFAULT_I2P_PROXY})")
    parser.add_argument("--token-env", default="GITEA_TOKEN", help="Имя переменной окружения с токеном Gitea")
    args = parser.parse_args()

    import os
    token = os.environ.get(args.token_env)
    if not token:
        sys.exit(f"Переменная окружения {args.token_env} не задана — нужен токен доступа Gitea.")

    artifacts = {}
    if args.linux:
        artifacts["linux"] = args.linux
    if args.windows:
        artifacts["windows"] = args.windows
    if not artifacts:
        sys.exit("Укажите хотя бы один артефакт: --linux и/или --windows.")

    for platform_key, path in artifacts.items():
        if not path.exists():
            sys.exit(f"Файл не найден: {path}")

    if not MANIFEST_PATH.exists():
        sys.exit(f"Не найден {MANIFEST_PATH} — запускайте скрипт из клонированного репозитория.")
    manifest = json.loads(MANIFEST_PATH.read_text())

    # Сверка с уже опубликованными clearnet-хэшами (если они есть для этой
    # версии) — просто предупреждение, не блокирует публикацию, но ловит
    # ситуацию "собрал artefact заново, а он вышел другой" (нестабильная
    # сборка, забыли перегенерировать один из двух артефактов и т.п.).
    clearnet_artifacts = manifest.get("artifacts", {}).get("clearnet", {})

    session = gitea_session(args.proxy)

    print(f"Подключаюсь к {GITEA_BASE} через прокси {args.proxy}...")
    release = get_or_create_release(session, token, args.tag)
    release_id = release["id"]
    print(f"Релиз {args.tag} на Gitea: id={release_id}")

    i2p_section = manifest.setdefault("artifacts", {}).setdefault("i2p", {})

    for platform_key, path in artifacts.items():
        local_sha256 = sha256_of(path)
        clearnet_entry = clearnet_artifacts.get(platform_key)
        if clearnet_entry and clearnet_entry.get("sha256", "").lower() != local_sha256:
            print(
                f"  ВНИМАНИЕ: sha256 файла {path.name} НЕ совпадает с тем, "
                f"что уже опубликовано в clearnet-секции манифеста для "
                f"{platform_key}. Убедитесь, что это действительно тот же "
                f"билд, прежде чем продолжать.",
                file=sys.stderr,
            )

        print(f"  Загружаю {path.name} ({platform_key})...")
        asset = upload_asset(session, token, release_id, path)
        download_url = asset.get("browser_download_url") or (
            f"{GITEA_BASE}/{GITEA_OWNER}/{GITEA_REPO}/releases/download/{args.tag}/{path.name}"
        )
        i2p_section[platform_key] = {"url": download_url, "sha256": local_sha256}
        print(f"    -> {download_url}")
        print(f"    -> sha256: {local_sha256}")

    manifest["version"] = args.tag.lstrip("v")
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"\n{MANIFEST_PATH} обновлён локально.")
    print("Дальше: проверьте diff, закоммитьте и запушьте на GitHub вручную, например:")
    print(f"  git add {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    print(f"  git commit -m 'chore: publish {args.tag} to git.community.i2p'")
    print("  git push origin main")


if __name__ == "__main__":
    main()
