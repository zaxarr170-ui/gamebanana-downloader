"""
core.py — ядро для GUI-версии загрузчика модов GameBanana.

Использует готовые функции из downloader.py (VirusTotal, превью, описание,
распаковка, история, настройки), а всё, где были input()/print()/tqdm,
переписано на колбэки (Hooks), чтобы им мог управлять интерфейс.
"""
import os
import re
import time
import html
import queue
from datetime import datetime
import shutil
import threading
import concurrent.futures

import requests

import downloader as dl

HEADERS = dl.API_HEADERS
SAFE_NAME = re.compile(r'[<>:"/\\|?*]')
CHUNK = 256 * 1024
VT_KEY_FILE = dl.VT_KEY_FILE


class Cancelled(Exception):
    """Пользователь нажал общий «Стоп»."""


class TaskCancelled(Cancelled):
    """Пользователь отменил только один мод (✕ на карточке)."""


class AnyEvent:
    """Похож на threading.Event: сработал, если сработало любое из событий (общее или локальное)."""

    def __init__(self, *events):
        self.events = [e for e in events if e is not None]

    def is_set(self):
        return any(e.is_set() for e in self.events)

    def wait(self, timeout=None):
        end = None if timeout is None else time.time() + timeout
        while not self.is_set():
            step = 0.1 if end is None else min(0.1, end - time.time())
            if step <= 0:
                break
            time.sleep(step)
        return self.is_set()


class Hooks:
    """Мост между ядром и интерфейсом."""

    def __init__(self, log=None, task=None, ask=None, cancel=None, local_cancel=None, task_cancels=None,
                 local_pause=None, task_pauses=None):
        self._log = log or print
        self._task = task or (lambda *a, **k: None)
        self._ask = ask
        self.global_cancel = cancel or threading.Event()
        self.local_cancel = local_cancel
        self.task_cancels = task_cancels if task_cancels is not None else {}   # {id карточки: Event}
        self.pause = local_pause                                               # Event паузы этого мода (set = пауза)
        self.task_pauses = task_pauses if task_pauses is not None else {}      # {id карточки: Event}
        # .cancel — то, что проверяют потоки скачивания: сработает и от общего «Стоп», и от ✕ на моде
        self.cancel = AnyEvent(self.global_cancel, local_cancel) if local_cancel is not None else self.global_cancel

    def for_task(self, local_event, local_pause=None):
        """Копия хуков для одного мода: общий Стоп + его личные события отмены и паузы."""
        return Hooks(self._log, self._task, self._ask, self.global_cancel, local_event, self.task_cancels,
                     local_pause, self.task_pauses)

    def register_task(self, tid):
        ev = self.task_cancels.get(tid)
        if ev is None:
            ev = self.task_cancels[tid] = threading.Event()
        self.task_pauses.setdefault(tid, threading.Event())
        return ev

    def toggle_pause(self, tid):
        """Пауза/продолжить одного мода (из интерфейса). Возвращает True, если теперь пауза."""
        ev = self.task_pauses.setdefault(tid, threading.Event())
        if ev.is_set():
            ev.clear()
        else:
            ev.set()
        return ev.is_set()

    def cancel_task(self, tid):
        """Отменяет один мод (вызывается из интерфейса)."""
        self.task_cancels.setdefault(tid, threading.Event()).set()

    def log(self, msg):
        self._log(msg)

    def task(self, tid, **fields):
        self._task(tid, **fields)

    def ask(self, kind, default=None, **kw):
        if self._ask is None:
            return default
        return self._ask(kind, default=default, cancel_ev=self.cancel, **kw)

    def check(self):
        if self.global_cancel.is_set():
            raise Cancelled()
        if self.local_cancel is not None and self.local_cancel.is_set():
            raise TaskCancelled()


