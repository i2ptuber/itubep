#!/usr/bin/env bash
#
# install.sh — установщик моста ITubeP (клиентской части).
#
# Поддерживает несколько пакетных менеджеров (apt/dnf/yum/pacman/zypper —
# Debian/Ubuntu, Fedora/RHEL/CentOS/Rocky/Alma, Arch/Manjaro, openSUSE) и
# работает как с systemd, так и без него (sysvinit, OpenRC, runit и т.п. —
# автозапуск через cron @reboot + pid-файлы, без system-level init-скриптов,
# которые сильно расходятся по конвенциям между дистрибутивами).
#
# Ставит и настраивает:
#   - I2P-роутер (i2pd по умолчанию, если ничего не найдено) ИЛИ использует
#     уже установленный Java I2P / i2pd
#   - i2psnark standalone + I2PSnark-RPC, собранные ИЗ ИСХОДНИКОВ i2p.i2p —
#     не зависит от готовых сборок с gitlab.com/i2pplus
#   - сам мост (Python venv + зависимости)
#   - автозапуск (systemd --user ИЛИ cron @reboot — по обстоятельствам)
#
# Одна команда: chmod +x install.sh && ./install.sh
# Идемпотентен — повторный запуск пропускает уже сделанные шаги
# (--rebuild форсирует пересборку i2psnark/RPC).
#
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="${HOME}/.local/share/itubep-bridge"
BUILD_DIR="${WORKDIR}/build"
I2P_SRC="${BUILD_DIR}/i2p.i2p"
RPC_SRC="${BUILD_DIR}/i2p.plugins.i2psnark-rpc"
SNARK_RUN_DIR="${WORKDIR}/i2psnark-run"
VENV_DIR="${WORKDIR}/venv"
BUILD_MARKER="${BUILD_DIR}/.build_complete"
RUN_STATE_DIR="${WORKDIR}/run"      # pid-файлы
LOG_DIR="${WORKDIR}/logs"
CTL_SCRIPT="${WORKDIR}/bin/itubep-ctl"

REBUILD=0
[ "${1:-}" = "--rebuild" ] && REBUILD=1

c_green() { printf '\033[32m%s\033[0m\n' "$1"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$1"; }
c_red() { printf '\033[31m%s\033[0m\n' "$1"; }
step() { echo ""; c_green "=== $1 ==="; }
warn() { c_yellow "[!] $1"; }
die() { c_red "ОШИБКА: $1"; exit 1; }

[ "$(id -u)" -eq 0 ] && die "Не запускайте от root — скрипт сам вызовет sudo, где нужно."
command -v sudo >/dev/null || die "Нужен sudo (для установки пакетов и, если есть, systemd-сервисов)."

# ============================================================================
step "1/9: Определение пакетного менеджера"
# ============================================================================
if command -v apt-get >/dev/null 2>&1; then
    PKG_MANAGER=apt
elif command -v dnf >/dev/null 2>&1; then
    PKG_MANAGER=dnf
elif command -v yum >/dev/null 2>&1; then
    PKG_MANAGER=yum
elif command -v pacman >/dev/null 2>&1; then
    PKG_MANAGER=pacman
elif command -v zypper >/dev/null 2>&1; then
    PKG_MANAGER=zypper
else
    die "Не найден ни один из поддерживаемых пакетных менеджеров (apt/dnf/yum/pacman/zypper)."
fi
echo "Пакетный менеджер: ${PKG_MANAGER}"

pkg_install() {
    case "$PKG_MANAGER" in
        apt)    sudo apt-get install -y "$@" ;;
        dnf)    sudo dnf install -y "$@" ;;
        yum)    sudo yum install -y "$@" ;;
        pacman) sudo pacman -S --noconfirm --needed "$@" ;;
        zypper) sudo zypper --non-interactive install "$@" ;;
    esac
}

pkg_try_install_one_of() {
    for pkg in "$@"; do
        if pkg_install "$pkg" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

case "$PKG_MANAGER" in
    apt)    sudo apt-get update -qq ;;
    dnf)    sudo dnf makecache --refresh -q || true ;;
    yum)    sudo yum makecache -q || true ;;
    pacman) sudo pacman -Sy --noconfirm ;;
    zypper) sudo zypper --non-interactive refresh ;;
esac

# ============================================================================
step "2/9: Базовые зависимости"
# ============================================================================
case "$PKG_MANAGER" in
    apt)    BASE_PKGS="git ant curl unzip zip cron python3 python3-venv python3-pip python3-tk" ;;
    dnf|yum) BASE_PKGS="git ant curl unzip zip cronie python3 python3-pip python3-tkinter" ;;
    pacman) BASE_PKGS="git apache-ant curl unzip zip cronie python python-pip tk" ;;
    zypper) BASE_PKGS="git ant curl unzip zip cron python3 python3-venv python3-pip python3-tk" ;;
