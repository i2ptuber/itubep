#!/usr/bin/env bash
#
# install.sh — installer for the ITubeP bridge (client part) /
#              установщик моста ITubeP (клиентской части).
#
# Supports several package managers (apt/dnf/yum/pacman/zypper — Debian/
# Ubuntu, Fedora/RHEL/CentOS/Rocky/Alma, Arch/Manjaro, openSUSE) and works
# both with systemd and without it (sysvinit, OpenRC, runit, etc. —
# autostart via cron @reboot + pid files, without system-level init
# scripts, which differ a lot in conventions between distros).
#
# Installs and configures:
#   - I2P router (i2pd by default if nothing is found) OR uses an already
#     installed Java I2P / i2pd
#   - i2psnark standalone + I2PSnark-RPC, built FROM SOURCE of i2p.i2p —
#     does not depend on prebuilt packages from gitlab.com/i2pplus
#   - the bridge itself (Python venv + dependencies)
#   - autostart (systemd --user OR cron @reboot — depending on the system)
#
# One command: chmod +x install.sh && ./install.sh
# Idempotent — re-running skips steps already done
# (--rebuild forces a rebuild of i2psnark/RPC).
#
# ---------------------------------------------------------------------------
# Localization / Локализация: all user-facing messages are shown in Russian
# if the system locale is Russian, and in English otherwise. This mirrors
# the language-detection logic used elsewhere in the project (see
# bridge/i18n.py) — system locale first, English as the universal fallback.
# ---------------------------------------------------------------------------
set -euo pipefail

# ============================================================================
# i18n
# ============================================================================
detect_install_lang() {
    local l="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
    case "$l" in
        ru*|ru_*) echo ru ;;
        *)        echo en ;;
    esac
}
ITUBEP_INSTALL_LANG="$(detect_install_lang)"

