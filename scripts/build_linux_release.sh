#!/usr/bin/env bash
#
# build_linux_release.sh — собирает Linux-артефакт релиза: архив
# itubep-bridge-linux-vX.Y.Z.tar.gz, который потом:
#   1) прикладывается к GitHub Release вручную (Releases -> Draft a new
#      release -> перетащить файл в зону "Attach binaries");
#   2) заливается на git.community.i2p через scripts/publish_i2p_release.py.
#
# Это НЕ компиляция — Python не компилируется в бинарник этим скриптом
# (это отдельная, более сложная задача, актуальная скорее для Windows-
# порта, см. обсуждение PyInstaller/Nuitka). Здесь мы просто аккуратно
# упаковываем исходники bridge/ в архив, который install.sh пользователя
# распакует и соберёт/запустит у себя, как и раньше.
#
# Использование:
#   ./scripts/build_linux_release.sh v0.1.0
#
# Результат: dist/itubep-bridge-linux-v0.1.0.tar.gz

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Использование: $0 vX.Y.Z" >&2
    exit 1
fi

TAG="$1"
VERSION="${TAG#v}"  # v0.1.0 -> 0.1.0

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$REPO_ROOT/dist"
ARCHIVE_NAME="itubep-bridge-linux-${TAG}.tar.gz"
STAGE_DIR="$(mktemp -d)"
STAGE_TARGET="$STAGE_DIR/itubep-bridge-${VERSION}"

echo "Версия сборки: $VERSION (тэг $TAG)"

# Сверяем версию в __version__.py с тэгом — частая причина путаницы:
# забыли обновить VERSION перед релизом, и обновление потом никогда не
# определится как "новее" (сравнение версий в bridge/updater.py смотрит
# именно на этот файл).
CURRENT_VERSION="$(grep -oP '(?<=VERSION = ")[^"]+' "$REPO_ROOT/bridge/__version__.py" || true)"
if [ "$CURRENT_VERSION" != "$VERSION" ]; then
    echo "ВНИМАНИЕ: bridge/__version__.py содержит VERSION=\"$CURRENT_VERSION\", а вы собираете $VERSION." >&2
    echo "Обновите bridge/__version__.py перед релизом, иначе апдейтер не увидит новую версию." >&2
    read -rp "Продолжить всё равно? [y/N] " confirm
    [ "$confirm" = "y" ] || exit 1
fi

mkdir -p "$STAGE_TARGET"

# Копируем ровно то, что нужно пользователю: сам код моста + install.sh +
# лицензию. НЕ копируем: __pycache__, .git, локальные SQLite-базы
# пользователя (их и не должно быть в репозитории), venv и т.п.
# Копируем ровно то, что нужно пользователю: сам код моста + install.sh +
# лицензию. НЕ копируем: __pycache__, .git, локальные SQLite-базы
# пользователя (их и не должно быть в репозитории), venv и т.п.
# (cp -r + чистка после — не зависим от rsync, который на некоторых
# минимальных системах не установлен из коробки)
cp -r "$REPO_ROOT/bridge" "$STAGE_TARGET/bridge"
find "$STAGE_TARGET/bridge" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_TARGET/bridge" -name '*.pyc' -delete
find "$STAGE_TARGET/bridge" -name '*.db' -delete
find "$STAGE_TARGET/bridge" -name '*.db-wal' -delete
find "$STAGE_TARGET/bridge" -name '*.db-shm' -delete

cp "$REPO_ROOT/LICENSE" "$STAGE_TARGET/" 2>/dev/null || true
cp "$REPO_ROOT/README.md" "$STAGE_TARGET/" 2>/dev/null || true

mkdir -p "$DIST_DIR"
tar -czf "$DIST_DIR/$ARCHIVE_NAME" -C "$STAGE_DIR" "itubep-bridge-${VERSION}"

rm -rf "$STAGE_DIR"

echo
echo "Готово: $DIST_DIR/$ARCHIVE_NAME"
echo "Размер: $(du -h "$DIST_DIR/$ARCHIVE_NAME" | cut -f1)"
echo "SHA256: $(sha256sum "$DIST_DIR/$ARCHIVE_NAME" | cut -d' ' -f1)"
echo
echo "Дальше:"
echo "  1) Прикрепите этот файл к GitHub Release для тэга $TAG"
echo "  2) Запустите: python3 scripts/publish_i2p_release.py $TAG --linux $DIST_DIR/$ARCHIVE_NAME"