esac
# shellcheck disable=SC2086
pkg_install $BASE_PKGS || warn "Не все базовые пакеты установились — смотрите вывод выше, возможно потребуется доустановить вручную."

if ! command -v ffmpeg >/dev/null 2>&1; then
    pkg_install ffmpeg || warn "ffmpeg не установился автоматически (на RHEL/CentOS/Rocky обычно нужен репозиторий RPM Fusion) — публикация видео не будет работать, пока не поставите ffmpeg вручную."
fi

find_jdk_with_javac() {
    for candidate in /usr/lib/jvm/*/bin/javac /usr/lib64/jvm/*/bin/javac; do
        [ -x "$candidate" ] && dirname "$(dirname "$candidate")" && return 0
    done
    return 1
}
if ! JAVA_HOME="$(find_jdk_with_javac)"; then
    case "$PKG_MANAGER" in
        apt)    pkg_try_install_one_of default-jdk-headless openjdk-17-jdk-headless openjdk-21-jdk-headless ;;
        dnf|yum) pkg_try_install_one_of java-latest-openjdk-devel java-17-openjdk-devel java-21-openjdk-devel ;;
        pacman) pkg_try_install_one_of jdk-openjdk ;;
        zypper) pkg_try_install_one_of java-17-openjdk-devel java-21-openjdk-devel ;;
    esac
    JAVA_HOME="$(find_jdk_with_javac)" || die \
        "Не нашёл JDK с рабочим javac после установки. Поставьте JDK (не только JRE!) вручную и запустите install.sh снова."
fi
export JAVA_HOME
echo "Используется JDK: ${JAVA_HOME}"

# ============================================================================
step "3/9: Определение init-системы (для автозапуска)"
# ============================================================================
if [ -d /run/systemd/system ]; then
    INIT_SYSTEM="systemd"
else
    INIT_SYSTEM="other"
fi
echo "Init-система: ${INIT_SYSTEM}"
if [ "$INIT_SYSTEM" = "other" ]; then
    echo "systemd не обнаружен — автозапуск будет через cron (@reboot) + pid-файлы."
fi

# ============================================================================
step "4/9: Определение I2P-клиента"
# ============================================================================
I2P_MODE=""
JAVA_I2P_WEBAPPS=""
I2PD_CONF_PATH=""

pkg_is_installed() {
    case "$PKG_MANAGER" in
        apt)    dpkg -s "$1" >/dev/null 2>&1 ;;
        dnf|yum) rpm -q "$1" >/dev/null 2>&1 ;;
        pacman) pacman -Qi "$1" >/dev/null 2>&1 ;;
        zypper) rpm -q "$1" >/dev/null 2>&1 ;;
    esac
}

find_javai2p_webapps() {
    for candidate in "${HOME}/.i2p/webapps" "/var/lib/i2p/.i2p/webapps" "/var/lib/i2p/i2p-config/webapps"; do
        [ -d "$candidate" ] && { echo "$candidate"; return 0; }
    done
    return 1
}

detected_javai2p=0
detected_i2pd=0
pkg_is_installed i2p || { [ -d "${HOME}/.i2p" ] && [ -f "${HOME}/.i2p/router.config" ]; } && detected_javai2p=1
pkg_is_installed i2pd || [ -f /etc/i2pd/i2pd.conf ] && detected_i2pd=1

prompt_manual_i2p_setup() {
    # Пользователь говорит, что роутер уже есть, просто автоопределение его
    # не увидело (нестандартный путь установки, необычный дистрибутив и т.п.)
    echo ""
    echo "Какой у вас роутер?"
    echo "  1) i2pd"
    echo "  2) Java I2P (i2p.itoopie / geti2p.net)"
    read -rp "Выбор [1/2]: " manual_choice
    case "$manual_choice" in
        1)
            I2P_MODE="i2pd"
            read -rp "Путь к i2pd.conf [/etc/i2pd/i2pd.conf]: " conf_path
            I2PD_CONF_PATH="${conf_path:-/etc/i2pd/i2pd.conf}"
            [ -f "$I2PD_CONF_PATH" ] || warn "Файл ${I2PD_CONF_PATH} не найден — проверьте путь позже вручную (i2cp.enabled=true)."
            ;;
        2)
            I2P_MODE="javai2p"
            read -rp "Путь к webapps/ вашего роутера (например ~/.i2p/webapps): " webapps_path
            webapps_path="${webapps_path/#\~/$HOME}"
            if [ -d "$webapps_path" ]; then
                JAVA_I2P_WEBAPPS="$webapps_path"
            else
                warn "Директория ${webapps_path} не найдена — RPC придётся подключить вручную после установки."
            fi
            ;;
        *)
            die "Некорректный выбор."
            ;;
    esac
}