declare -A MSG_RU=(
    [not_root]="Не запускайте от root — скрипт сам вызовет sudo, где нужно."
    [need_sudo]="Нужен sudo (для установки пакетов и, если есть, systemd-сервисов)."

    [step1]="1/9: Определение пакетного менеджера"
    [no_pkg_manager]="Не найден ни один из поддерживаемых пакетных менеджеров (apt/dnf/yum/pacman/zypper)."
    [pkg_manager_is]="Пакетный менеджер: %s"

    [step2]="2/9: Базовые зависимости"
    [base_pkgs_partial]="Не все базовые пакеты установились — смотрите вывод выше, возможно потребуется доустановить вручную."
    [ffmpeg_fail]="ffmpeg не установился автоматически (на RHEL/CentOS/Rocky обычно нужен репозиторий RPM Fusion) — публикация видео не будет работать, пока не поставите ffmpeg вручную."
    [jdk_not_found]="Не нашёл JDK с рабочим javac после установки. Поставьте JDK (не только JRE!) вручную и запустите install.sh снова."
    [jdk_using]="Используется JDK: %s"

    [step3]="3/9: Определение init-системы (для автозапуска)"
    [init_system_is]="Init-система: %s"
    [init_other]="systemd не обнаружен — автозапуск будет через cron (@reboot) + pid-файлы."

    [step4]="4/9: Определение I2P-клиента"
    [manual_router_prompt_title]="Какой у вас роутер?"
    [manual_router_opt1]="  1) i2pd"
    [manual_router_opt2]="  2) Java I2P (i2p.itoopie / geti2p.net)"
    [manual_router_read]="Выбор [1/2]: "
    [i2pd_conf_read]="Путь к i2pd.conf [/etc/i2pd/i2pd.conf]: "
    [i2pd_conf_missing]="Файл %s не найден — проверьте путь позже вручную (i2cp.enabled=true)."
    [javai2p_webapps_read]="Путь к webapps/ вашего роутера (например ~/.i2p/webapps): "
    [javai2p_webapps_missing]="Директория %s не найдена — RPC придётся подключить вручную после установки."
    [invalid_choice]="Некорректный выбор."
    [both_detected_title]="Обнаружены оба варианта: Java I2P И i2pd."
    [both_opt1]="  1) Использовать Java I2P (встроенный i2psnark)"
    [both_opt2]="  2) Использовать i2pd (поставим отдельный standalone i2psnark)"
    [both_read]="Выбор [1/2]: "
    [javai2p_detected]="Обнаружен Java I2P router. Будем использовать его встроенный i2psnark."
    [i2pd_detected]="Обнаружен i2pd. Поставим отдельный standalone i2psnark рядом с ним."
    [none_detected_title]="Ни i2pd, ни Java I2P не найдены автоматически."
    [none_prompt_title]="Что делаем?"
    [none_opt1]="  1) Установить i2pd (рекомендуется — легче, меньше требований)"
    [none_opt2]="  2) Установить Java I2P router"
    [none_opt3]="  3) У меня уже есть роутер, просто установщик его не нашёл — укажу сам"
    [none_opt4]="  4) Отменить установку (настрою роутер сам и запущу install.sh снова)"
    [none_read]="Выбор [1/2/3/4]: "
    [i2pd_install_fail]="Не удалось установить i2pd автоматически (в репозиториях вашего дистрибутива его может не быть). Поставьте i2pd вручную и запустите install.sh снова."
    [javai2p_install_fail]="Не удалось установить Java I2P автоматически — установите вручную (см. geti2p.net) и запустите install.sh снова."
    [javai2p_webapps_autofail]="Java I2P установлен, но webapps/ не нашёлся автоматически — подключим RPC вручную позже."
    [install_cancelled]="Установка отменена. Настройте I2P-роутер и запустите install.sh снова."
    [mode_is]="Режим: I2P_MODE=%s"

    [step5]="5/9: Настройка I2P-роутера"
    [i2cp_already_enabled]="i2cp.enabled уже включён."
    [i2cp_enable_manually]="Включите i2cp в настройках своего i2pd роутера и перезагрузите его."
    [i2pd_conf_not_found]="%s не найден — убедитесь вручную, что I2CP включён (i2cp.enabled=true) и роутер перезапущен."
    [javai2p_i2cp_ok]="Java I2P: I2CP включён по умолчанию, ничего настраивать не нужно."

    [step6]="6/9: Сборка i2psnark standalone + RPC из исходников"
    [already_built]="Уже собрано ранее — пропускаю (--rebuild форсирует пересборку)."
    [cloning_i2p]="Клонирую i2p.i2p (исходники, неглубокий клон)..."
    [building_i2psnark]="Собираю i2psnark (ant i2psnark)..."
    [cloning_rpc]="Клонирую i2p.plugins.i2psnark-rpc..."
    [building_rpc]="Собираю RPC-плагин (ant war)..."
    [build_done]="Сборка завершена."
    [i2psnark_build_missing]="Сборка i2psnark не найдена — что-то пошло не так на шаге 6."
    [transmission_war_missing]="transmission.war.jar не найден — сборка RPC-плагина не удалась."

    [step7]="7/9: Установка i2psnark + RPC"
    [snark_already_deployed]="i2psnark уже развёрнут в %s — пропускаю (не трогаю ваши i2psnark.config.d/ и данные)."
    [snark_updating_jar]="Обновляю только jar/war (безопасно перезаписать, не пользовательские данные)..."
    [snark_installed_at]="i2psnark standalone установлен в %s"
    [snark_edit_hint1]="Если ваш I2P-роутер НЕ на localhost — отредактируйте"
    [snark_edit_hint2]="(i2psnark.i2cpHost=...) — при повторных запусках install.sh эта правка теперь сохранится."
    [transmission_war_copied]="transmission.war скопирован в %s"
    [javai2p_restart_needed]="Требуется перезапуск Java I2P router, чтобы подхватить новый webapp."
    [javai2p_service_not_found]="Не нашёл сервис 'i2p' — перезапустите роутер вручную (или через его консоль)."
    [javai2p_webapps_manual1]="Папка webapps/ Java I2P не найдена — скопируйте вручную:"
    [javai2p_webapps_manual2]="  cp %s <путь_к_вашему_.i2p>/webapps/transmission.war"
    [javai2p_webapps_manual3]="  и перезапустите роутер."

    [step8]="8/9: Python-окружение моста"
    [deps_installed_at]="Зависимости установлены в %s"

    [step9]="9/9: Автозапуск"
    [ctl_script_at]="Control-скрипт: %s"
    [localbin_in_path]="~/.local/bin уже в PATH — команда 'itubep-ctl' доступна."
    [localbin_not_in_path]="~/.local/bin не в PATH — добавляю в ~/.bashrc и ~/.profile"
    [localbin_open_new_shell]="Откройте новый терминал (или выполните 'source ~/.bashrc'), чтобы команда 'itubep-ctl' заработала без полного пути."
    [loginctl_fail]="Не удалось включить loginctl linger — сервисы могут останавливаться при выходе из системы."
    [autostart_systemd]="Автозапуск настроен через systemd --user."
    [crontab_added]="Добавлена запись @reboot в crontab пользователя."
    [crontab_exists]="Запись автозапуска в crontab уже есть."
    [crontab_missing]="crontab не найден — автозапуск при перезагрузке не настроен."
    [crontab_manual_run]="Запускайте вручную: %s start-all"
    [starting_now]="Запускаю сейчас..."

    [done]="Готово"
    [useful_commands]="Полезные команды (после открытия нового терминала, если PATH только что обновился):"
    [status_bridge_systemd]="  Статус моста:   systemctl --user status itubep-bridge.service"
    [logs_bridge_systemd]="  Логи моста:     journalctl --user -u itubep-bridge.service -f"
    [status_snark_systemd]="  Статус snark:   systemctl --user status itubep-i2psnark.service"
    [ctl_also_available]="  (itubep-ctl тоже доступен для ручного управления/логов, см. ниже)"
    [status_cmd]="  Статус:         itubep-ctl status"
    [stop_cmd]="  Остановить:     itubep-ctl stop-all"
    [start_cmd]="  Запустить:      itubep-ctl start-all"
    [logs_bridge_at]="  Логи моста:     %s"
    [logs_snark_at]="  Логи snark:     %s"
    [snark_webui]="  Веб-интерфейс snark: http://127.0.0.1:8002/i2psnark/"
    [settings_cmd]="  Настройки/сопряжение: itubep-ctl settings"
    [pairings_cmd]="  Управление сопряжениями: itubep-ctl pairings"
    [first_run_note]="Первый запуск может занять пару минут, пока i2pd/I2P строит туннели — это нормально."

    [ctl_generated_by]="Автосгенерировано install.sh — не редактировать вручную, перезапустите install.sh"
    [ctl_name_bridge]="мост"
    [ctl_name_snark]="i2psnark"
    [ctl_already_running]="%s уже запущен (PID %s)"
    [ctl_port_taken1]="%s: порт %s уже занят ДРУГИМ процессом, не отслеживаемым через pid-файл."
    [ctl_port_taken2]="  (например, был запущен вручную раньше, до itubep-ctl — pid-файл о нём не знает)"
    [ctl_port_taken3]="  Проверьте: ss -ltnp | grep %s   (или lsof -i :%s)"
    [ctl_port_taken4]="  Остановите его вручную, либо разберитесь, что занимает порт, прежде чем запускать снова."
    [ctl_started]="%s запущен (PID %s)"
    [ctl_alive_not_listening]="%s: процесс жив (PID %s), но порт %s ещё не слушается через %sс — это НЕ обязательно ошибка"
    [ctl_alive_hint1]="  (для i2psnark установка I2P-туннелей может занимать больше времени). Проверьте чуть позже:"
    [ctl_alive_hint2]="  itubep-ctl status-bridge / status-snark   (или загляните в %s)"
    [ctl_failed_start]="%s НЕ запустился за %sс — смотрите %s. Последние строки:"
    [ctl_force_kill]="%s не завершился за %sс после SIGTERM — принудительно (SIGKILL)"
    [ctl_stopped]="%s остановлен"
    [ctl_not_running]="%s не запущен"
    [ctl_status_running]="%s: запущен (PID %s)"
    [ctl_status_stopped]="%s: остановлен"
    [ctl_status_running_systemd]="%s: запущен (systemd, %s)"
    [ctl_status_stopped_systemd]="%s: остановлен (systemd: %s)"
    [ctl_snark_unused]="i2psnark standalone не используется (режим: %s)"
    [ctl_usage]="Использование: %s {start-all|stop-all|status|start-bridge|stop-bridge|status-bridge|start-snark|stop-snark|status-snark|settings|pairings}"
)