# ---------------------------------------------------------------- утилиты
def load_vt_key():
    if os.path.exists(VT_KEY_FILE):
        try:
            with open(VT_KEY_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def save_vt_key(key):
    with open(VT_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key.strip())


def fmt_speed(bps):
    mb = bps / 1048576
    return f"{mb:.2f} МБ/с" if mb >= 1 else f"{bps / 1024:.0f} КБ/с"


def clean_folder_name(name, fallback):
    name = SAFE_NAME.sub("", name or "").strip(" .")[:120]
    return name or fallback


def unique_path(path):
    base, n = path, 1
    while os.path.exists(path):
        path = f"{base} ({n})"
        n += 1
    return path


def parse_links(text):
    """Из текста (ссылки/ID по строкам) делает список ссылок без дублей."""
    urls, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for tok in re.split(r"[\s,;]+", line):
            tok = tok.strip("\"'")
            if not tok:
                continue
            if tok.isdigit():
                tok = f"https://gamebanana.com/mods/{tok}"
            if tok not in seen:
                seen.add(tok)
                urls.append(tok)
    return urls


def read_links_file(path=None):
    path = path or dl.LINKS_FILE
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Вставляйте ссылки или ID модов по одной на строку\n")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return parse_links(f.read())


def fetch_collection(col_url, hooks):
    match = re.search(r"/collections/(\d+)", col_url)
    if not match:
        hooks.log("[-] Ошибка: Это не похоже на ссылку коллекции GameBanana.")
        return []
    col_id = match.group(1)
    hooks.log(f"[*] Получаем список модов из коллекции #{col_id}...")
    try:
        api_url = f"https://gamebanana.com/apiv11/Collection/{col_id}/Items?_nPerpage=50"
        res = requests.get(api_url, headers=HEADERS, timeout=10, verify=False)
        if res.status_code != 200:
            hooks.log(f"[-] Не удалось получить коллекцию (код {res.status_code})")
            return []
        data = res.json()
        items = data.get("_aRecords", data.get("_aItems", []))
        urls = []
        for item in items:
            mod_data = item.get("_aMod", item)
            mod_id = mod_data.get("_idRow")
            if mod_id:
                urls.append(f"https://gamebanana.com/mods/{mod_id}")
        if urls:
            hooks.log(f"[+] Из коллекции добавлено модов: {len(urls)}")
        else:
            hooks.log("[-] В коллекции не найдено модов (возможно, она пуста).")
        return urls
    except Exception as e:
        hooks.log(f"[-] Ошибка при загрузке коллекции: {e}")
        return []


def check_updates(history, hooks):
    """Режим 4: возвращает список ссылок на моды с новыми файлами."""
    ids = [(i, d) for i, d in history.items() if str(i).isdigit()]
    if not ids:
        hooks.log("[-] История пуста (нет модов с ID для проверки).")
        return []
    hooks.log(f"[*] Найдено уникальных модов в истории: {len(ids)}. Проверяем...")
    urls = []
    hooks.task("total", done=0, total=len(ids), label="Проверка обновлений")
    for k, (m_id, m_data) in enumerate(ids, 1):
        hooks.check()                       # при «Стоп» выходим, счётчик остаётся честным
        m_type = m_data.get("type", "mods")
        downloaded = m_data.get("files", [])
        hooks.log(f"[*] Проверка {'WIP' if m_type == 'wips' else 'мода'} ID: {m_id}...")
        try:
            api_model = "Wip" if m_type == "wips" else "Mod"
            api_url = f"https://gamebanana.com/apiv11/{api_model}/{m_id}/ProfilePage"
            res = requests.get(api_url, headers=HEADERS, timeout=10, verify=False)
            if res.status_code == 200:
                files_data = res.json().get("_aFiles", [])
                new_files = [f.get("_sFile") for f in files_data if f.get("_sFile") not in downloaded]
                if new_files:
                    hooks.log(f"[+] Найдено обновление! Новые файлы: {', '.join(map(str, new_files))}")
                    urls.append(f"https://gamebanana.com/{m_type}/{m_id}")
                else:
                    hooks.log("[i] Актуален.")
            else:
                hooks.log(f"[-] Ошибка API (код {res.status_code})")
        except Exception as e:
            hooks.log(f"[-] Ошибка сети: {e}")
        # done двигаем только ПОСЛЕ реальной проверки мода
        hooks.task("total", done=k, total=len(ids), label="Проверка обновлений")
        hooks.cancel.wait(1)
    if urls:
        hooks.log(f"[+] Найдено модов с обновлениями: {len(urls)}")
    else:
        hooks.log("[i] Все моды актуальны. Обновлений не найдено.")
    return urls


# ---------------------------------------------------------------- прогресс
class FileProgress:
    """Собирает прогресс по нескольким файлам одного мода в один бар."""

    def __init__(self, hooks, tid, n_files):
        self.hooks, self.tid, self.n = hooks, tid, max(n_files, 1)
        self.done_bytes, self.frac, self.finished = {}, {}, set()
        self.t0 = time.time()
        self._last = 0.0
        self.lock = threading.Lock()

    def update(self, idx, done, total):
        with self.lock:
            self.done_bytes[idx] = done
            if total > 0:
                self.frac[idx] = min(done / total, 0.999)
            now = time.time()
            if now - self._last < 0.15:
                return
            self._last = now
            self._emit(now)

    def finish(self, idx):
        with self.lock:
            self.frac[idx] = 1.0
            self.finished.add(idx)
            self._emit(time.time())

    def _emit(self, now):
        progress = (sum(self.frac.values()) / self.n) if self.frac else None
        total_done = sum(self.done_bytes.values())
        speed = total_done / max(now - self.t0, 0.001)
        status = f"Файлов готово: {len(self.finished)}/{self.n} · загружено {total_done / 1048576:.1f} МБ"
        self.hooks.task(self.tid, progress=progress, speed=fmt_speed(speed),
                        status=status, state="running")


# ---------------------------------------------------------------- VirusTotal
def make_confirm(hooks):
    def confirm(title, text, yes="Да", no="Нет"):
        r = hooks.ask("confirm", default=False, title=title, text=text, yes=yes, no=no)
        hooks.check()
        return bool(r)
    return confirm


def vt_check(archive_path, name, opts, hooks, interactive):
    """True — можно продолжать, False — архив удалён (используется авто-распаковщиком)."""
    return dl.vt_gate(archive_path, name, opts["vt_key"], opts["vt_proxy"], hooks.log,
                      make_confirm(hooks), interactive)


# ---------------------------------------------------------------- скачивание
def download_file(ctx, target_file, idx, main_mod_path, is_single, prog):
    """Возвращает 'ok' | 'skipped' | 'failed'. Использует докачку и файл check из downloader.py."""
    hooks, opts, check = ctx["hooks"], ctx["opts"], ctx["check"]
    mod_id, url = ctx["mod_id"], ctx["url"]
    dl_url = target_file.get("_sDownloadUrl")
    name = target_file.get("_sFile", f"file_{mod_id}.zip")
    safe_name = SAFE_NAME.sub("_", os.path.basename(name)) or f"file_{mod_id}.zip"

    if not dl_url:
        hooks.log(f"[-] У файла {name} нет прямой ссылки, пропускаем.")
        prog.finish(idx)
        return "failed"

    if is_single:
        extract_path = main_mod_path
    else:
        sub = clean_folder_name(os.path.splitext(name)[0], f"file_{idx}")
        extract_path = os.path.join(main_mod_path, f"{idx} - {sub}")
        os.makedirs(extract_path, exist_ok=True)

    archive_path = os.path.join(extract_path, safe_name)
    expected = int(target_file.get("_nFilesize") or 0)
    md5 = target_file.get("_sMd5Checksum") or ""
    check.register(name, dl_url, archive_path, extract_path, expected, md5, idx)
    check.update(name, status="downloading")

    hooks.log(f"[*] Скачиваем: {name}")

    def on_prog(done, total):
        check.update(name, downloaded=done, total=total)
        prog.update(idx, done, total)

    result = dl.stream_download(dl_url, archive_path, HEADERS, expected, on_prog, hooks.cancel.is_set, hooks.log,
                              pause_event=hooks.pause)
    if result == "cancelled":
        check.update(name, status="paused")
        hooks.check()          # поднимет Cancelled (общий Стоп) или TaskCancelled (✕ на моде)
        raise Cancelled()
    if result != "ok":
        hooks.log(f"[-] Ошибка скачивания файла {name}. Файл check сохранён, докачать можно кнопкой «Продолжить».")
        check.update(name, status="failed")
        prog.finish(idx)
        return "failed"

    v_status, v_msg = dl.verify_archive(archive_path, expected, md5)
    if v_status != "ok":
        hooks.log(f"[-] {name}: {v_msg}")
        if v_status == "corrupt":
            try:
                os.remove(archive_path)
            except OSError:
                pass
        check.update(name, status="failed")
        prog.finish(idx)
        return "failed"

    hooks.log(f"[+] Файл успешно скачан: {name} ({v_msg})")
    check.update(name, status="downloaded")
    hooks.task(ctx["tid"], status=f"Обработка {name}...")
    outcome = dl.finalize_archive(
        archive_path, extract_path, idx, name, mod_id, url, main_mod_path, ctx["history"], opts,
        opts["vt_key"], hooks.log, make_confirm(hooks), interactive=not opts["sleep_mode"])
    check.update(name, status="done")
    prog.finish(idx)
    return "ok" if outcome == "ok" else "skipped"


def _safe_download(*args):
    try:
        return download_file(*args)
    except Cancelled:
        raise
    except Exception as e:
        args[0]["hooks"].log(f"[-] Ошибка при скачивании: {e}")
        return "failed"


def process_mod(tid, url, opts, hooks, history, update_mode):
    """Обрабатывает одну ссылку. Возвращает (state, status_text)."""
    sleep, turbo = opts["sleep_mode"], opts["turbo_mode"]

    if not update_mode and dl.check_history_url(url, history):
        hooks.log("[!] Этот мод уже найден в истории скачиваний (history.json).")
        if sleep:
            hooks.log("[i] [Режим сна] Мод уже в истории, пропускаем автоматически.")
            return "skipped", "Уже в истории"
        again = hooks.ask("confirm", default=False, title="Мод уже скачан",
                          text=f"{url}\n\nЭтот мод уже есть в истории. Скачать снова?",
                          yes="Скачать снова", no="Пропустить")
        hooks.check()
        if not again:
            hooks.log("[i] Мод пропущен по запросу.")
            return "skipped", "Пропущен"

    match = re.search(r"/(mods|wips)/(\d+)", url)
    if not match:
        hooks.log("[-] Ошибка: это не похоже на ссылку мода или WIP GameBanana.")
        return "error", "Неверная ссылка"
    url_type, mod_id = match.group(1), match.group(2)
    api_model = "Wip" if url_type == "wips" else "Mod"
    api_url = f"https://gamebanana.com/apiv11/{api_model}/{mod_id}/ProfilePage"

    try:
        res = requests.get(api_url, headers=HEADERS, timeout=10, verify=False)
    except Exception as e:
        hooks.log(f"[-] Ошибка сети: {e}")
        return "error", "Ошибка сети"
    if res.status_code != 200:
        hooks.log(f"[-] Не удалось получить данные (код {res.status_code})")
        return "error", f"Ошибка API ({res.status_code})"

    data = res.json()
    files_data = data.get("_aFiles", [])
    if not files_data:
        if url_type == "wips":
            hooks.log("[-] В этой ссылке на wips нет файлов — мод ещё в разработке или произошла ошибка.")
        else:
            hooks.log("[-] У этого мода не найдено файлов для скачивания.")
        return "error", "Нет файлов"

    mod_name = data.get("_sName", f"{url_type}_{mod_id}")
    hooks.task(tid, title=mod_name)

    # --- выбор файлов
    if len(files_data) == 1:
        selected, is_single = [files_data[0]], True
        hooks.log("[i] Найден только один файл. Выбираем его автоматически (без подпапки).")
    elif sleep:
        selected, is_single = files_data, False
        hooks.log(f"[i] [Режим сна] Найдено файлов: {len(files_data)}. Скачиваем ВСЕ версии.")
    else:
        items = []
        for f_item in files_data:
            extra = []
            if f_item.get("_sVersion"):
                extra.append(f"v{f_item['_sVersion']}")
            if f_item.get("_sDescription"):
                extra.append(str(f_item["_sDescription"]))
            items.append({"name": f_item.get("_sFile", "Неизвестный файл"), "extra": " | ".join(extra)})
        ans = hooks.ask("choose_files", default="all", title=mod_name, items=items)
        hooks.check()
        if ans == "skip":
            hooks.log("[i] Мод пропущен по запросу.")
            return "skipped", "Пропущен"
        if isinstance(ans, list) and ans:
            selected = [files_data[i] for i in ans if 0 <= i < len(files_data)]
            is_single = len(selected) == 1
        else:
            selected, is_single = files_data, False

    # --- папка
    folder = clean_folder_name(mod_name, f"{url_type}_{mod_id}")
    main_mod_path = unique_path(os.path.join(opts["save_dir"], folder))
    os.makedirs(main_mod_path, exist_ok=True)
    hooks.log(f"[i] Папка мода: {os.path.basename(main_mod_path)}")
    hooks.task(tid, folder=main_mod_path)        # карточка получает путь для кнопки «Открыть папку»

    hooks.task(tid, status="Сохраняем описание и превью...")
    if dl.save_mod_info(data, mod_id, url_type, main_mod_path, HEADERS):
        hooks.log("[+] Описание сохранено в «Описание + обновления.txt»")
    if opts["download_preview"]:
        ok, info = dl.download_preview_image(data, main_mod_path)
        if ok:
            hooks.log(f"[+] Превью мода сохранено (preview.{info})")
        else:
            hooks.log(f"[-] Превью не скачано: {info}")
    hooks.check()

    # --- файлы
    prog = FileProgress(hooks, tid, len(selected))
    hooks.task(tid, status="Скачиваем...", progress=None, state="running")
    check = dl.CheckFile(main_mod_path, url, mod_id, url_type, mod_name, is_single)
    check.start()
    ctx = {"tid": tid, "hooks": hooks, "opts": opts, "mod_id": mod_id, "url": url,
           "history": history, "check": check}
    results = []
    try:
        if turbo:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                futures = [ex.submit(_safe_download, ctx, f, i, main_mod_path, is_single, prog)
                           for i, f in enumerate(selected, 1)]
            for fut in futures:
                results.append(fut.result())
        else:
            for i, f in enumerate(selected, 1):
                results.append(_safe_download(ctx, f, i, main_mod_path, is_single, prog))
    finally:
        if not check.close():
            hooks.log("[!] Часть файлов не докачана. Файл «check» оставлен в папке мода, "
                      "продолжить можно кнопкой «Продолжить прерванные».")

    hooks.log(f"[+] Всё готово! Папка мода: {os.path.basename(main_mod_path)}")
    if "ok" in results:
        return "done", f"Готово · {os.path.basename(main_mod_path)}", main_mod_path
    if "failed" in results:
        return "error", "Не удалось скачать файлы", main_mod_path
    return "skipped", "Пропущен (VirusTotal)", main_mod_path


def _fmt_size(b):
    if not b:
        return "размер неизвестен"
    mb = b / 1048576
    return f"{mb:.1f} МБ" if mb >= 1 else f"{b / 1024:.0f} КБ"


# ---------------------------------------------------------------- умная очередь загрузок (V8.1)
# Один рабочий поток тянет ссылки из общей очереди. Новые ссылки можно добавлять, пока он работает.
_dl_queue = queue.Queue()        # элементы: (tid, url, opts, hooks, history, update_mode)
_dl_lock = threading.Lock()
_dl_pending = 0                  # ссылок в очереди + в работе
_dl_alive = False                # рабочий поток запущен
_dl_idle = threading.Event()     # set = очередь пуста и воркера нет
_dl_idle.set()
_dl_next_tid = 0                 # сквозные id карточек (не повторяются между запусками)
_dl_total = 0                    # всего ссылок в текущей сессии очереди
_dl_finished = 0
_dl_on_finish = None


def is_downloading():
    return _dl_alive


def _emit_total(hooks):
    with _dl_lock:
        done, total = _dl_finished, _dl_total
    hooks.task("total", done=done, total=total, label="Модов обработано")


def enqueue_urls(urls, opts, hooks, history, update_mode=False, on_finish=None):
    """Кладёт ссылки в очередь. True — запущен новый воркер, False — добавлено к уже идущей очереди.
    on_finish() вызывается один раз, когда очередь опустела (или была остановлена)."""
    global _dl_pending, _dl_alive, _dl_next_tid, _dl_total, _dl_finished, _dl_on_finish
    urls = list(urls)
    if not urls:
        return False
    items = []
    with _dl_lock:
        started_new = not _dl_alive
        if started_new:
            _dl_alive = True
            _dl_idle.clear()
            _dl_total = _dl_finished = 0
            _dl_on_finish = on_finish
        for url in urls:
            _dl_next_tid += 1
            items.append((_dl_next_tid, url, opts, hooks, history, update_mode))
        _dl_total += len(urls)          # прогресс: ожидаемое число растёт под локом
        _dl_pending += len(urls)
    for it in items:                    # карточки «В очереди» создаём ДО того, как воркер сможет взять ссылку
        hooks.register_task(it[0])
        hooks.task(it[0], title=it[1], state="queued", status="В очереди", progress=0, speed="")
    _emit_total(hooks)
    hooks.log(f"[i] {'Всего' if started_new else 'Добавлено в очередь'} ссылок: {len(urls)}")
    for it in items:
        _dl_queue.put(it)
    if started_new:
        threading.Thread(target=_download_worker, daemon=True).start()
    return started_new


def _drain_queue():
    """«Стоп»: вычищает всё, что ещё не началось, и помечает карточки отменёнными."""
    global _dl_pending, _dl_finished
    drained = []
    with _dl_lock:
        while True:
            try:
                drained.append(_dl_queue.get_nowait())
            except queue.Empty:
                break
        _dl_pending -= len(drained)
        _dl_finished += len(drained)
    for it in drained:
        it[3].task(it[0], state="cancelled", status="Остановлено", speed="", progress=0)


def _process_one_url(tid, url, opts, hooks, history, update_mode, label):
    """Одна ссылка из очереди. Возвращает "stopped", если нажат общий «Стоп», иначе None."""
    local = hooks.register_task(tid)
    if hooks.global_cancel.is_set():
        hooks.task(tid, state="cancelled", status="Остановлено", speed="", progress=0)
        return "stopped"
    if local.is_set():                  # ✕ нажали ещё в очереди
        hooks.task(tid, state="cancelled", status="Отменено", speed="", progress=0)
        return None
    th = hooks.for_task(local, hooks.task_pauses.get(tid))
    hooks.log(f"[{label}] Обработка: {url}")
    hooks.task(tid, state="running", status="Получаем данные...", progress=None)
    try:
        res = process_mod(tid, url, opts, th, history, update_mode)
        state, status = res[0], res[1]
        fields = dict(state=state, status=status, speed="", progress=1.0 if state == "done" else 0)
        if len(res) > 2 and res[2]:
            fields["folder"] = res[2]
        hooks.task(tid, **fields)
    except TaskCancelled:
        hooks.log(f"[i] Мод {label} отменён пользователем, идём дальше.")
        hooks.task(tid, state="cancelled", status="Отменено", speed="", progress=0)
    except Cancelled:
        hooks.task(tid, state="cancelled", status="Остановлено", speed="", progress=0)
        return "stopped"
    except Exception as e:
        hooks.log(f"[-] Ошибка при обработке: {e}")
        hooks.task(tid, state="error", status=str(e)[:120], speed="", progress=0)
    return None


def _download_worker():
    global _dl_pending, _dl_alive, _dl_finished, _dl_on_finish
    last_hooks, stopped = None, False
    try:
        while True:
            try:
                tid, url, opts, hooks, history, update_mode = _dl_queue.get(timeout=0.5)
            except queue.Empty:
                with _dl_lock:
                    if _dl_pending <= 0:        # решение «выходить» принимается под локом — ссылки не теряются
                        _dl_alive = False
                        break
                continue
            last_hooks = hooks
            with _dl_lock:
                label = f"{_dl_finished + 1}/{_dl_total}"
            try:
                res = _process_one_url(tid, url, opts, hooks, history, update_mode, label)
            except Exception as e:
                hooks.log(f"[-] Ошибка очереди: {e}")
                res = None
            if res == "stopped":
                stopped = True
                _drain_queue()
            with _dl_lock:
                _dl_pending -= 1
                _dl_finished += 1
            _emit_total(hooks)
            hooks.log("-" * 40)
    finally:
        with _dl_lock:
            _dl_alive = False
            cb, _dl_on_finish = _dl_on_finish, None
        _dl_idle.set()
        if last_hooks is not None:
            last_hooks.log("[i] Остановлено пользователем." if stopped else
                           "[+] Все задачи выполнены!")
        if cb:
            try:
                cb()
            except Exception:
                pass


def run_batch(urls, opts, hooks, history, update_mode=False):
    """Совместимость: кладёт ссылки в очередь и ждёт, пока она опустеет."""
    enqueue_urls(urls, opts, hooks, history, update_mode)
    while not _dl_idle.wait(0.3):
        pass
    return "cancelled" if hooks.global_cancel.is_set() else "finished"


def _inspect_one(tid, url, hooks, history):
    """Один проход «только проверить»: ничего не качаем, папки и историю не трогаем."""
    hooks.check()
    m = re.search(r"/(mods|wips)/(\d+)", url)
    if not m:
        hooks.log("[-] Это не похоже на ссылку мода или WIP GameBanana.")
        return "error", "Неверная ссылка"
    url_type, mod_id = m.group(1), m.group(2)
    data, err = dl.fetch_mod_data(url, HEADERS)
    if not data:
        hooks.log(f"[-] Не удалось получить данные ({err})")
        return "error", f"Ошибка API: {err}"
    hooks.check()
    name = data.get("_sName", f"{url_type}_{mod_id}")
    files = data.get("_aFiles", [])
    hooks.task(tid, title=name)

    in_history = dl.check_history_url(url, history)
    known = set((history.get(str(mod_id)) or {}).get("files", []))
    new_files = [f for f in files if f.get("_sFile") not in known] if in_history else files
    if not in_history:
        mark = "новый"
    elif new_files:
        mark = f"в истории, есть новые файлы: {len(new_files)}"
    else:
        mark = "уже в истории"

    total_size = sum(int(f.get("_nFilesize") or 0) for f in files)
    hooks.log(f"[i] {name} — {mark}")
    lines = []
    for f in files:
        line = f"{f.get('_sFile', '?')} ({_fmt_size(int(f.get('_nFilesize') or 0))})"
        hooks.log(f"[i]    • {line}")
        lines.append("• " + line)
    if not files:
        hooks.log("[!]    У мода нет файлов для скачивания.")
    shown = lines[:8] + ([f"…и ещё {len(lines) - 8}"] if len(lines) > 8 else [])
    status = f"{mark} · файлов: {len(files)} · {_fmt_size(total_size)}"
    if shown:
        status += "\n" + "\n".join(shown)
    return "info", status


def inspect_links(urls, hooks, history):
    """Режим «Проверить»: по аналогии с run_batch, но без скачивания, папок и записи в историю."""
    n = len(urls)
    for i, u in enumerate(urls, 1):
        hooks.register_task(i)
        hooks.task(i, title=u, state="queued", status="В очереди", progress=0, speed="")
    hooks.task("total", done=0, total=n, label="Проверено ссылок")
    hooks.log(f"[i] Режим «только проверить»: ссылок {n}. Ничего не скачивается.")

    finished = 0
    stopped_at = None
    for i, url in enumerate(urls, 1):
        if hooks.cancel.is_set():
            stopped_at = i
            break
        local = hooks.register_task(i)
        if local.is_set():
            hooks.task(i, state="cancelled", status="Отменено", progress=0)
            finished += 1
            hooks.task("total", done=finished, total=n, label="Проверено ссылок")
            continue
        th = hooks.for_task(local, hooks.task_pauses.get(i))
        hooks.log(f"[{i}/{n}] Проверка: {url}")
        hooks.task(i, state="running", status="Запрашиваем GameBanana...", progress=None)
        try:
            state, status = _inspect_one(i, url, th, history)
            hooks.task(i, state=state, status=status, progress=0)
        except TaskCancelled:
            hooks.task(i, state="cancelled", status="Отменено", progress=0)
        except Cancelled:
            stopped_at = i
            break
        except Exception as e:
            hooks.log(f"[-] Ошибка при проверке: {e}")
            hooks.task(i, state="error", status=str(e)[:120], progress=0)
        finished += 1
        hooks.task("total", done=finished, total=n, label="Проверено ссылок")
    if stopped_at is not None:
        hooks.log("[i] Остановлено пользователем.")
        for j in range(stopped_at, n + 1):
            hooks.task(j, state="cancelled", status="Остановлено", progress=0)
        return "cancelled"
    hooks.log("[+] Проверка завершена. Ничего не скачано и не записано в историю.")
    return "finished"


# ---------------------------------------------------------------- предпросмотр страницы мода
def clean_html(raw):
    if not raw:
        return ""
    t = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", str(raw), flags=re.I)
    t = re.sub(r"<li[^>]*>", "• ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def time_ago(ts, now=None):
    """'время назад' по unix-времени: 45s, 2m, 5h, 1d, 3w, 2mo, 1y."""
    d = max(0, int((now if now is not None else time.time()) - float(ts)))
    for sec, suf in ((31536000, "y"), (2592000, "mo"), (604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if d >= sec:
            return f"{d // sec}{suf}"
    return f"{d}s"


def fmt_date(ts):
    return datetime.fromtimestamp(float(ts)).strftime("%d.%m.%Y") if ts else "—"


def _image_url(data):
    for imgs in ((data.get("_aPreviewMedia") or {}).get("_aImages"), data.get("_aImages")):
        if isinstance(imgs, list) and imgs:
            img = imgs[0]
            base, fn = img.get("_sBaseUrl", ""), img.get("_sFile530") or img.get("_sFile", "")
            if base and fn:
                return base.rstrip("/") + "/" + fn.lstrip("/")
    return data.get("_sImageUrl") or ""


def fetch_preview(text):
    """Данные для окна предпросмотра. Возвращает (info, ошибка). Запросы: ProfilePage + Updates."""
    urls = parse_links(text or "")
    if not urls:
        return None, "пустой ввод"
    url = urls[0]
    data, err = dl.fetch_mod_data(url, HEADERS)
    if not data:
        return None, err
    m = re.search(r"/(mods|wips)/(\d+)", url)
    url_type, mod_id = m.group(1), m.group(2)
    files = [{"name": f.get("_sFile", "?"), "size": int(f.get("_nFilesize") or 0)}
             for f in data.get("_aFiles", [])]

    updates = []
    try:
        api_model = "Wip" if url_type == "wips" else "Mod"
        res = requests.get(f"https://gamebanana.com/apiv11/{api_model}/{mod_id}/Updates?_nPerpage=50",
                           headers=HEADERS, timeout=10, verify=False)
        records = res.json().get("_aRecords", []) if res.status_code == 200 else []
    except Exception:
        records = []
    for u in records:
        title = u.get("_sTitle") or u.get("_sName") or "Без названия"
        ver = u.get("_sVersion")
        if ver and str(ver) not in title:
            title += f" {ver}"
        text_u = clean_html(u.get("_sText"))
        for ch in (u.get("_aChangeLog") or []):
            if isinstance(ch, dict) and ch.get("_sText"):
                text_u += ("\n" if text_u else "") + "• " + clean_html(ch["_sText"])
        names = [f.get("_sFile") for f in (u.get("_aFiles") or u.get("_aAttachments") or [])
                 if isinstance(f, dict) and f.get("_sFile")]
        ts = u.get("_tsDateAdded") or 0
        updates.append({"title": title, "text": text_u, "files": names, "ts": ts,
                        "date": fmt_date(ts), "ago": time_ago(ts) if ts else ""})
    updates.sort(key=lambda x: x["ts"], reverse=True)       # новые сверху
    return {"url": f"https://gamebanana.com/{url_type}/{mod_id}", "type": url_type, "id": mod_id,
            "name": data.get("_sName", f"{url_type}_{mod_id}"),
            "desc": clean_html(data.get("_sText")), "image": _image_url(data),
            "files": files, "updates": updates}, ""


# ---------------------------------------------------------------- докачка / целостность
def resume_all(save_dir, opts, hooks, history, deep=False):
    """Докачивает всё по файлам check. deep=True: ещё и MD5-проверка + проверка сохранённых архивов."""
    checks = dl.find_check_files(save_dir)
    n = len(checks)
    if not n:
        hooks.log(f"[i] Прерванных загрузок не найдено в: {save_dir}")
    for i, (path, data) in enumerate(checks, 1):
        hooks.register_task(i)
        hooks.task(i, title=data.get("mod_name") or data.get("url") or path, state="queued",
                   status="В очереди", progress=0, speed="", folder=os.path.dirname(path))
    if n:
        hooks.task("total", done=0, total=n, label="Модов проверено")
    finished = 0
    stopped_at = None
    for i, (path, data) in enumerate(checks, 1):
        if hooks.cancel.is_set():
            stopped_at = i
            break
        local = hooks.register_task(i)
        if local.is_set():
            hooks.task(i, state="cancelled", status="Отменено", speed="", progress=0)
            finished += 1
            hooks.task("total", done=finished, total=n, label="Модов проверено")
            continue
        th = hooks.for_task(local, hooks.task_pauses.get(i))
        hooks.log(f"[{i}/{n}] Продолжаем: {data.get('mod_name') or data.get('url')}")
        hooks.task(i, state="running", status="Проверяем и докачиваем...", progress=None)
        last = {"t": 0.0, "t0": time.time()}

        def on_progress(name, done, total, _i=i, _last=last):
            now = time.time()
            if now - _last["t"] < 0.2:
                return
            _last["t"] = now
            speed = done / max(now - _last["t0"], 0.001)
            hooks.task(_i, progress=(done / total) if total else None, speed=fmt_speed(speed),
                       status=f"{name}: {done / 1048576:.1f} МБ")

        try:
            state, text = dl.resume_from_check(
                path, opts, opts["vt_key"], history, log=th.log, confirm=make_confirm(th),
                on_progress=on_progress, should_cancel=th.cancel.is_set, deep=deep, pause_event=th.pause,
                interactive=not opts["sleep_mode"])
        except TaskCancelled:
            state, text = "cancelled", "Отменено"
        except Cancelled:
            stopped_at = i
            break
        except Exception as e:
            hooks.log(f"[-] Ошибка: {e}")
            state, text = "error", str(e)[:120]
        if state == "cancelled":
            if hooks.cancel.is_set():
                stopped_at = i
                break
            hooks.log(f"[i] Мод {i}/{n} отменён пользователем, идём дальше.")
            hooks.task(i, state="cancelled", status="Отменено", speed="", progress=0)
        else:
            ui_state = "done" if state == "done" else "error"
            hooks.task(i, state=ui_state, status=text, speed="", progress=1.0 if state == "done" else 0,
                       folder=os.path.dirname(path))
        finished += 1
        hooks.task("total", done=finished, total=n, label="Модов проверено")
    if stopped_at is not None:
        hooks.log("[i] Остановлено пользователем.")
        for j in range(stopped_at, n + 1):
            hooks.task(j, state="cancelled", status="Остановлено", speed="", progress=0)
        return "cancelled"
    if deep:
        hooks.log("[*] Проверяем сохранённые архивы по данным GameBanana...")
        dl.verify_history_archives(save_dir, history, hooks.log)
    hooks.log("[+] Готово.")
    return "finished"


# ---------------------------------------------------------------- авто-распаковщик
def watch_folder(opts, hooks, history, stop):
    src = dl.AUTO_UNPACK_DIR
    os.makedirs(src, exist_ok=True)
    hooks.log(f"[i] Слежение за папкой «{src}» запущено. Кидайте туда .zip / .rar / .7z")
    processed = set()
    while not stop.is_set():
        try:
            files = [f for f in os.listdir(src) if f.lower().endswith((".zip", ".rar", ".7z"))]
        except Exception as e:
            hooks.log(f"[-] Не удаётся прочитать папку: {e}")
            stop.wait(2)
            continue
        processed.intersection_update(files)
        for file in files:
            if stop.is_set():
                break
            if file in processed:
                continue
            file_path = os.path.join(src, file)
            try:
                size1 = os.path.getsize(file_path)
                if stop.wait(opts["auto_unpack_check_speed"]):
                    break
                size2 = os.path.getsize(file_path)
                if size1 != size2 or size1 <= 0:
                    continue  # ещё докачивается
                processed.add(file)
                _handle_dropped(file, file_path, opts, hooks, history)
            except Cancelled:
                break
            except Exception as e:
                hooks.log(f"[-] Ошибка обработки {file}: {e}")
        stop.wait(1)
    hooks.log("[i] Слежение остановлено.")


def _handle_dropped(file, file_path, opts, hooks, history):
    hooks.log(f"[+] Обнаружен новый файл: {file}")
    if opts["use_virustotal"] and opts["vt_key"]:
        if not vt_check(file_path, file, opts, hooks, interactive=True):
            return

    mod_name = clean_folder_name(os.path.splitext(file)[0], "mod")
    if opts["auto_unpack_ask_name"]:
        ans = hooks.ask("text", default=mod_name, title="Название папки",
                        text=f"Введите название папки для «{file}»:")
        hooks.check()
        if ans:
            mod_name = clean_folder_name(ans, mod_name)

    target = unique_path(os.path.join(opts["save_dir"], mod_name))
    os.makedirs(target, exist_ok=True)

    if opts["disable_unpack"]:
        shutil.move(file_path, os.path.join(target, file))
        hooks.log(f"[+] Архив перемещён в «{os.path.basename(target)}» (распаковка отключена).")
        dl.add_to_history("Browser", file, mod_name, "From_Browser", history, "mods")
        return

    hooks.log(f"[*] Распаковываем в «{os.path.basename(target)}»...")
    if dl.smart_extract(file_path, target):
        os.remove(file_path)
        hooks.log("[+] Успешно распаковано!")
        dl.add_to_history("Browser", file, mod_name, "From_Browser", history, "mods")
    else:
        hooks.log("[-] Ошибка распаковки. Файл оставлен в папке.")