if [ "$detected_javai2p" -eq 1 ] && [ "$detected_i2pd" -eq 1 ]; then
    echo "Обнаружены оба варианта: Java I2P И i2pd."
    echo "  1) Использовать Java I2P (встроенный i2psnark)"
    echo "  2) Использовать i2pd (поставим отдельный standalone i2psnark)"
    read -rp "Выбор [1/2]: " both_choice
    if [ "$both_choice" = "2" ]; then
        I2P_MODE="i2pd"
        I2PD_CONF_PATH="/etc/i2pd/i2pd.conf"
    else
        I2P_MODE="javai2p"
    fi
elif [ "$detected_javai2p" -eq 1 ]; then
    I2P_MODE="javai2p"
    echo "Обнаружен Java I2P router. Будем использовать его встроенный i2psnark."
elif [ "$detected_i2pd" -eq 1 ]; then
    I2P_MODE="i2pd"
    I2PD_CONF_PATH="/etc/i2pd/i2pd.conf"
    echo "Обнаружен i2pd. Поставим отдельный standalone i2psnark рядом с ним."
else
    echo "Ни i2pd, ни Java I2P не найдены автоматически."
    echo ""
    echo "Что делаем?"
    echo "  1) Установить i2pd (рекомендуется — легче, меньше требований)"
    echo "  2) Установить Java I2P router"
    echo "  3) У меня уже есть роутер, просто установщик его не нашёл — укажу сам"
    echo "  4) Отменить установку (настрою роутер сам и запущу install.sh снова)"
    read -rp "Выбор [1/2/3/4]: " none_choice
    case "$none_choice" in
        1)
            pkg_install i2pd || die \
                "Не удалось установить i2pd автоматически (в репозиториях вашего дистрибутива его может не быть). Поставьте i2pd вручную и запустите install.sh снова."
            I2P_MODE="i2pd"
            I2PD_CONF_PATH="/etc/i2pd/i2pd.conf"
            ;;
        2)
            case "$PKG_MANAGER" in
                apt)    pkg_install i2p ;;
                dnf|yum) pkg_install i2p ;;
                pacman) pkg_install i2p ;;
                zypper) pkg_install i2p ;;
            esac || die "Не удалось установить Java I2P автоматически — установите вручную (см. geti2p.net) и запустите install.sh снова."
            I2P_MODE="javai2p"
            JAVA_I2P_WEBAPPS="$(find_javai2p_webapps)" || warn "Java I2P установлен, но webapps/ не нашёлся автоматически — подключим RPC вручную позже."
            ;;
        3)
            prompt_manual_i2p_setup
            ;;
        4)
            echo "Установка отменена. Настройте I2P-роутер и запустите install.sh снова."
            exit 0
            ;;
        *)
            die "Некорректный выбор."
            ;;
    esac
fi

echo "Режим: I2P_MODE=${I2P_MODE}"

# ============================================================================
step "5/9: Настройка I2P-роутера"
# ============================================================================
service_restart_best_effort() {
    local name="$1"
    if [ "$INIT_SYSTEM" = "systemd" ]; then
        sudo systemctl restart "$name" 2>/dev/null && sudo systemctl enable "$name" --quiet 2>/dev/null && return 0
    fi
    if [ -x "/etc/init.d/${name}" ]; then
        sudo "/etc/init.d/${name}" restart && return 0
    fi
    if command -v service >/dev/null 2>&1; then
        sudo service "$name" restart && return 0
    fi
    if command -v rc-service >/dev/null 2>&1; then
        sudo rc-service "$name" restart && return 0
    fi
    return 1
}

if [ "$I2P_MODE" = "i2pd" ]; then
    I2PD_CONF="${I2PD_CONF_PATH:-/etc/i2pd/i2pd.conf}"
    if [ -f "$I2PD_CONF" ]; then
        if sudo grep -qE '^\s*i2cp\.enabled\s*=\s*true' "$I2PD_CONF" 2>/dev/null; then
            echo "i2cp.enabled уже включён."
        else
            sudo cp "$I2PD_CONF" "${I2PD_CONF}.itubep.bak"
            if sudo grep -qE '^\s*#?\s*i2cp\.enabled' "$I2PD_CONF" 2>/dev/null; then
                sudo sed -i -E 's/^\s*#?\s*i2cp\.enabled\s*=.*/i2cp.enabled = true/' "$I2PD_CONF"
            else
                printf '\ni2cp.enabled = true\n' | sudo tee -a "$I2PD_CONF" > /dev/null
            fi
            echo "Включили i2cp.enabled=true в ${I2PD_CONF} (бэкап: ${I2PD_CONF}.itubep.bak)"
        fi
        echo "Перезапускаю i2pd..."
        service_restart_best_effort i2pd || warn "Не смог автоматически перезапустить i2pd — перезапустите вручную (это нужно, чтобы i2cp.enabled применился)."
        sleep 2
    else
        warn "${I2PD_CONF} не найден — убедитесь вручную, что I2CP включён (i2cp.enabled=true) и роутер перезапущен."
    fi