declare -A MSG_EN=(
    [not_root]="Do not run as root — the script will call sudo itself where needed."
    [need_sudo]="sudo is required (to install packages and, if present, systemd services)."

    [step1]="1/9: Detecting package manager"
    [no_pkg_manager]="None of the supported package managers were found (apt/dnf/yum/pacman/zypper)."
    [pkg_manager_is]="Package manager: %s"

    [step2]="2/9: Base dependencies"
    [base_pkgs_partial]="Not all base packages installed successfully — check the output above, you may need to install some manually."
    [ffmpeg_fail]="ffmpeg did not install automatically (on RHEL/CentOS/Rocky the RPM Fusion repo is usually needed) — video publishing won't work until you install ffmpeg manually."
    [jdk_not_found]="Could not find a JDK with a working javac after installation. Install a JDK (not just a JRE!) manually and run install.sh again."
    [jdk_using]="Using JDK: %s"

    [step3]="3/9: Detecting init system (for autostart)"
    [init_system_is]="Init system: %s"
    [init_other]="systemd not detected — autostart will use cron (@reboot) + pid files."

    [step4]="4/9: Detecting I2P client"
    [manual_router_prompt_title]="Which router do you have?"
    [manual_router_opt1]="  1) i2pd"
    [manual_router_opt2]="  2) Java I2P (i2p.itoopie / geti2p.net)"
    [manual_router_read]="Choice [1/2]: "
    [i2pd_conf_read]="Path to i2pd.conf [/etc/i2pd/i2pd.conf]: "
    [i2pd_conf_missing]="File %s not found — check the path later manually (i2cp.enabled=true)."
    [javai2p_webapps_read]="Path to your router's webapps/ (e.g. ~/.i2p/webapps): "
    [javai2p_webapps_missing]="Directory %s not found — you'll need to connect the RPC manually after installation."
    [invalid_choice]="Invalid choice."
    [both_detected_title]="Both were detected: Java I2P AND i2pd."
    [both_opt1]="  1) Use Java I2P (built-in i2psnark)"
    [both_opt2]="  2) Use i2pd (a separate standalone i2psnark will be installed)"
    [both_read]="Choice [1/2]: "
    [javai2p_detected]="Java I2P router detected. We'll use its built-in i2psnark."
    [i2pd_detected]="i2pd detected. A separate standalone i2psnark will be installed alongside it."
    [none_detected_title]="Neither i2pd nor Java I2P were found automatically."
    [none_prompt_title]="What should we do?"
    [none_opt1]="  1) Install i2pd (recommended — lighter, fewer requirements)"
    [none_opt2]="  2) Install Java I2P router"
    [none_opt3]="  3) I already have a router, the installer just didn't find it — I'll set it up manually"
    [none_opt4]="  4) Cancel installation (I'll set up the router myself and run install.sh again)"
    [none_read]="Choice [1/2/3/4]: "
    [i2pd_install_fail]="Could not install i2pd automatically (it may not be in your distro's repositories). Install i2pd manually and run install.sh again."
    [javai2p_install_fail]="Could not install Java I2P automatically — install it manually (see geti2p.net) and run install.sh again."
    [javai2p_webapps_autofail]="Java I2P was installed, but webapps/ was not found automatically — we'll connect the RPC manually later."
    [install_cancelled]="Installation cancelled. Set up the I2P router and run install.sh again."
    [mode_is]="Mode: I2P_MODE=%s"

    [step5]="5/9: Configuring the I2P router"
    [i2cp_already_enabled]="i2cp.enabled is already on."
    [i2cp_enable_manually]="Enable i2cp in your i2pd router's settings and restart it."
    [i2pd_conf_not_found]="%s not found — make sure manually that I2CP is enabled (i2cp.enabled=true) and the router has been restarted."
    [javai2p_i2cp_ok]="Java I2P: I2CP is enabled by default, nothing to configure."

    [step6]="6/9: Building i2psnark standalone + RPC from source"
    [already_built]="Already built earlier — skipping (--rebuild forces a rebuild)."
    [cloning_i2p]="Cloning i2p.i2p (source, shallow clone)..."
    [building_i2psnark]="Building i2psnark (ant i2psnark)..."
    [cloning_rpc]="Cloning i2p.plugins.i2psnark-rpc..."
    [building_rpc]="Building the RPC plugin (ant war)..."
    [build_done]="Build finished."
    [i2psnark_build_missing]="i2psnark build not found — something went wrong at step 6."
    [transmission_war_missing]="transmission.war.jar not found — the RPC plugin build failed."

    [step7]="7/9: Installing i2psnark + RPC"
    [snark_already_deployed]="i2psnark is already deployed at %s — skipping (not touching your i2psnark.config.d/ or data)."
    [snark_updating_jar]="Updating only jar/war (safe to overwrite, not user data)..."
    [snark_installed_at]="i2psnark standalone installed at %s"
    [snark_edit_hint1]="If your I2P router is NOT on localhost — edit"
    [snark_edit_hint2]="(i2psnark.i2cpHost=...) — this edit will now be kept across repeated install.sh runs."
    [transmission_war_copied]="transmission.war copied to %s"
    [javai2p_restart_needed]="Java I2P router needs to be restarted to pick up the new webapp."
    [javai2p_service_not_found]="Couldn't find the 'i2p' service — restart the router manually (or via its console)."
    [javai2p_webapps_manual1]="Java I2P's webapps/ folder was not found — copy it manually:"
    [javai2p_webapps_manual2]="  cp %s <path_to_your_.i2p>/webapps/transmission.war"
    [javai2p_webapps_manual3]="  and restart the router."

    [step8]="8/9: Bridge Python environment"
    [deps_installed_at]="Dependencies installed at %s"

    [step9]="9/9: Autostart"
    [ctl_script_at]="Control script: %s"
    [localbin_in_path]="~/.local/bin is already in PATH — the 'itubep-ctl' command is available."
    [localbin_not_in_path]="~/.local/bin is not in PATH — adding it to ~/.bashrc and ~/.profile"
    [localbin_open_new_shell]="Open a new terminal (or run 'source ~/.bashrc') for the 'itubep-ctl' command to work without the full path."
    [loginctl_fail]="Failed to enable loginctl linger — services may stop when you log out."
    [autostart_systemd]="Autostart configured via systemd --user."
    [crontab_added]="An @reboot entry was added to the user's crontab."
    [crontab_exists]="An autostart entry already exists in crontab."
    [crontab_missing]="crontab not found — autostart on reboot is not configured."
    [crontab_manual_run]="Run manually: %s start-all"
    [starting_now]="Starting now..."

    [done]="Done"
    [useful_commands]="Useful commands (after opening a new terminal, if PATH was just updated):"
    [status_bridge_systemd]="  Bridge status:  systemctl --user status itubep-bridge.service"
    [logs_bridge_systemd]="  Bridge logs:    journalctl --user -u itubep-bridge.service -f"
    [status_snark_systemd]="  Snark status:   systemctl --user status itubep-i2psnark.service"
    [ctl_also_available]="  (itubep-ctl is also available for manual control/logs, see below)"
    [status_cmd]="  Status:         itubep-ctl status"
    [stop_cmd]="  Stop:           itubep-ctl stop-all"
    [start_cmd]="  Start:          itubep-ctl start-all"
    [logs_bridge_at]="  Bridge logs:    %s"
    [logs_snark_at]="  Snark logs:     %s"
    [snark_webui]="  Snark web UI: http://127.0.0.1:8002/i2psnark/"
    [settings_cmd]="  Settings/pairing: itubep-ctl settings"
    [pairings_cmd]="  Manage pairings: itubep-ctl pairings"
    [first_run_note]="The first run may take a couple of minutes while i2pd/I2P builds tunnels — that's normal."

    [ctl_generated_by]="Auto-generated by install.sh — do not edit by hand, re-run install.sh instead"
    [ctl_name_bridge]="bridge"
    [ctl_name_snark]="i2psnark"
    [ctl_already_running]="%s is already running (PID %s)"
    [ctl_port_taken1]="%s: port %s is already taken by ANOTHER process not tracked via the pid file."
    [ctl_port_taken2]="  (e.g. it was started manually earlier, before itubep-ctl — the pid file doesn't know about it)"
    [ctl_port_taken3]="  Check: ss -ltnp | grep %s   (or lsof -i :%s)"
    [ctl_port_taken4]="  Stop it manually, or figure out what's using the port, before starting again."
    [ctl_started]="%s started (PID %s)"
    [ctl_alive_not_listening]="%s: process is alive (PID %s), but port %s isn't listening yet after %ss — this is NOT necessarily an error"
    [ctl_alive_hint1]="  (for i2psnark, setting up I2P tunnels can take longer). Check again in a bit:"
    [ctl_alive_hint2]="  itubep-ctl status-bridge / status-snark   (or look at %s)"
    [ctl_failed_start]="%s did NOT start within %ss — see %s. Last lines:"
    [ctl_force_kill]="%s did not stop within %ss after SIGTERM — forcing (SIGKILL)"
    [ctl_stopped]="%s stopped"
    [ctl_not_running]="%s is not running"
    [ctl_status_running]="%s: running (PID %s)"
    [ctl_status_stopped]="%s: stopped"
    [ctl_status_running_systemd]="%s: running (systemd, %s)"
    [ctl_status_stopped_systemd]="%s: stopped (systemd: %s)"
    [ctl_snark_unused]="i2psnark standalone is not used (mode: %s)"
    [ctl_usage]="Usage: %s {start-all|stop-all|status|start-bridge|stop-bridge|status-bridge|start-snark|stop-snark|status-snark|settings|pairings}"
)

