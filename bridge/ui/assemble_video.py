"""
assemble_video.py — собирает докачанные через i2psnark HLS-сегменты
(segment_0000.ts, segment_0001.ts, ...) в один обычный видеофайл.

Нужен для no-JS сценария: пользователь без JS видит на странице видео только
ссылку на .torrent, скачивает её в i2psnark вручную (родным клиентом или
через веб-интерфейс i2psnark), и после того как торрент докачался — этим
скриптом склеивает сегменты в один .mp4, который можно смотреть в любом
обычном плеере (без HLS.js, без моста, без браузера вообще).

Использование:
    cd bridge
    python3 -m tools.assemble_video <torrent_name> [опции]

<torrent_name> — имя папки с сегментами внутри storage-директории i2psnark
(то же самое имя, под которым торрент показан в веб-интерфейсе i2psnark).
ВАЖНО: для видео с этого сайта torrent_name — это НЕ video_id. video_id —
sha256 манифеста, torrent_name — sha256 содержимого самих сегментов
(compute_content_id, см. snark/publisher.py) — это два независимых хеша.
Смотрите имя папки прямо в i2psnark (http://127.0.0.1:8002/i2psnark/) —
там будет реальный torrent_name, а не video_id со страницы видео на сайте.

Примеры:
    # автоматически найдёт storage-директорию из настроек моста (той же
    # БД, что использует settings_window), соберёт в <torrent_name>.mp4
    # рядом с сегментами
    python3 -m tools.assemble_video 3f9a2e...c1

    # явно указать storage-директорию и путь вывода
    python3 -m tools.assemble_video 3f9a2e...c1 \
        --storage-dir ~/i2psnark-run/i2psnark --output ~/Videos/out.mp4

    # разрешить сборку, даже если часть сегментов ещё не докачана
    # (соберёт видео из того, что есть, оборвётся на первом пропуске)
    python3 -m tools.assemble_video 3f9a2e...c1 --allow-partial
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from i18n import t

SEGMENT_RE = re.compile(r"^segment_(\d{4,})\.ts$")


class AssembleError(Exception):
    pass


def default_storage_dir() -> Path:
    """
    Пытается взять storage-директорию из той же БД настроек, что использует
    settings_window.py (policy/storage.py — лёгкий модуль, без tkinter,
    безопасно импортировать из чисто консольного скрипта). Если БД/значение
    недоступны — падаем на дефолт i2psnark.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from policy.storage import PolicyStorage  # noqa: E402

        return Path(PolicyStorage().get_snark_storage_dir())
    except Exception:
        return Path.home() / "i2psnark-run" / "i2psnark"


def find_segments(torrent_dir: Path) -> list[tuple[int, Path]]:
    if not torrent_dir.is_dir():
        raise AssembleError(t("assemble.dir_not_found", dir=torrent_dir))

    found: list[tuple[int, Path]] = []
    for p in torrent_dir.iterdir():
        m = SEGMENT_RE.match(p.name)
        if m:
            found.append((int(m.group(1)), p))

    if not found:
        raise AssembleError(t("assemble.no_segments", dir=torrent_dir))

    found.sort(key=lambda t: t[0])
    return found


def check_gaps(segments: list[tuple[int, Path]], allow_partial: bool) -> list[tuple[int, Path]]:
    """
    Проверяет, что индексы сегментов идут подряд без пропусков (0,1,2,...).
    Пропуск означает недокачанный сегмент — склеивать через дыру нельзя
    (получится обрыв видео/аудио в этом месте), поэтому по умолчанию
    останавливаемся на первом пропуске и используем только префикс до него.
    С --allow-partial — то же самое, но без ошибки, только предупреждение.
    """
    usable = []
    expected = segments[0][0]
    for idx, path in segments:
        if idx != expected:
            msg = t("assemble.gap_msg", expected=expected, found=idx, usable=len(usable))
            if allow_partial:
                print(f"[!] {msg}", file=sys.stderr)
                break
            else:
                raise AssembleError(msg + t("assemble.gap_hint"))
        usable.append((idx, path))
        expected += 1
    return usable


def assemble(
    torrent_name: str,
    storage_dir: Path,
    output: Path | None,
    allow_partial: bool,
    keep_ts: bool,
) -> Path:
    torrent_dir = storage_dir / torrent_name
    segments = find_segments(torrent_dir)
    usable = check_gaps(segments, allow_partial)

    if not usable:
        raise AssembleError(t("assemble.no_usable_segments"))

    print(t("assemble.assembling", usable=len(usable), total=len(segments)))

    output = output or torrent_dir.with_suffix(".mp4")
    output.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        # concat demuxer — корректно клеит MPEG-TS с пересчётом таймстампов,
        # надёжнее сырой конкатенации байт (-c copy — без перекодирования,
        # быстро и без потери качества, это просто ремукс контейнера)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as list_file:
            for _, path in usable:
                # ffmpeg concat формат требует экранировать одинарные кавычки
                escaped = str(path.resolve()).replace("'", "'\\''")
                list_file.write(f"file '{escaped}'\n")
            list_path = Path(list_file.name)

        try:
            cmd = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_path), "-c", "copy", str(output),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise AssembleError(t("assemble.ffmpeg_error", stderr=result.stderr[-3000:]))
        finally:
            list_path.unlink(missing_ok=True)
    else:
        # ffmpeg не найден — падаем на сырую конкатенацию .ts (MPEG-TS
        # спроектирован быть конкатенируемым побайтово; большинство плееров
        # типа VLC/mpv проглотят это нормально, но таймстампы не
        # пересчитываются, возможны мелкие артефакты на стыках сегментов)
        print("[!] " + t("assemble.no_ffmpeg"), file=sys.stderr)
        if output.suffix != ".ts":
            output = output.with_suffix(".ts")
        with open(output, "wb") as out_f:
            for _, path in usable:
                with open(path, "rb") as seg_f:
                    shutil.copyfileobj(seg_f, out_f)

    if not keep_ts:
        pass  # исходные сегменты — это данные i2psnark, их мы не трогаем и не удаляем

    return output


def main():
    parser = argparse.ArgumentParser(
        description=t("assemble.arg_description"),
    )
    parser.add_argument("torrent_name", help=t("assemble.arg_torrent_name"))
    parser.add_argument("--storage-dir", type=Path, default=None,
                         help=t("assemble.arg_storage_dir"))
    parser.add_argument("--output", type=Path, default=None,
                         help=t("assemble.arg_output"))
    parser.add_argument("--allow-partial", action="store_true",
                         help=t("assemble.arg_allow_partial"))
    parser.add_argument("--keep-ts", action="store_true",
                         help=t("assemble.arg_keep_ts"))
    args = parser.parse_args()

    storage_dir = args.storage_dir or default_storage_dir()

    try:
        output = assemble(
            args.torrent_name, storage_dir, args.output, args.allow_partial, args.keep_ts,
        )
    except AssembleError as e:
        print(t("assemble.error_prefix", error=e), file=sys.stderr)
        sys.exit(1)

    print(t("assemble.done", output=output))


if __name__ == "__main__":
    main()