else
    echo "Java I2P: I2CP включён по умолчанию, ничего настраивать не нужно."
fi

# ============================================================================
step "6/9: Сборка i2psnark standalone + RPC из исходников"
# ============================================================================
[ "$REBUILD" -eq 1 ] && rm -rf "$BUILD_DIR"

if [ -f "$BUILD_MARKER" ]; then
    echo "Уже собрано ранее — пропускаю (--rebuild форсирует пересборку)."
else
    mkdir -p "$BUILD_DIR"

    if [ ! -d "$I2P_SRC" ]; then
        echo "Клонирую i2p.i2p (исходники, неглубокий клон)..."
        git clone --depth 1 https://github.com/i2p/i2p.i2p.git "$I2P_SRC"
    fi
    cd "$I2P_SRC"
    echo "require.gettext=false" > override.properties
    echo "Собираю i2psnark (ant i2psnark)..."
    ant i2psnark

    if [ ! -d "$RPC_SRC" ]; then
        echo "Клонирую i2p.plugins.i2psnark-rpc..."
        git clone --depth 1 https://github.com/i2p/i2p.plugins.i2psnark-rpc.git "$RPC_SRC"
    fi
    cd "$RPC_SRC"
    echo "require.gettext=false" > override.properties
    sed -i 's/value="1\.7"/value="8"/' src/build.xml

    mkdir -p "${I2P_SRC}/build"
    cp "${I2P_SRC}/apps/i2psnark/java/build/i2psnark.jar" "${I2P_SRC}/build/i2psnark.jar"

    echo "Собираю RPC-плагин (ant war)..."
    ant war

    touch "$BUILD_MARKER"
    echo "Сборка завершена."
fi

I2PSNARK_BUILD="${I2P_SRC}/apps/i2psnark/java/build/i2psnark"
TRANSMISSION_WAR="${RPC_SRC}/src/build/transmission.war.jar"
[ -d "$I2PSNARK_BUILD" ] || die "Сборка i2psnark не найдена — что-то пошло не так на шаге 6."
[ -f "$TRANSMISSION_WAR" ] || die "transmission.war.jar не найден — сборка RPC-плагина не удалась."

# ============================================================================
step "7/9: Установка i2psnark + RPC"
# ============================================================================
if [ "$I2P_MODE" = "i2pd" ]; then
    SNARK_DEPLOY_MARKER="${SNARK_RUN_DIR}/.itubep_deployed"
    if [ -f "$SNARK_DEPLOY_MARKER" ] && [ "$REBUILD" -eq 0 ]; then
        echo "i2psnark уже развёрнут в ${SNARK_RUN_DIR} — пропускаю (не трогаю ваши i2psnark.config.d/ и данные)."
        echo "Обновляю только jar/war (безопасно перезаписать, не пользовательские данные)..."
        cp "${I2PSNARK_BUILD}/i2psnark.jar" "${SNARK_RUN_DIR}/i2psnark.jar"
        cp "$TRANSMISSION_WAR" "${SNARK_RUN_DIR}/webapps/transmission.war"
    else
        # Полное (пере)разворачивание — только при первом запуске или явном
        # --rebuild. Раньше это делалось БЕЗУСЛОВНО на каждом запуске
        # install.sh, включая повторные запуски просто чтобы подтянуть
        # исправления в самом install.sh — и стирало пользовательские
        # правки в i2psnark.config.d/ (например, i2cpHost для роутера не на
        # localhost) и вообще любые данные, накопленные i2psnark, на
        # каждый такой перезапуск. Теперь — только осознанно.
        rm -rf "$SNARK_RUN_DIR"
        cp -r "$I2PSNARK_BUILD" "$SNARK_RUN_DIR"
        chmod +x "${SNARK_RUN_DIR}/launch-i2psnark"
        mkdir -p "${SNARK_RUN_DIR}/webapps"
        cp "$TRANSMISSION_WAR" "${SNARK_RUN_DIR}/webapps/transmission.war"

        mkdir -p "${SNARK_RUN_DIR}/i2psnark.config.d"
        cat > "${SNARK_RUN_DIR}/i2psnark.config.d/i2psnark.config" << 'EOF'
i2psnark.i2cpHost=127.0.0.1
i2psnark.i2cpPort=7654
i2psnark.i2cpOptions=inbound.length=3 inbound.quantity=3 outbound.length=3 outbound.quantity=3
EOF
        touch "$SNARK_DEPLOY_MARKER"
        echo "i2psnark standalone установлен в ${SNARK_RUN_DIR}"
        echo "Если ваш I2P-роутер НЕ на localhost — отредактируйте"
        echo "  ${SNARK_RUN_DIR}/i2psnark.config.d/i2psnark.config"
        echo "(i2psnark.i2cpHost=...) — при повторных запусках install.sh эта правка теперь сохранится."
    fi