# msg <key> [args...] — returns the translated, printf-formatted string for
# the current install-time language. Falls back to the key itself if a
# translation is somehow missing (should not happen — both dicts share keys).
msg() {
    local key="$1"; shift || true
    local fmt=""
    if [ "$ITUBEP_INSTALL_LANG" = "ru" ]; then
        fmt="${MSG_RU[$key]:-}"
    else
        fmt="${MSG_EN[$key]:-}"
    fi
    [ -n "$fmt" ] || fmt="$key"
    # shellcheck disable=SC2059
    printf "$fmt" "$@"
}

# msg_raw <key> — returns the translated string as-is, with any %s
# placeholders left intact and no printf formatting applied. Use this (not
# msg) whenever the string is a template that will be filled in later by
# something else — e.g. baked into the generated itubep-ctl script, where
# printf runs again at itubep-ctl's own runtime. Calling msg() with no
# arguments would already run printf over the template and silently eat the
# %s placeholders (printf substitutes missing arguments with an empty
# string), corrupting the template before it's ever used.
msg_raw() {
    local key="$1"
    if [ "$ITUBEP_INSTALL_LANG" = "ru" ]; then
        printf '%s' "${MSG_RU[$key]:-$key}"
    else
        printf '%s' "${MSG_EN[$key]:-$key}"
    fi
}

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="${HOME}/.local/share/itubep-bridge"
BUILD_DIR="${WORKDIR}/build"
I2P_SRC="${BUILD_DIR}/i2p.i2p"
RPC_SRC="${BUILD_DIR}/i2p.plugins.i2psnark-rpc"
SNARK_RUN_DIR="${WORKDIR}/i2psnark-run"
VENV_DIR="${WORKDIR}/venv"
BUILD_MARKER="${BUILD_DIR}/.build_complete"
RUN_STATE_DIR="${WORKDIR}/run"      # pid files / pid-файлы
LOG_DIR="${WORKDIR}/logs"
CTL_SCRIPT="${WORKDIR}/bin/itubep-ctl"

REBUILD=0
[ "${1:-}" = "--rebuild" ] && REBUILD=1

c_green() { printf '\033[32m%s\033[0m\n' "$1"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$1"; }
c_red() { printf '\033[31m%s\033[0m\n' "$1"; }
step() { echo ""; c_green "=== $1 ==="; }
warn() { c_yellow "[!] $1"; }
die() { c_red "$(msg err_prefix) $1"; exit 1; }
MSG_RU[err_prefix]="ОШИБКА:"
MSG_EN[err_prefix]="ERROR:"

[ "$(id -u)" -eq 0 ] && die "$(msg not_root)"
command -v sudo >/dev/null || die "$(msg need_sudo)"

# ============================================================================
step "$(msg step1)"
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
    die "$(msg no_pkg_manager)"
fi
echo "$(msg pkg_manager_is "$PKG_MANAGER")"

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
step "$(msg step2)"
# ============================================================================
case "$PKG_MANAGER" in
    apt)    BASE_PKGS="git ant curl unzip zip cron python3 python3-venv python3-pip python3-tk" ;;
    dnf|yum) BASE_PKGS="git ant curl unzip zip cronie python3 python3-pip python3-tkinter" ;;
    pacman) BASE_PKGS="git apache-ant curl unzip zip cronie python python-pip tk" ;;
    zypper) BASE_PKGS="git ant curl unzip zip cron python3 python3-venv python3-pip python3-tk" ;;
esac
# shellcheck disable=SC2086
pkg_install $BASE_PKGS || warn "$(msg base_pkgs_partial)"

if ! command -v ffmpeg >/dev/null 2>&1; then
    pkg_install ffmpeg || warn "$(msg ffmpeg_fail)"
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
    JAVA_HOME="$(find_jdk_with_javac)" || die "$(msg jdk_not_found)"