else
    if [ -n "$JAVA_I2P_WEBAPPS" ]; then
        cp "$TRANSMISSION_WAR" "${JAVA_I2P_WEBAPPS}/transmission.war"
        echo "transmission.war скопирован в ${JAVA_I2P_WEBAPPS}"
        warn "Требуется перезапуск Java I2P router, чтобы подхватить новый webapp."
        service_restart_best_effort i2p || warn "Не нашёл сервис 'i2p' — перезапустите роутер вручную (или через его консоль)."
    else
        warn "Папка webapps/ Java I2P не найдена — скопируйте вручную:"
        warn "  cp ${TRANSMISSION_WAR} <путь_к_вашему_.i2p>/webapps/transmission.war"
        warn "  и перезапустите роутер."
    fi
fi

# ============================================================================
step "8/9: Python-окружение моста"
# ============================================================================
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${BRIDGE_DIR}/requirements.txt"
echo "Зависимости установлены в ${VENV_DIR}"

# ============================================================================
step "9/9: Автозапуск"
# ============================================================================
mkdir -p "$RUN_STATE_DIR" "$LOG_DIR" "$(dirname "$CTL_SCRIPT")"

cat > "$CTL_SCRIPT" << EOF
#!/usr/bin/env bash
# Автосгенерировано install.sh — не редактировать вручную, перезапустите install.sh
set -u

RUN_STATE_DIR="${RUN_STATE_DIR}"
LOG_DIR="${LOG_DIR}"
BRIDGE_DIR="${BRIDGE_DIR}"
VENV_DIR="${VENV_DIR}"
SNARK_RUN_DIR="${SNARK_RUN_DIR}"
I2P_MODE="${I2P_MODE}"
INIT_SYSTEM="${INIT_SYSTEM}"

_is_running() {
    # Раньше дополнительно сверяли /proc/\$pid/cmdline на подстроку — на
    # практике это оказалось ненадёжно: launch-i2psnark — это обёрточный
    # скрипт, который делает exec java ..., и cmdline процесса после этого
    # никак не похож на "launch-i2psnark"; для python3 -m тоже на практике
    # никогда не совпадало. Вместо этого проверяем то, что нас в
    # действительности волнует: процесс жив (kill -0) И ожидаемый порт
    # реально слушается — а не точное совпадение аргументов запуска.
    local pidfile="\$1" port="\$2"
    [ -f "\$pidfile" ] || return 1
    local pid; pid="\$(cat "\$pidfile" 2>/dev/null)"
    [ -n "\$pid" ] || return 1
    kill -0 "\$pid" 2>/dev/null || return 1
    if [ -n "\$port" ]; then
        _port_in_use "\$port" || return 1
    fi
    return 0
}

_port_in_use() {
    local port="\$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk '{print \$4}' | grep -qE "[:.]\${port}\$"
        return \$?
    fi
    python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(('127.0.0.1', \$port))
    s.close()
    sys.exit(1)
except OSError:
    sys.exit(0)
" 2>/dev/null
}

_pid_on_port() {
    # Отдаёт PID процесса, реально слушающего TCP-порт \$1 (или пусто, если
    # не нашли/нечем проверить). Нужно потому что PID, пойманный через \$!
    # в момент запуска, не всегда оказывается PID-ом процесса, который в
    # итоге держит порт — некоторые обёрточные скрипты (например
    # launch-i2psnark) запускают целевой процесс как ДОЧЕРНИЙ, а не через
    # exec, так что \$! указывает на промежуточный/родительский процесс, а
    # не на реального держателя порта.
    local port="\$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | grep -E "[:.]\${port}[[:space:]]" | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n1
        return
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"\$port" -sTCP:LISTEN 2>/dev/null | head -n1
        return
    fi
    if command -v fuser >/dev/null 2>&1; then
        fuser "\${port}/tcp" 2>/dev/null | awk '{print \$1}'
    fi
}

_start() {
    local name="\$1" pidfile="\$2" logfile="\$3" workdir="\$4" port="\$5" wait_timeout="\$6"; shift 6
    if _is_running "\$pidfile" "\$port"; then
        echo "\$name уже запущен (PID \$(cat "\$pidfile"))"
        return 0
    fi
    if [ -n "\$port" ] && _port_in_use "\$port"; then
        echo "\$name: порт \$port уже занят ДРУГИМ процессом, не отслеживаемым через \$pidfile."
        echo "  (например, был запущен вручную раньше, до itubep-ctl — pid-файл о нём не знает)"
        echo "  Проверьте: ss -ltnp | grep \$port   (или lsof -i :\$port)"
        echo "  Остановите его вручную, либо разберитесь, что занимает порт, прежде чем запускать снова."
        return 1
    fi
    rm -f "\$pidfile"
    : > "\$logfile"  # обрезаем лог — иначе вывод прошлой попытки запуска
                     # мешается со свежей (append-режим внутри самого запуска
                     # сохраняется, это just однократная обрезка перед стартом)
    ( cd "\$workdir" && PYTHONUNBUFFERED=1 nohup "\$@" >>"\$logfile" 2>&1 & echo \$! > "\$pidfile" )

    # JVM (i2psnark) может стартовать не за одно мгновение — поллим вместо
    # одной фиксированной короткой паузы. wait_timeout настраивается на
    # вызов: i2psnark ждёт заметно дольше (установка I2P-туннелей — это
    # легитимно небыстрый процесс, "Connecting to I2P" в логе на 15-й
    # секунде — НЕ ошибка, а норма, особенно на "холодном" роутере).
    local waited=0
    while [ "\$waited" -lt "\$wait_timeout" ]; do
        if _is_running "\$pidfile" "\$port"; then
            # Раз _is_running только что подтвердила, что порт слушается —
            # спросим ОС, кто именно его держит, и если это не тот PID, что
            # мы записали из \$!, поправим pid-файл на реальный. Иначе
            # stop-all будет пытаться убить процесс, который либо уже не
            # существует, либо не тот, что реально держит порт (см. кейс
            # с обёрточными скриптами вроде launch-i2psnark выше).
            if [ -n "\$port" ]; then
                local real_pid; real_pid="\$(_pid_on_port "\$port")"
                if [ -n "\$real_pid" ] && [ "\$real_pid" != "\$(cat "\$pidfile" 2>/dev/null)" ]; then
                    echo "\$real_pid" > "\$pidfile"
                fi
            fi
            echo "\$name запущен (PID \$(cat "\$pidfile"))"
            return 0
        fi
        sleep 1
        waited=\$((waited + 1))
    done

    # Таймаут вышел, но прежде чем пугать "не запустился" — проверим, жив ли
    # вообще процесс. Это означает, что сервис, скорее всего, просто ещё
    # поднимается (JVM/I2P), а не упал.
    local pid; pid="\$(cat "\$pidfile" 2>/dev/null)"
    if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null; then
        echo "\$name: процесс жив (PID \$pid), но порт \$port ещё не слушается через \${wait_timeout}с — это НЕ обязательно ошибка"
        echo "  (для i2psnark установка I2P-туннелей может занимать больше времени). Проверьте чуть позже:"
        echo "  itubep-ctl status-bridge / status-snark   (или загляните в \$logfile)"
        return 0
    fi

    echo "\$name НЕ запустился за \${wait_timeout}с — смотрите \$logfile. Последние строки:"
    tail -n 15 "\$logfile" 2>/dev/null | sed 's/^/    /'
    return 1
}

_stop() {
    local name="\$1" pidfile="\$2" port="\$3"
    if _is_running "\$pidfile" "\$port"; then
        local pid; pid="\$(cat "\$pidfile")"
        kill "\$pid" 2>/dev/null

        # kill только просит процесс завершиться (SIGTERM) и возвращается
        # немедленно — он НЕ ждёт реальной смерти процесса. JVM (i2psnark)
        # может ещё несколько секунд закрывать I2P-туннели, прежде чем
        # реально освободит порт. Раньше pid-файл удалялся и "остановлен"
        # печаталось сразу же — и если сразу следом запускался start-all,
        # он не находил pid-файл (уже удалён), видел занятый порт и решал,
        # что это посторонний процесс, хотя это был тот же самый сервис,
        # ещё не успевший завершиться. Поэтому дожидаемся реальной смерти
        # процесса, с эскалацией до SIGKILL по таймауту.
        local waited=0 stop_timeout=15
        while kill -0 "\$pid" 2>/dev/null; do
            if [ "\$waited" -ge "\$stop_timeout" ]; then
                echo "\$name не завершился за \${stop_timeout}с после SIGTERM — принудительно (SIGKILL)"
                kill -9 "\$pid" 2>/dev/null
                break
            fi
            sleep 1
            waited=\$((waited + 1))
        done

        # Даже после смерти процесса порт освобождается не всегда мгновенно
        # (например TIME_WAIT) — подождём ещё немного, чтобы следующий
        # start-all не спутал это с чужим процессом.
        if [ -n "\$port" ]; then
            waited=0
            while [ "\$waited" -lt 5 ] && _port_in_use "\$port"; do
                sleep 1
                waited=\$((waited + 1))
            done
        fi

        rm -f "\$pidfile"
        echo "\$name остановлен"
    else
        rm -f "\$pidfile"  # висящий protухший pid-файл без реального процесса
        echo "\$name не запущен"
    fi
}

_status() {
    local name="\$1" pidfile="\$2" port="\$3"
    if _is_running "\$pidfile" "\$port"; then
        echo "\$name: запущен (PID \$(cat "\$pidfile"))"
    else
        echo "\$name: остановлен"
    fi
}