fi
export JAVA_HOME
echo "$(msg jdk_using "$JAVA_HOME")"

# ============================================================================
step "$(msg step3)"
# ============================================================================
if [ -d /run/systemd/system ]; then
    INIT_SYSTEM="systemd"
else
    INIT_SYSTEM="other"
fi
echo "$(msg init_system_is "$INIT_SYSTEM")"
if [ "$INIT_SYSTEM" = "other" ]; then
    echo "$(msg init_other)"
fi

# ============================================================================
step "$(msg step4)"
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
    # User says a router already exists, autodetection just didn't see it
    # (nonstandard install path, unusual distro, etc.) /
    # Пользователь говорит, что роутер уже есть, просто автоопределение его
    # не увидело (нестандартный путь установки, необычный дистрибутив и т.п.)
    echo ""
    echo "$(msg manual_router_prompt_title)"
    echo "$(msg manual_router_opt1)"
    echo "$(msg manual_router_opt2)"
    read -rp "$(msg manual_router_read)" manual_choice
    case "$manual_choice" in
        1)
            I2P_MODE="i2pd"
            read -rp "$(msg i2pd_conf_read)" conf_path
            I2PD_CONF_PATH="${conf_path:-/etc/i2pd/i2pd.conf}"
            [ -f "$I2PD_CONF_PATH" ] || warn "$(msg i2pd_conf_missing "$I2PD_CONF_PATH")"
            ;;
        2)
            I2P_MODE="javai2p"
            read -rp "$(msg javai2p_webapps_read)" webapps_path
            webapps_path="${webapps_path/#\~/$HOME}"
            if [ -d "$webapps_path" ]; then
                JAVA_I2P_WEBAPPS="$webapps_path"
            else
                warn "$(msg javai2p_webapps_missing "$webapps_path")"
            fi
            ;;
        *)
            die "$(msg invalid_choice)"
            ;;
    esac
}

if [ "$detected_javai2p" -eq 1 ] && [ "$detected_i2pd" -eq 1 ]; then
    echo "$(msg both_detected_title)"
    echo "$(msg both_opt1)"
    echo "$(msg both_opt2)"
    read -rp "$(msg both_read)" both_choice
    if [ "$both_choice" = "2" ]; then
        I2P_MODE="i2pd"
        I2PD_CONF_PATH="/etc/i2pd/i2pd.conf"
    else
        I2P_MODE="javai2p"
    fi
elif [ "$detected_javai2p" -eq 1 ]; then
    I2P_MODE="javai2p"
    echo "$(msg javai2p_detected)"
elif [ "$detected_i2pd" -eq 1 ]; then
    I2P_MODE="i2pd"
    I2PD_CONF_PATH="/etc/i2pd/i2pd.conf"
    echo "$(msg i2pd_detected)"
else
    echo "$(msg none_detected_title)"
    echo ""
    echo "$(msg none_prompt_title)"
    echo "$(msg none_opt1)"
    echo "$(msg none_opt2)"
    echo "$(msg none_opt3)"
    echo "$(msg none_opt4)"
    read -rp "$(msg none_read)" none_choice
    case "$none_choice" in
        1)
            pkg_install i2pd || die "$(msg i2pd_install_fail)"
            I2P_MODE="i2pd"
            I2PD_CONF_PATH="/etc/i2pd/i2pd.conf"
            ;;
        2)
            case "$PKG_MANAGER" in
                apt)    pkg_install i2p ;;
                dnf|yum) pkg_install i2p ;;
                pacman) pkg_install i2p ;;
                zypper) pkg_install i2p ;;
            esac || die "$(msg javai2p_install_fail)"
            I2P_MODE="javai2p"
            JAVA_I2P_WEBAPPS="$(find_javai2p_webapps)" || warn "$(msg javai2p_webapps_autofail)"
            ;;
        3)
            prompt_manual_i2p_setup
            ;;
        4)
            echo "$(msg install_cancelled)"
            exit 0
            ;;
        *)
            die "$(msg invalid_choice)"
            ;;
    esac
fi

echo "$(msg mode_is "$I2P_MODE")"

# ============================================================================
step "$(msg step5)"
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
            echo "$(msg i2cp_already_enabled)"
        else
            echo "$(msg i2cp_enable_manually)"
        fi
    else
        warn "$(msg i2pd_conf_not_found "$I2PD_CONF")"
    fi
else
    echo "$(msg javai2p_i2cp_ok)"
fi

# ============================================================================
step "$(msg step6)"
# ============================================================================
[ "$REBUILD" -eq 1 ] && rm -rf "$BUILD_DIR"

if [ -f "$BUILD_MARKER" ]; then
    echo "$(msg already_built)"
else
    mkdir -p "$BUILD_DIR"

    if [ ! -d "$I2P_SRC" ]; then
        echo "$(msg cloning_i2p)"
        git clone --depth 1 https://github.com/i2p/i2p.i2p.git "$I2P_SRC"
    fi
    cd "$I2P_SRC"
    echo "require.gettext=false" > override.properties
    echo "$(msg building_i2psnark)"
    ant i2psnark

    if [ ! -d "$RPC_SRC" ]; then
        echo "$(msg cloning_rpc)"
        git clone --depth 1 https://github.com/i2p/i2p.plugins.i2psnark-rpc.git "$RPC_SRC"
    fi
    cd "$RPC_SRC"
    echo "require.gettext=false" > override.properties
    sed -i 's/value="1\.7"/value="8"/' src/build.xml

    mkdir -p "${I2P_SRC}/build"
    cp "${I2P_SRC}/apps/i2psnark/java/build/i2psnark.jar" "${I2P_SRC}/build/i2psnark.jar"

    echo "$(msg building_rpc)"
    ant war

    touch "$BUILD_MARKER"
    echo "$(msg build_done)"
fi

I2PSNARK_BUILD="${I2P_SRC}/apps/i2psnark/java/build/i2psnark"
TRANSMISSION_WAR="${RPC_SRC}/src/build/transmission.war.jar"
[ -d "$I2PSNARK_BUILD" ] || die "$(msg i2psnark_build_missing)"
[ -f "$TRANSMISSION_WAR" ] || die "$(msg transmission_war_missing)"