_svc_start() {
    # На systemd делегируем реальному супервизору вместо pidfile-механизма —
    # иначе status/start никогда не узнают о процессе, поднятом юнитом
    # (Type=simple, enable --now), и будут считать порт "занятым кем-то
    # посторонним". На всех остальных системах (sysvinit/OpenRC/runit/no
    # init) systemd-юнитов не существует в принципе — там pidfile-механизм
    # это и есть единственный супервизор, ничего не меняем.
    local unit="\$1" name="\$2" pidfile="\$3" logfile="\$4" workdir="\$5" port="\$6" wait_timeout="\$7"; shift 7
    if [ "\$INIT_SYSTEM" = "systemd" ]; then
        systemctl --user start "\$unit"
        return \$?
    fi
    _start "\$name" "\$pidfile" "\$logfile" "\$workdir" "\$port" "\$wait_timeout" "\$@"
}

_svc_stop() {
    local unit="\$1" name="\$2" pidfile="\$3" port="\$4"
    if [ "\$INIT_SYSTEM" = "systemd" ]; then
        systemctl --user stop "\$unit"
        return \$?
    fi
    _stop "\$name" "\$pidfile" "\$port"
}

_svc_status() {
    local unit="\$1" name="\$2" pidfile="\$3" port="\$4"
    if [ "\$INIT_SYSTEM" = "systemd" ]; then
        if systemctl --user is-active --quiet "\$unit"; then
            echo "\$name: запущен (systemd, \$(systemctl --user show -p MainPID --value "\$unit" 2>/dev/null | sed 's/^0\$/?/'))"
        else
            echo "\$name: остановлен (systemd: \$(systemctl --user is-active "\$unit" 2>/dev/null))"
        fi
        return 0
    fi
    _status "\$name" "\$pidfile" "\$port"
}

case "\${1:-}" in
    start-bridge)
        _svc_start "itubep-bridge.service" "мост" "\${RUN_STATE_DIR}/bridge.pid" "\${LOG_DIR}/bridge.log" "\$BRIDGE_DIR" "9080" "15" \\
            "\${VENV_DIR}/bin/python3" -m transport.http_server
        ;;
    stop-bridge)  _svc_stop  "itubep-bridge.service" "мост" "\${RUN_STATE_DIR}/bridge.pid" "9080" ;;
    status-bridge) _svc_status "itubep-bridge.service" "мост" "\${RUN_STATE_DIR}/bridge.pid" "9080" ;;
    start-snark)
        [ "\$I2P_MODE" = "i2pd" ] || { echo "i2psnark standalone не используется (режим: \$I2P_MODE)"; exit 0; }
        _svc_start "itubep-i2psnark.service" "i2psnark" "\${RUN_STATE_DIR}/snark.pid" "\${LOG_DIR}/snark.log" "\$SNARK_RUN_DIR" "8002" "60" \\
            "\${SNARK_RUN_DIR}/launch-i2psnark"
        ;;
    stop-snark)
        [ "\$I2P_MODE" = "i2pd" ] || { echo "i2psnark standalone не используется (режим: \$I2P_MODE)"; exit 0; }
        _svc_stop "itubep-i2psnark.service" "i2psnark" "\${RUN_STATE_DIR}/snark.pid" "8002"
        ;;
    status-snark)
        [ "\$I2P_MODE" = "i2pd" ] || { echo "i2psnark standalone не используется (режим: \$I2P_MODE)"; exit 0; }
        _svc_status "itubep-i2psnark.service" "i2psnark" "\${RUN_STATE_DIR}/snark.pid" "8002"
        ;;
    start-all)
        "\$0" start-snark
        sleep 2
        "\$0" start-bridge
        ;;
    stop-all)
        "\$0" stop-bridge
        "\$0" stop-snark
        ;;
    status)
        "\$0" status-bridge
        "\$0" status-snark
        ;;
    settings)
        # Foreground GUI-окно настроек — не через pidfile-механизм (это
        # разовый интерактивный инструмент, а не постоянно работающий
        # процесс), просто открываем и ждём закрытия окна.
        cd "\$BRIDGE_DIR" && exec "\${VENV_DIR}/bin/python3" -m ui.settings_window
        ;;
    pairings)
        cd "\$BRIDGE_DIR" && exec "\${VENV_DIR}/bin/python3" -m ui.manage_pairings
        ;;
    *)
        echo "Использование: \$0 {start-all|stop-all|status|start-bridge|stop-bridge|status-bridge|start-snark|stop-snark|status-snark|settings|pairings}"
        exit 1
        ;;
esac
EOF
chmod +x "$CTL_SCRIPT"
echo "Control-скрипт: ${CTL_SCRIPT}"

# --- Короткая команда "itubep-ctl" без полного пути (как у пакетов из apt) ---
# ~/.local/bin — стандартный XDG-путь для пользовательских бинарников, не
# требует sudo (в отличие от /usr/local/bin). На большинстве современных
# дистрибутивов уже входит в PATH по умолчанию для интерактивных шеллов, но
# не везде — проверяем и дописываем в rc-файлы, если нет.
LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "$LOCAL_BIN"
ln -sf "$CTL_SCRIPT" "${LOCAL_BIN}/itubep-ctl"

case ":${PATH}:" in
    *":${LOCAL_BIN}:"*)
        echo "~/.local/bin уже в PATH — команда 'itubep-ctl' доступна."
        ;;
    *)
        warn "~/.local/bin не в PATH — добавляю в ~/.bashrc и ~/.profile"
        PATH_MARKER="# itubep-bridge: добавлено install.sh"
        for rcfile in "${HOME}/.bashrc" "${HOME}/.profile"; do
            [ -f "$rcfile" ] || continue
            if ! grep -qF "$PATH_MARKER" "$rcfile" 2>/dev/null; then
                {
                    echo ""
                    echo "$PATH_MARKER"
                    echo 'export PATH="$HOME/.local/bin:$PATH"'
                } >> "$rcfile"
            fi
        done
        warn "Откройте новый терминал (или выполните 'source ~/.bashrc'), чтобы команда 'itubep-ctl' заработала без полного пути."
        ;;
esac

if [ "$INIT_SYSTEM" = "systemd" ]; then
    SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
    mkdir -p "$SYSTEMD_USER_DIR"

    if [ "$I2P_MODE" = "i2pd" ]; then
        cat > "${SYSTEMD_USER_DIR}/itubep-i2psnark.service" << EOF
[Unit]
Description=ITubeP — i2psnark standalone (BitTorrent для i2pd)
After=network.target

[Service]
Type=simple
WorkingDirectory=${SNARK_RUN_DIR}
ExecStart=${SNARK_RUN_DIR}/launch-i2psnark
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    fi

    AFTER_EXTRA=""
    [ "$I2P_MODE" = "i2pd" ] && AFTER_EXTRA="itubep-i2psnark.service"
    cat > "${SYSTEMD_USER_DIR}/itubep-bridge.service" << EOF
[Unit]
Description=ITubeP Bridge
After=network.target ${AFTER_EXTRA}

[Service]
Type=simple
WorkingDirectory=${BRIDGE_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/python3 -m transport.http_server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

    sudo loginctl enable-linger "$USER" 2>/dev/null || warn "Не удалось включить loginctl linger — сервисы могут останавливаться при выходе из системы."
    systemctl --user daemon-reload
    [ "$I2P_MODE" = "i2pd" ] && systemctl --user enable --now itubep-i2psnark.service
    systemctl --user enable --now itubep-bridge.service
    echo "Автозапуск настроен через systemd --user."

else
    CRON_MARKER="# itubep-bridge autostart (добавлено install.sh)"
    if command -v crontab >/dev/null 2>&1; then
        CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
        if ! echo "$CURRENT_CRON" | grep -qF "$CRON_MARKER"; then
            {
                echo "$CURRENT_CRON"
                echo "$CRON_MARKER"
                echo "@reboot ${CTL_SCRIPT} start-all >> ${LOG_DIR}/autostart.log 2>&1"
            } | crontab -
            echo "Добавлена запись @reboot в crontab пользователя."
        else
            echo "Запись автозапуска в crontab уже есть."
        fi
    else
        warn "crontab не найден — автозапуск при перезагрузке не настроен."
        warn "Запускайте вручную: ${CTL_SCRIPT} start-all"
    fi

    echo "Запускаю сейчас..."
    "$CTL_SCRIPT" start-all
fi

# ============================================================================
c_green "=== Готово ==="
# ============================================================================
echo ""
echo "Полезные команды (после открытия нового терминала, если PATH только что обновился):"
if [ "$INIT_SYSTEM" = "systemd" ]; then
    echo "  Статус моста:   systemctl --user status itubep-bridge.service"
    echo "  Логи моста:     journalctl --user -u itubep-bridge.service -f"
    [ "$I2P_MODE" = "i2pd" ] && echo "  Статус snark:   systemctl --user status itubep-i2psnark.service"
    echo "  (itubep-ctl тоже доступен для ручного управления/логов, см. ниже)"
else
    echo "  Статус:         itubep-ctl status"
    echo "  Остановить:     itubep-ctl stop-all"
    echo "  Запустить:      itubep-ctl start-all"
    echo "  Логи моста:     ${LOG_DIR}/bridge.log"
    [ "$I2P_MODE" = "i2pd" ] && echo "  Логи snark:     ${LOG_DIR}/snark.log"
fi
[ "$I2P_MODE" = "i2pd" ] && echo "  Веб-интерфейс snark: http://127.0.0.1:8002/i2psnark/"
echo "  Настройки/сопряжение: itubep-ctl settings"
echo "  Управление сопряжениями: itubep-ctl pairings"
echo ""
echo "Первый запуск может занять пару минут, пока i2pd/I2P строит туннели — это нормально."