# ============================================================================
step "$(msg step7)"
# ============================================================================
if [ "$I2P_MODE" = "i2pd" ]; then
    SNARK_DEPLOY_MARKER="${SNARK_RUN_DIR}/.itubep_deployed"
    if [ -f "$SNARK_DEPLOY_MARKER" ] && [ "$REBUILD" -eq 0 ]; then
        echo "$(msg snark_already_deployed "$SNARK_RUN_DIR")"
        echo "$(msg snark_updating_jar)"
        cp "${I2PSNARK_BUILD}/i2psnark.jar" "${SNARK_RUN_DIR}/i2psnark.jar"
        cp "$TRANSMISSION_WAR" "${SNARK_RUN_DIR}/webapps/transmission.war"
    else
        # Full (re)deployment — only on first run or explicit --rebuild.
        # Previously this happened UNCONDITIONALLY on every install.sh run,
        # including re-runs just to pick up fixes in install.sh itself — and
        # wiped user edits in i2psnark.config.d/ (e.g. i2cpHost for a router
        # not on localhost) and any data accumulated by i2psnark, on every
        # such re-run. Now — only deliberately. /
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
        echo "$(msg snark_installed_at "$SNARK_RUN_DIR")"
        echo "$(msg snark_edit_hint1)"
        echo "  ${SNARK_RUN_DIR}/i2psnark.config.d/i2psnark.config"
        echo "$(msg snark_edit_hint2)"
    fi
else
    if [ -n "$JAVA_I2P_WEBAPPS" ]; then
        cp "$TRANSMISSION_WAR" "${JAVA_I2P_WEBAPPS}/transmission.war"
        echo "$(msg transmission_war_copied "$JAVA_I2P_WEBAPPS")"
        warn "$(msg javai2p_restart_needed)"
        service_restart_best_effort i2p || warn "$(msg javai2p_service_not_found)"
    else
        warn "$(msg javai2p_webapps_manual1)"
        warn "$(msg javai2p_webapps_manual2 "$TRANSMISSION_WAR")"
        warn "$(msg javai2p_webapps_manual3)"
    fi
fi

# ============================================================================
step "$(msg step8)"
# ============================================================================
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${BRIDGE_DIR}/requirements.txt"
echo "$(msg deps_installed_at "$VENV_DIR")"

# ============================================================================
step "$(msg step9)"
# ============================================================================
mkdir -p "$RUN_STATE_DIR" "$LOG_DIR" "$(dirname "$CTL_SCRIPT")"

# The generated itubep-ctl script's language is baked in at generation time
# to match this install run (same detection as everywhere else in the
# project — see bridge/i18n.py) /
# Язык сгенерированного скрипта itubep-ctl фиксируется на момент генерации
# и соответствует этому запуску установки.
CTL_NAME_BRIDGE="$(msg_raw ctl_name_bridge)"
CTL_NAME_SNARK="$(msg_raw ctl_name_snark)"
CTL_GENERATED_BY="$(msg_raw ctl_generated_by)"
CTL_ALREADY_RUNNING_FMT="$(msg_raw ctl_already_running)"
CTL_PORT_TAKEN1_FMT="$(msg_raw ctl_port_taken1)"
CTL_PORT_TAKEN2="$(msg_raw ctl_port_taken2)"
CTL_PORT_TAKEN3_FMT="$(msg_raw ctl_port_taken3)"
CTL_PORT_TAKEN4="$(msg_raw ctl_port_taken4)"
CTL_STARTED_FMT="$(msg_raw ctl_started)"
CTL_ALIVE_NOT_LISTENING_FMT="$(msg_raw ctl_alive_not_listening)"
CTL_ALIVE_HINT1="$(msg_raw ctl_alive_hint1)"
CTL_ALIVE_HINT2_FMT="$(msg_raw ctl_alive_hint2)"
CTL_FAILED_START_FMT="$(msg_raw ctl_failed_start)"
CTL_FORCE_KILL_FMT="$(msg_raw ctl_force_kill)"
CTL_STOPPED_FMT="$(msg_raw ctl_stopped)"
CTL_NOT_RUNNING_FMT="$(msg_raw ctl_not_running)"
CTL_STATUS_RUNNING_FMT="$(msg_raw ctl_status_running)"
CTL_STATUS_STOPPED_FMT="$(msg_raw ctl_status_stopped)"
CTL_STATUS_RUNNING_SYSTEMD_FMT="$(msg_raw ctl_status_running_systemd)"
CTL_STATUS_STOPPED_SYSTEMD_FMT="$(msg_raw ctl_status_stopped_systemd)"
CTL_SNARK_UNUSED_FMT="$(msg_raw ctl_snark_unused)"
CTL_USAGE_FMT="$(msg_raw ctl_usage)"

cat > "$CTL_SCRIPT" << EOF
#!/usr/bin/env bash
# ${CTL_GENERATED_BY}
set -u

RUN_STATE_DIR="${RUN_STATE_DIR}"
LOG_DIR="${LOG_DIR}"
BRIDGE_DIR="${BRIDGE_DIR}"
VENV_DIR="${VENV_DIR}"
SNARK_RUN_DIR="${SNARK_RUN_DIR}"
I2P_MODE="${I2P_MODE}"
INIT_SYSTEM="${INIT_SYSTEM}"

# Message templates — kept in double-quoted variables (not inlined directly
# into printf format strings) so that apostrophes in the English text (e.g.
# "isn't", "doesn't") can't break the quoting.
MSG_ALREADY_RUNNING="${CTL_ALREADY_RUNNING_FMT}"
MSG_PORT_TAKEN1="${CTL_PORT_TAKEN1_FMT}"
MSG_PORT_TAKEN2="${CTL_PORT_TAKEN2}"
MSG_PORT_TAKEN3="${CTL_PORT_TAKEN3_FMT}"
MSG_PORT_TAKEN4="${CTL_PORT_TAKEN4}"
MSG_STARTED="${CTL_STARTED_FMT}"
MSG_ALIVE_NOT_LISTENING="${CTL_ALIVE_NOT_LISTENING_FMT}"
MSG_ALIVE_HINT1="${CTL_ALIVE_HINT1}"
MSG_ALIVE_HINT2="${CTL_ALIVE_HINT2_FMT}"
MSG_FAILED_START="${CTL_FAILED_START_FMT}"
MSG_FORCE_KILL="${CTL_FORCE_KILL_FMT}"
MSG_STOPPED="${CTL_STOPPED_FMT}"
MSG_NOT_RUNNING="${CTL_NOT_RUNNING_FMT}"
MSG_STATUS_RUNNING="${CTL_STATUS_RUNNING_FMT}"
MSG_STATUS_STOPPED="${CTL_STATUS_STOPPED_FMT}"
MSG_STATUS_RUNNING_SYSTEMD="${CTL_STATUS_RUNNING_SYSTEMD_FMT}"
MSG_STATUS_STOPPED_SYSTEMD="${CTL_STATUS_STOPPED_SYSTEMD_FMT}"
MSG_SNARK_UNUSED="${CTL_SNARK_UNUSED_FMT}"
MSG_USAGE="${CTL_USAGE_FMT}"

_is_running() {
    # We only check that the process is alive (kill -0) AND the expected
    # port is actually listening — not an exact match of launch arguments,
    # since wrapper scripts (e.g. launch-i2psnark, which execs java ...)
    # make cmdline-substring checks unreliable in practice. /
    # Проверяем то, что нас в действительности волнует: процесс жив
    # (kill -0) И ожидаемый порт реально слушается.
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
    # Returns the PID of the process actually listening on TCP port \$1 (or
    # empty if not found/no way to check). Needed because the PID captured
    # via \$! at launch time isn't always the process that ends up holding
    # the port — some wrapper scripts (e.g. launch-i2psnark) start the
    # target process as a CHILD rather than via exec, so \$! points at an
    # intermediate/parent process, not the real port holder. /
    # Отдаёт PID процесса, реально слушающего TCP-порт \$1.
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
        printf "\$MSG_ALREADY_RUNNING\n" "\$name" "\$(cat "\$pidfile")"
        return 0
    fi
    if [ -n "\$port" ] && _port_in_use "\$port"; then
        printf "\$MSG_PORT_TAKEN1\n" "\$name" "\$port"
        echo "${CTL_PORT_TAKEN2}"
        printf "\$MSG_PORT_TAKEN3\n" "\$port" "\$port"
        echo "${CTL_PORT_TAKEN4}"
        return 1
    fi
    rm -f "\$pidfile"
    : > "\$logfile"  # truncate the log so previous attempt's output doesn't
                     # mix in with the fresh one (append mode within the run
                     # itself is kept — this is just a one-time truncation
                     # before start)
    ( cd "\$workdir" && PYTHONUNBUFFERED=1 nohup "\$@" >>"\$logfile" 2>&1 & echo \$! > "\$pidfile" )

    # The JVM (i2psnark) may not start instantly — poll instead of a single
    # fixed short pause. wait_timeout is tuned per call: i2psnark waits
    # noticeably longer (setting up I2P tunnels legitimately takes a while;
    # "Connecting to I2P" in the log at second 15 is NOT an error, especially
    # on a "cold" router).
    local waited=0
    while [ "\$waited" -lt "\$wait_timeout" ]; do
        if _is_running "\$pidfile" "\$port"; then
            # Since _is_running just confirmed the port is listening, ask the
            # OS who actually holds it, and if it's not the PID we recorded
            # from \$!, fix the pid file to the real one. Otherwise stop-all
            # would try to kill a process that either no longer exists or
            # isn't the real port holder (see the wrapper-script case above).
            if [ -n "\$port" ]; then
                local real_pid; real_pid="\$(_pid_on_port "\$port")"
                if [ -n "\$real_pid" ] && [ "\$real_pid" != "\$(cat "\$pidfile" 2>/dev/null)" ]; then
                    echo "\$real_pid" > "\$pidfile"
                fi
            fi
            printf "\$MSG_STARTED\n" "\$name" "\$(cat "\$pidfile")"
            return 0
        fi
        sleep 1
        waited=\$((waited + 1))
    done

    # Timeout passed, but before reporting a failure, check whether the
    # process is still alive at all — the service is most likely just still
    # starting up (JVM/I2P), not crashed.
    local pid; pid="\$(cat "\$pidfile" 2>/dev/null)"
    if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null; then
        printf "\$MSG_ALIVE_NOT_LISTENING\n" "\$name" "\$pid" "\$port" "\$wait_timeout"
        echo "${CTL_ALIVE_HINT1}"
        printf "\$MSG_ALIVE_HINT2\n" "\$logfile"
        return 0
    fi

    printf "\$MSG_FAILED_START\n" "\$name" "\$wait_timeout" "\$logfile"
    tail -n 15 "\$logfile" 2>/dev/null | sed 's/^/    /'
    return 1
}

_stop() {
    local name="\$1" pidfile="\$2" port="\$3"
    if _is_running "\$pidfile" "\$port"; then
        local pid; pid="\$(cat "\$pidfile")"
        kill "\$pid" 2>/dev/null

        # kill only requests termination (SIGTERM) and returns immediately —
        # it does NOT wait for the process to actually die. The JVM
        # (i2psnark) may take a few more seconds closing I2P tunnels before
        # it actually frees the port. So we wait for the process to really
        # die, escalating to SIGKILL on timeout.
        local waited=0 stop_timeout=15
        while kill -0 "\$pid" 2>/dev/null; do
            if [ "\$waited" -ge "\$stop_timeout" ]; then
                printf "\$MSG_FORCE_KILL\n" "\$name" "\$stop_timeout"
                kill -9 "\$pid" 2>/dev/null
                break
            fi
            sleep 1
            waited=\$((waited + 1))
        done

        # Even after the process dies, the port isn't always freed instantly
        # (e.g. TIME_WAIT) — wait a bit more so the next start-all doesn't
        # mistake this for a foreign process.
        if [ -n "\$port" ]; then
            waited=0
            while [ "\$waited" -lt 5 ] && _port_in_use "\$port"; do
                sleep 1
                waited=\$((waited + 1))
            done
        fi

        rm -f "\$pidfile"
        printf "\$MSG_STOPPED\n" "\$name"
    else
        rm -f "\$pidfile"  # stale pid file with no real process behind it
        printf "\$MSG_NOT_RUNNING\n" "\$name"
    fi
}

_status() {
    local name="\$1" pidfile="\$2" port="\$3"
    if _is_running "\$pidfile" "\$port"; then
        printf "\$MSG_STATUS_RUNNING\n" "\$name" "\$(cat "\$pidfile")"
    else
        printf "\$MSG_STATUS_STOPPED\n" "\$name"
    fi
}

_svc_start() {
    # On systemd we delegate to the real supervisor instead of the pidfile
    # mechanism — otherwise status/start would never learn about a process
    # started by the unit (Type=simple, enable --now) and would think the
    # port is "taken by something else". On every other system (sysvinit/
    # OpenRC/runit/no init) systemd units don't exist at all — there the
    # pidfile mechanism is the only supervisor, nothing changes.
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
            printf "\$MSG_STATUS_RUNNING_SYSTEMD\n" "\$name" "\$(systemctl --user show -p MainPID --value "\$unit" 2>/dev/null | sed 's/^0\$/?/')"
        else
            printf "\$MSG_STATUS_STOPPED_SYSTEMD\n" "\$name" "\$(systemctl --user is-active "\$unit" 2>/dev/null)"
        fi
        return 0
    fi
    _status "\$name" "\$pidfile" "\$port"
}

case "\${1:-}" in
    start-bridge)
        _svc_start "itubep-bridge.service" "${CTL_NAME_BRIDGE}" "\${RUN_STATE_DIR}/bridge.pid" "\${LOG_DIR}/bridge.log" "\$BRIDGE_DIR" "9080" "15" \\
            "\${VENV_DIR}/bin/python3" -m transport.http_server
        ;;
    stop-bridge)  _svc_stop  "itubep-bridge.service" "${CTL_NAME_BRIDGE}" "\${RUN_STATE_DIR}/bridge.pid" "9080" ;;
    status-bridge) _svc_status "itubep-bridge.service" "${CTL_NAME_BRIDGE}" "\${RUN_STATE_DIR}/bridge.pid" "9080" ;;
    start-snark)
        [ "\$I2P_MODE" = "i2pd" ] || { printf "\$MSG_SNARK_UNUSED\n" "\$I2P_MODE"; exit 0; }
        _svc_start "itubep-i2psnark.service" "${CTL_NAME_SNARK}" "\${RUN_STATE_DIR}/snark.pid" "\${LOG_DIR}/snark.log" "\$SNARK_RUN_DIR" "8002" "60" \\
            "\${SNARK_RUN_DIR}/launch-i2psnark"
        ;;
    stop-snark)
        [ "\$I2P_MODE" = "i2pd" ] || { printf "\$MSG_SNARK_UNUSED\n" "\$I2P_MODE"; exit 0; }
        _svc_stop "itubep-i2psnark.service" "${CTL_NAME_SNARK}" "\${RUN_STATE_DIR}/snark.pid" "8002"
        ;;
    status-snark)
        [ "\$I2P_MODE" = "i2pd" ] || { printf "\$MSG_SNARK_UNUSED\n" "\$I2P_MODE"; exit 0; }
        _svc_status "itubep-i2psnark.service" "${CTL_NAME_SNARK}" "\${RUN_STATE_DIR}/snark.pid" "8002"
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
        # Foreground settings GUI window — not via the pidfile mechanism
        # (it's a one-off interactive tool, not a long-running process),
        # just open it and wait for the window to close.
        cd "\$BRIDGE_DIR" && exec "\${VENV_DIR}/bin/python3" -m ui.settings_window
        ;;
    pairings)
        cd "\$BRIDGE_DIR" && exec "\${VENV_DIR}/bin/python3" -m ui.manage_pairings
        ;;
    *)
        printf "\$MSG_USAGE\n" "\$0"
        exit 1
        ;;
esac
EOF
chmod +x "$CTL_SCRIPT"
echo "$(msg ctl_script_at "$CTL_SCRIPT")"

# --- Short "itubep-ctl" command without the full path (like apt packages) ---
# ~/.local/bin is the standard XDG path for user binaries, no sudo needed
# (unlike /usr/local/bin). Included in PATH by default for interactive
# shells on most modern distros, but not everywhere — check and append to
# rc files if missing. /
# ~/.local/bin — стандартный XDG-путь для пользовательских бинарников.
LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "$LOCAL_BIN"
ln -sf "$CTL_SCRIPT" "${LOCAL_BIN}/itubep-ctl"

case ":${PATH}:" in
    *":${LOCAL_BIN}:"*)
        echo "$(msg localbin_in_path)"
        ;;
    *)
        warn "$(msg localbin_not_in_path)"
        PATH_MARKER="# itubep-bridge: added by install.sh"
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
        warn "$(msg localbin_open_new_shell)"
        ;;
esac

if [ "$INIT_SYSTEM" = "systemd" ]; then
    SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
    mkdir -p "$SYSTEMD_USER_DIR"

    if [ "$I2P_MODE" = "i2pd" ]; then
        cat > "${SYSTEMD_USER_DIR}/itubep-i2psnark.service" << EOF
[Unit]
Description=ITubeP — i2psnark standalone (BitTorrent for i2pd)
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

    sudo loginctl enable-linger "$USER" 2>/dev/null || warn "$(msg loginctl_fail)"
    systemctl --user daemon-reload
    [ "$I2P_MODE" = "i2pd" ] && systemctl --user enable --now itubep-i2psnark.service
    systemctl --user enable --now itubep-bridge.service
    echo "$(msg autostart_systemd)"

else
    CRON_MARKER="# itubep-bridge autostart (added by install.sh)"
    if command -v crontab >/dev/null 2>&1; then
        CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
        if ! echo "$CURRENT_CRON" | grep -qF "$CRON_MARKER"; then
            {
                echo "$CURRENT_CRON"
                echo "$CRON_MARKER"
                echo "@reboot ${CTL_SCRIPT} start-all >> ${LOG_DIR}/autostart.log 2>&1"
            } | crontab -
            echo "$(msg crontab_added)"
        else
            echo "$(msg crontab_exists)"
        fi
    else
        warn "$(msg crontab_missing)"
        warn "$(msg crontab_manual_run "$CTL_SCRIPT")"
    fi

    echo "$(msg starting_now)"
    "$CTL_SCRIPT" start-all
fi

# ============================================================================
c_green "=== $(msg done) ==="
# ============================================================================
echo ""
echo "$(msg useful_commands)"
if [ "$INIT_SYSTEM" = "systemd" ]; then
    echo "$(msg status_bridge_systemd)"
    echo "$(msg logs_bridge_systemd)"
    [ "$I2P_MODE" = "i2pd" ] && echo "$(msg status_snark_systemd)"
    echo "$(msg ctl_also_available)"
else
    echo "$(msg status_cmd)"
    echo "$(msg stop_cmd)"
    echo "$(msg start_cmd)"
    echo "$(msg logs_bridge_at "${LOG_DIR}/bridge.log")"
    [ "$I2P_MODE" = "i2pd" ] && echo "$(msg logs_snark_at "${LOG_DIR}/snark.log")"
fi
[ "$I2P_MODE" = "i2pd" ] && echo "$(msg snark_webui)"
echo "$(msg settings_cmd)"
echo "$(msg pairings_cmd)"
echo ""
echo "$(msg first_run_note)"
