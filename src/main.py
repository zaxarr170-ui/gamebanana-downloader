"""
app.py — графический интерфейс (Flet) для загрузчика модов GameBanana.

Запуск:   python app.py      (или: flet run app.py)
Файлы downloader.py, core.py и app.py должны лежать в одной папке.
"""
import os
import re
import sys
import queue
import asyncio
import threading
import subprocess
import traceback
import colorsys
import math
import webbrowser
from collections import deque

# Если запущено без консоли (pythonw), stdout может быть None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import flet as ft

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None

import downloader as dl   # твой исходный скрипт (логика)

# ---------------------------------------------------------------- Android
# В собранном приложении Flet даёт приватную папку через FLET_APP_STORAGE_DATA.
# Все настройки/логи/моды складываем туда, чтобы не зависеть от пути к скрипту.
IS_ANDROID = bool(os.environ.get("FLET_APP_STORAGE_DATA")) or bool(os.environ.get("ANDROID_ROOT"))
_DATA_DIR = os.environ.get("FLET_APP_STORAGE_DATA") or os.environ.get("FLET_APP_STORAGE_TEMP")
if IS_ANDROID and _DATA_DIR:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        dl.BASE_DIR = _DATA_DIR
        dl.DEFAULT_SAVE_DIR = os.path.join(_DATA_DIR, "downloaded_mods")
        dl.AUTO_UNPACK_DIR = os.path.join(_DATA_DIR, "auto_unpack")
        dl.SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")
        dl.HISTORY_FILE = os.path.join(_DATA_DIR, "history.json")
        dl.LOG_FILE = os.path.join(_DATA_DIR, "downloader.log")
        dl.VT_KEY_FILE = os.path.join(_DATA_DIR, "vt_api_key.txt")
        dl.LINKS_FILE = os.path.join(_DATA_DIR, "links.txt")
    except OSError:
        pass

import core               # обёртка с колбэками

ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class LogStream:
    """Перехватывает print() из downloader.py и отправляет строки в окно лога."""
    encoding = "utf-8"

    def __init__(self, emit):
        self.emit = emit
        self.buf = ""
        self.lock = threading.Lock()
        self.file = open(dl.LOG_FILE, "a", encoding="utf-8")

    def write(self, s):
        n = len(s)
        s = ANSI.sub("", s)
        with self.lock:
            self.file.write(s)
            self.file.flush()
            *lines, self.buf = (self.buf + s.replace("\r", "\n")).split("\n")
        for ln in lines:
            if ln.strip():
                self.emit(ln.rstrip())
        return n

    def flush(self):
        pass

    def isatty(self):
        return False


def rainbow(h, s=0.6, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def resource_path(name):
    """Путь к файлу рядом с программой или внутри exe (PyInstaller)."""
    for base in (getattr(sys, "_MEIPASS", None), dl.BASE_DIR, os.path.dirname(os.path.abspath(__file__))):
        if base and os.path.exists(os.path.join(base, name)):
            return os.path.join(base, name)
    return None


TITLE = "Умный загрузчик модов GameBanana"
SIGNATURE = "@ziler_mods TT V8.0"
TIKTOK_NICK = "@ziler_mods"
TIKTOK_URL = "https://www.tiktok.com/@ziler_mods"
MAX_BLOCKS = 5

# ---------------------------------------------------------------- локализация
LOCALES = {
    "ru": {
        "tab_dl": "Загрузка", "tab_queue": "Очередь", "tab_log": "Лог", "tab_watch": "Авто-распаковка",
        "tab_history": "История", "tab_settings": "Настройки",
        "queue_title": "Очередь загрузок", "log_title": "Журнал работы", "history_title": "История скачиваний",
        "btn_inspect": "Проверить",
        "tip_inspect": "Только посмотреть: названия, файлы, размеры. Ничего не скачивается",
        "btn_stop": "Стоп", "btn_links": "Из links.txt", "btn_updates": "Проверить обновления",
        "btn_paste": "Вставить из буфера", "tip_paste": "То же, что Ctrl+Shift+V",
        "btn_txt": "Из .txt файла", "btn_resume": "Продолжить прерванные",
        "btn_verify": "Проверить целостность файлов", "btn_collection": "Добавить из коллекции",
        "collection_title": "Коллекция GameBanana", "collection_label": "Ссылка на коллекцию GameBanana",
        "btn_add_block": "Добавить", "tip_add_block": "Добавить ещё один блок ссылок (до 5)",
        "btn_dl": "Скачать", "tip_dl_block": "Скачать только ссылки из этого блока",
        "tip_del_block": "Удалить блок",
        "download_started": "Загрузка запущена", "added_to_queue": "Добавлено в очередь загрузок",
        "block_label": "Блок {n}: ссылки или ID модов (по одной в строке)",
        "sleep": "Режим сна (полный автопилот, без вопросов)",
        "turbo": "Турбо-режим (3 потока на файлы мода)",
        "custom_dir": "Выбирать кастомную папку",
        "hint_keys": "F11 — полный экран | Ctrl+Shift+V — вставить ссылки",
        "preview_label": "Окно странички", "preview_field": "ID или ссылка на мод",
        "btn_preview": "Просмотр",
        "tip_pause": "Пауза", "tip_resume": "Продолжить", "st_paused": "Пауза", "st_resuming": "Продолжаем...",
        "pv_loading": "Загружаю данные мода...", "pv_err": "Не удалось получить данные: {e}",
        "pv_empty": "Вставь ID или ссылку на мод", "pv_files": "Файлы", "pv_updates": "Обновления",
        "pv_no_updates": "Обновлений пока нет", "pv_download": "Скачать этот мод", "pv_close": "Закрыть",
        "pv_added": "Ссылка добавлена в первый блок", "pv_no_desc": "Описание отсутствует",
        "s_title": "Настройки", "s_lang": "Язык интерфейса", "s_light": "Светлая тема",
        "s_notify": "По завершении выводить окно на передний план",
        "s_clearlog": "Очищать лог при запуске", "s_vt": "Проверять архивы через VirusTotal",
        "s_nounpack": "Не распаковывать архивы (оставлять как есть)",
        "s_preview_img": "Скачивать превью (картинки)", "s_autounpack": "Авто-распаковщик",
        "s_askname": "Спрашивать название папки мода",
    },
    "en": {
        "tab_dl": "Download", "tab_queue": "Queue", "tab_log": "Log", "tab_watch": "Auto-unpack",
        "tab_history": "History", "tab_settings": "Settings",
        "queue_title": "Download queue", "log_title": "Activity log", "history_title": "Download history",
        "btn_inspect": "Check",
        "tip_inspect": "Look only: names, files, sizes. Nothing is downloaded",
        "btn_stop": "Stop", "btn_links": "From links.txt", "btn_updates": "Check for updates",
        "btn_paste": "Paste from clipboard", "tip_paste": "Same as Ctrl+Shift+V",
        "btn_txt": "From .txt file", "btn_resume": "Resume interrupted",
        "btn_verify": "Verify file integrity", "btn_collection": "Add from collection",
        "collection_title": "GameBanana collection", "collection_label": "GameBanana collection link",
        "btn_add_block": "Add", "tip_add_block": "Add another link block (up to 5)",
        "btn_dl": "Download", "tip_dl_block": "Download only the links from this block",
        "tip_del_block": "Remove block",
        "download_started": "Download started", "added_to_queue": "Added to the download queue",
        "block_label": "Block {n}: mod links or IDs (one per line)",
        "sleep": "Sleep mode (full autopilot, no questions)",
        "turbo": "Turbo mode (3 threads for mod files)",
        "custom_dir": "Use custom folder",
        "hint_keys": "F11 — fullscreen | Ctrl+Shift+V — paste links",
        "preview_label": "Page preview", "preview_field": "Mod ID or link",
        "btn_preview": "Preview",
        "tip_pause": "Pause", "tip_resume": "Resume", "st_paused": "Paused", "st_resuming": "Resuming...",
        "pv_loading": "Loading mod data...", "pv_err": "Could not get data: {e}",
        "pv_empty": "Enter a mod ID or link", "pv_files": "Files", "pv_updates": "Updates",
        "pv_no_updates": "No updates yet", "pv_download": "Download this mod", "pv_close": "Close",
        "pv_added": "Link added to the first block", "pv_no_desc": "No description",
        "s_title": "Settings", "s_lang": "Interface language", "s_light": "Light theme",
        "s_notify": "Bring window to front when finished",
        "s_clearlog": "Clear log on startup", "s_vt": "Scan archives with VirusTotal",
        "s_nounpack": "Do not unpack archives (keep as is)",
        "s_preview_img": "Download previews (images)", "s_autounpack": "Auto-unpacker",
        "s_askname": "Ask for mod folder name",
    },
}
_LANG = ["ru"]
I18N = []          # (контрол, атрибут, ключ) — перерисовываются при смене языка


def t(key):
    return LOCALES.get(_LANG[0], LOCALES["ru"]).get(key) or LOCALES["ru"].get(key, key)


def tr(ctrl, **attrs):
    """tr(ctrl, label="ключ", tooltip="ключ"): ставит текст и запоминает контрол для смены языка."""
    for attr, key in attrs.items():
        setattr(ctrl, attr, t(key))
        I18N.append((ctrl, attr, key))
    return ctrl


# ---------------------------------------------------------------- жидкое стекло
# Фон из размытых цветных пятен + полупрозрачные панели со светящейся кромкой.
GLASS = {"light": False, "panels": [], "cards": [], "blobs": [], "bg": None, "tabbar": None}


def glass_palette():
    """Цвета стекла для текущей темы."""
    if GLASS["light"]:
        return {
            "bg": ["#eef2fb", "#e4e9f8", "#f9eefb"],
            "fill": [ft.Colors.with_opacity(0.78, ft.Colors.WHITE),
                     ft.Colors.with_opacity(0.42, ft.Colors.WHITE)],
            "card": [ft.Colors.with_opacity(0.66, ft.Colors.WHITE),
                     ft.Colors.with_opacity(0.34, ft.Colors.WHITE)],
            "sunken": [ft.Colors.with_opacity(0.50, ft.Colors.WHITE),
                       ft.Colors.with_opacity(0.24, ft.Colors.WHITE)],
            "edge": [ft.Colors.with_opacity(0.98, ft.Colors.WHITE),
                     ft.Colors.with_opacity(0.30, ft.Colors.WHITE)],
            "blob": ["#8b7cf6", "#4fc3f7", "#ff8ac4", "#4fd8b0"],
            "blob_alpha": 0.42,
            "shadow": ft.Colors.with_opacity(0.16, "#2b2350"),
        }
    return {
        "bg": ["#0a0918", "#171034", "#08121f"],
        "fill": [ft.Colors.with_opacity(0.14, ft.Colors.WHITE),
                 ft.Colors.with_opacity(0.04, ft.Colors.WHITE)],
        "card": [ft.Colors.with_opacity(0.10, ft.Colors.WHITE),
                 ft.Colors.with_opacity(0.03, ft.Colors.WHITE)],
        "sunken": [ft.Colors.with_opacity(0.34, ft.Colors.BLACK),
                   ft.Colors.with_opacity(0.18, ft.Colors.BLACK)],
        "edge": [ft.Colors.with_opacity(0.38, ft.Colors.WHITE),
                 ft.Colors.with_opacity(0.05, ft.Colors.WHITE)],
        "blob": ["#7c4dff", "#00b0ff", "#ff4fa3", "#00e5b0"],
        "blob_alpha": 0.55,
        "shadow": ft.Colors.with_opacity(0.50, "#000000"),
    }


def glass_gradient(colors):
    return ft.LinearGradient(begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT, colors=colors)


def glass_fill(role):
    c = glass_palette()
    return c.get(role) or c["fill"]


def glass_shadow():
    return ft.BoxShadow(blur_radius=30, spread_radius=-12, offset=ft.Offset(x=0, y=12),
                        color=glass_palette()["shadow"])


def glass(content=None, padding=14, radius=22, expand=False, role="fill",
          elevated=True, register=True):
    """Панель «жидкое стекло»: кромка-подсветка, полупрозрачная заливка, мягкая тень."""
    inner = ft.Container(content=content, padding=padding, border_radius=max(2, radius - 1),
                         gradient=glass_gradient(glass_fill(role)), expand=expand)
    outer = ft.Container(content=inner, padding=1, border_radius=radius, expand=expand,
                         gradient=glass_gradient(glass_palette()["edge"]),
                         shadow=glass_shadow() if elevated else None)
    if register:
        GLASS["panels"].append({"outer": outer, "inner": inner, "role": role, "elevated": elevated})
    return outer


def glass_blob(left, top, size, index):
    """Мягкое цветное пятно на фоне (даёт эффект размытого стекла под панелями)."""
    c = glass_palette()
    col = c["blob"][index % len(c["blob"])]
    return ft.Container(
        left=left, top=top, width=size, height=size, border_radius=size // 2,
        blur=max(8, size // 10),
        animate=ft.Animation(1400, ft.AnimationCurve.EASE_IN_OUT),
        gradient=ft.RadialGradient(colors=[ft.Colors.with_opacity(c["blob_alpha"], col),
                                           ft.Colors.with_opacity(0.0, col)]))


def build_background():
    """Слои фона: градиент во весь экран + размытые пятна (все позиционированы по краям)."""
    c = glass_palette()
    bg = ft.Container(left=0, top=0, right=0, bottom=0, gradient=glass_gradient(c["bg"]))
    GLASS["bg"] = bg
    layers = [bg]
    for left, top, size, i in ((-180, -200, 620, 0), (660, -150, 560, 1),
                               (720, 400, 640, 2), (30, 500, 520, 3)):
        blob = glass_blob(left, top, size, i)
        GLASS["blobs"].append((blob, left, top, i))
        layers.append(blob)
    return layers


def tab_indicator():
    return ft.Colors.with_opacity(0.85 if GLASS["light"] else 0.20, ft.Colors.WHITE)


def refresh_glass():
    """Перекрасить всё стекло после смены светлой/тёмной темы."""
    c = glass_palette()
    for p in GLASS["panels"]:
        p["inner"].gradient = glass_gradient(glass_fill(p["role"]))
        p["outer"].gradient = glass_gradient(c["edge"])
        if p["elevated"]:
            p["outer"].shadow = glass_shadow()
    for p in GLASS["cards"]:
        p["inner"].gradient = glass_gradient(c["card"])
        p["outer"].gradient = glass_gradient(c["edge"])
    if GLASS["bg"] is not None:
        GLASS["bg"].gradient = glass_gradient(c["bg"])
    for blob, left, top, i in GLASS["blobs"]:
        col = c["blob"][i % len(c["blob"])]
        blob.gradient = ft.RadialGradient(colors=[ft.Colors.with_opacity(c["blob_alpha"], col),
                                                  ft.Colors.with_opacity(0.0, col)])
    if GLASS["tabbar"] is not None:
        GLASS["tabbar"].indicator_color = tab_indicator()


def glass_dialog(**kw):
    """Диалог в том же стеклянном стиле (плотная подложка, чтобы текст читался)."""
    kw.setdefault("bgcolor", "#eef2fb" if GLASS["light"] else "#121029")
    kw.setdefault("shape", ft.RoundedRectangleBorder(radius=26))
    return ft.AlertDialog(**kw)


# Цвета средней яркости: читаются и на тёмной, и на светлой теме
STATE_STYLE = {
    "queued":    (ft.Icons.HOURGLASS_EMPTY, ft.Colors.GREY_500),
    "running":   (ft.Icons.DOWNLOADING, ft.Colors.BLUE_500),
    "done":      (ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN_600),
    "error":     (ft.Icons.ERROR, ft.Colors.RED_600),
    "skipped":   (ft.Icons.SKIP_NEXT, ft.Colors.AMBER_700),
    "cancelled": (ft.Icons.CANCEL, ft.Colors.ORANGE_700),
    "info":      (ft.Icons.INFO, ft.Colors.BLUE_500),      # режим «Проверить»
}
TERMINAL = ("done", "error", "skipped", "cancelled", "info")


def log_color(line, light=False):
    """Цвет строки лога по тегу. light=True: более тёмные оттенки для светлой темы."""
    s = line.lstrip()
    if s.startswith("[-]") or "КРИТИЧЕСКАЯ" in s[:20]:
        return ft.Colors.RED_700 if light else ft.Colors.RED_300
    if s.startswith("[+]"):
        return ft.Colors.GREEN_800 if light else ft.Colors.GREEN_300
    if s.startswith("[!]"):
        return ft.Colors.ORANGE_900 if light else ft.Colors.ORANGE_400
    if s.startswith("[i]"):
        return ft.Colors.CYAN_900 if light else ft.Colors.CYAN_300
    if s.startswith("[*]") or s.startswith("->"):
        return ft.Colors.BLUE_800 if light else ft.Colors.BLUE_200
    if re.match(r"^\[\d+/\d+\]", s):
        return ft.Colors.PINK_700 if light else ft.Colors.PINK_200
    return None


class TaskCard:
    """Карточка одного мода в очереди (✕ — отмена только этого мода, папка — когда готово)."""

    def __init__(self, tid, on_cancel=None, on_open_folder=None, on_copy_folder=None, on_pause=None):
        self.tid = tid
        self.state = "queued"
        self.paused = False
        self.folder = None
        self.icon_box = ft.Container(content=ft.Icon(ft.Icons.HOURGLASS_EMPTY, color=ft.Colors.GREY_500))
        self.title = ft.Text("", weight=ft.FontWeight.BOLD, size=14, max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS)
        self.status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.speed = ft.Text("", size=12)
        self.bar = ft.ProgressBar(value=0, bar_height=6)
        self.rainbow = False
        self.btn_folder = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN, icon_size=20, tooltip="Открыть папку мода", visible=False,
            on_click=lambda e: on_open_folder(self) if on_open_folder else None)
        self.btn_copy = ft.IconButton(
            icon=ft.Icons.CONTENT_COPY, icon_size=20, tooltip="Скопировать путь к папке", visible=False,
            on_click=lambda e: on_copy_folder(self) if on_copy_folder else None)
        self.btn_pause = ft.IconButton(
            icon=ft.Icons.PAUSE, icon_size=18, tooltip=t("tip_pause"),
            on_click=lambda e: on_pause(self) if on_pause else None)
        self.btn_cancel = ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=18, tooltip="Отменить только этот мод",
            on_click=lambda e: on_cancel(self) if on_cancel else None)
        self.view = glass(
            padding=12,
            radius=18,
            role="card",
            register=False,
            content=ft.Row(
                controls=[
                    self.icon_box,
                    ft.Column(controls=[self.title, self.status, self.bar], spacing=4, expand=True),
                    self.speed,
                    self.btn_folder,
                    self.btn_copy,
                    self.btn_pause,
                    self.btn_cancel,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
        )
        GLASS["cards"].append({"outer": self.view, "inner": self.view.content})

    def set_paused(self, flag):
        self.paused = flag
        self.btn_pause.icon = ft.Icons.PLAY_ARROW if flag else ft.Icons.PAUSE
        self.btn_pause.tooltip = t("tip_resume") if flag else t("tip_pause")
        self.rainbow = (self.state == "running") and not flag
        if flag:
            self.status.value = t("st_paused")
            self.speed.value = ""
        else:
            self.status.value = t("st_resuming")

    def apply(self, f):
        # пока пауза, поздние обновления статуса/скорости от потока не затирают надпись «Пауза»
        if self.paused and f.get("state", self.state) not in TERMINAL:
            f = {k: v for k, v in f.items() if k not in ("status", "speed")}
        if "title" in f:
            self.title.value = f["title"]
        if "status" in f:
            self.status.value = f["status"]
        if "speed" in f:
            self.speed.value = f["speed"]
        if "progress" in f:
            self.bar.value = f["progress"]      # None = бегущая полоска
        if "folder" in f and f["folder"]:
            self.folder = f["folder"]
        if "state" in f and f["state"] in STATE_STYLE:
            self.state = f["state"]
            icon, color = STATE_STYLE[self.state]
            self.icon_box.content = ft.Icon(icon, color=color)
            self.bar.color = color
            self.rainbow = (self.state == "running") and not self.paused
        # ✕ только пока мод не завершён; «папка» только у готовых
        self.btn_cancel.visible = self.state not in TERMINAL
        self.btn_folder.visible = (self.state == "done" and bool(self.folder))
        self.btn_copy.visible = self.btn_folder.visible
        self.btn_pause.visible = self.state not in TERMINAL
        if self.state in TERMINAL and self.paused:
            self.paused = False
            self.btn_pause.icon = ft.Icons.PAUSE


class AppState:
    def __init__(self):
        self.running = False
        self.queue_mode = False    # идущая задача — умная очередь (кнопки «Скачать» остаются активными)
        self.cancel = threading.Event()
        self.watching = False
        self.watch_stop = threading.Event()
        self.pending_asks = deque()
        self.dialog_open = False
        self.history = dl.load_history()
        self.hooks = None          # хуки текущей задачи (для отмены одного мода)
        self.light = False         # светлая тема
        self.tray = None
        self.tray_title = ""
        self.tray_show = False
        self.tray_quit = False
        self.total = (0, 0)        # (готово, всего) для трея
        self.notify = None         # (текст, вывести_окно_вперёд) — показывается в ui_loop


class Answer:
    def __init__(self):
        self.event = threading.Event()
        self.value = None


OPEN_HOOK = {"fn": None}   # на Android вместо «открыть папку» показываем путь


def open_path(p):
    p = os.path.abspath(p)
    os.makedirs(p, exist_ok=True)
    if IS_ANDROID:
        fn = OPEN_HOOK["fn"]
        if fn is not None:
            try:
                fn(p)
            except Exception:
                pass
        return
    if sys.platform.startswith("win"):
        os.startfile(p)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def main(page: ft.Page):
    page.title = "Умный загрузчик модов GameBanana"
    page.theme_mode = ft.ThemeMode.DARK
    try:
        # заметные полосы прокрутки, которые можно тянуть мышкой
        page.theme = ft.Theme(
            color_scheme_seed=ft.Colors.DEEP_PURPLE,
            scrollbar_theme=ft.ScrollbarTheme(thumb_visibility=True, thickness=10, radius=8, interactive=True),
            input_decoration_theme=ft.InputDecorationTheme(
                filled=True, fill_color=ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE)),
        )
    except Exception:
        page.theme = ft.Theme(color_scheme_seed=ft.Colors.DEEP_PURPLE)
    page.dark_theme = page.theme          # одинаковая цветовая схема в обеих темах
    page.padding = 0
    if not IS_ANDROID:                     # окно и иконка есть только на десктопе
        try:
            page.window.width = 1020
            page.window.height = 780
            page.window.min_width = 780
            page.window.min_height = 580
            _icon = resource_path("icon.ico")
            if _icon:
                page.window.icon = _icon
        except Exception:
            pass

    S = AppState()
    ui_q = queue.Queue()
    settings = dl.load_settings()
    settings.setdefault("gui_sleep", False)
    settings.setdefault("gui_turbo", False)
    settings.setdefault("gui_save_dir", dl.DEFAULT_SAVE_DIR)
    settings.setdefault("gui_custom_dir", False)
    settings.setdefault("gui_light_theme", False)     # V6.5
    settings.setdefault("gui_notify_front", True)     # V6.5
    settings.setdefault("gui_clear_log", False)       # V7.1
    if settings["gui_clear_log"]:
        try:
            open(dl.LOG_FILE, "w", encoding="utf-8").close()
        except OSError:
            pass
    settings.setdefault("gui_lang", "ru")             # V8.0
    _LANG[0] = settings["gui_lang"] if settings["gui_lang"] in LOCALES else "ru"
    S.light = bool(settings["gui_light_theme"])
    GLASS["light"] = S.light
    page.theme_mode = ft.ThemeMode.LIGHT if S.light else ft.ThemeMode.DARK
    cards = {}

    # перехват print() из downloader.py -> окно лога
    sys.stdout = LogStream(lambda line: ui_q.put(("log", line)))
    sys.stderr = sys.stdout

    picker = ft.FilePicker()
    page.services.append(picker)

    def toast(msg):
        page.show_dialog(ft.SnackBar(ft.Text(msg)))

    def launch(u):
        """Ссылка: на Android — системный браузер, на десктопе — webbrowser."""
        try:
            page.launch_url(u)
        except Exception:
            try:
                webbrowser.open(u)
            except Exception:
                pass

    if IS_ANDROID:                        # «Открыть папку» на Android нечем — показываем путь
        OPEN_HOOK["fn"] = lambda p: toast(f"Папка: {p}")

    def save_settings():
        dl.save_settings(settings)

    # ============================================================ ЛОГ
    log_list = ft.ListView(expand=True, spacing=0, auto_scroll=True)

    def add_log(text):
        for ln in str(text).splitlines():
            if not ln.strip():
                continue
            log_list.controls.append(
                ft.Text(ln, size=12, font_family="Consolas", selectable=True, color=log_color(ln, S.light)))
        if len(log_list.controls) > 1500:
            del log_list.controls[:300]

    def clear_log(e):
        log_list.controls.clear()

    log_box = glass(content=log_list, expand=True, padding=10, radius=16,
                    role="sunken", elevated=False)
    log_tab = ft.Column(expand=True, controls=[
        ft.Row(controls=[
            tr(ft.Text(weight=ft.FontWeight.BOLD, size=16), value="log_title"),
            ft.Container(expand=True),
            ft.OutlinedButton(content="Очистить", icon=ft.Icons.DELETE_SWEEP, on_click=clear_log),
            ft.OutlinedButton(content="Открыть downloader.log", icon=ft.Icons.TEXT_SNIPPET,
                              on_click=lambda e: open_path(os.path.dirname(os.path.abspath(dl.LOG_FILE)))),
        ]),
        log_box,
    ])

    # ============================================================ ОЧЕРЕДЬ
    def empty_state(icon, text):
        label = ft.Text(text, size=14, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.CENTER)
        box = ft.Column(expand=True, alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
                        controls=[ft.Icon(icon, size=72, color=ft.Colors.with_opacity(0.35, ft.Colors.ON_SURFACE)),
                                  label])
        return box, label

    def parse_speed(t):
        m = re.match(r"\s*([\d.,]+)\s*(МБ|КБ)", t or "")
        if not m:
            return 0.0
        return float(m.group(1).replace(",", ".")) * (1048576 if m.group(2) == "МБ" else 1024)

    def current_speed():
        return sum(parse_speed(c.speed.value) for c in cards.values() if c.state == "running")

    queue_list = ft.ListView(expand=True, spacing=8, visible=False)
    queue_empty, _ = empty_state(ft.Icons.INBOX, "Очередь пуста.\nДобавь ссылки на вкладке «Загрузка»")
    queue_speed = ft.Text("", size=13, weight=ft.FontWeight.BOLD, visible=False)

    def refresh_queue_view():
        has = bool(cards)
        queue_list.visible = has
        queue_empty.visible = not has
        queue_speed.visible = has
        queue_speed.value = f"Общая скорость: {core.fmt_speed(current_speed())}"

    queue_tab = ft.Column(expand=True, controls=[
        tr(ft.Text(weight=ft.FontWeight.BOLD, size=16), value="queue_title"),
        ft.Text("Здесь появляются все моды текущего запуска со своим прогрессом.",
                size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        queue_list,
        queue_empty,
        queue_speed,
    ])

    # ============================================================ ЗАГРУЗКА
    blocks = []                      # [{"field", "dl", "del", "row"}]
    blocks_col = ft.Column(spacing=10)
    btn_add_block = ft.OutlinedButton(content="", icon=ft.Icons.ADD)

    def all_text():
        """Текст из всех блоков (для «Проверить», вставки и т.д.)."""
        return "\n".join((b["field"].value or "") for b in blocks)

    def refresh_blocks():
        n = len(blocks)
        btn_add_block.visible = n < MAX_BLOCKS          # «+» прячется при 5 блоках
        for i, b in enumerate(blocks):
            b["field"].label = t("block_label").format(n=i + 1)
            b["del"].visible = n > 1                    # корзина прячется, если блок один

    def add_block(text=""):
        if len(blocks) >= MAX_BLOCKS:
            return
        field = ft.TextField(hint_text="https://gamebanana.com/mods/12345\n67890", value=text,
                             multiline=True, min_lines=3, max_lines=6, expand=True)
        btn_dl = tr(ft.FilledButton(content="", icon=ft.Icons.DOWNLOAD, disabled=S.running and not S.queue_mode),
                    content="btn_dl", tooltip="tip_dl_block")
        btn_del = tr(ft.IconButton(icon=ft.Icons.DELETE_OUTLINE), tooltip="tip_del_block")
        b = {"field": field, "dl": btn_dl, "del": btn_del}
        b["row"] = ft.Row(controls=[field, ft.Column(controls=[btn_dl, btn_del], spacing=4)],
                          vertical_alignment=ft.CrossAxisAlignment.START)
        btn_dl.on_click = lambda e, b=b: on_block_start(b)
        btn_del.on_click = lambda e, b=b: remove_block(b)
        blocks.append(b)
        blocks_col.controls.append(b["row"])
        refresh_blocks()

    def remove_block(b):
        if len(blocks) <= 1:
            return
        blocks.remove(b)
        blocks_col.controls.remove(b["row"])
        refresh_blocks()
        page.update()

    def on_add_block(e):
        add_block()
        page.update()
    btn_add_block.on_click = on_add_block

    def append_to_first(urls):
        """Добавляет ссылки (без дублей по всем блокам) в первый блок. Возвращает число добавленных."""
        have = set(core.parse_links(all_text()))
        new = [u for u in urls if u not in have]
        if new:
            existing = (blocks[0]["field"].value or "").strip()
            blocks[0]["field"].value = (existing + "\n" if existing else "") + "\n".join(new)
        return len(new)

    add_block()

    sleep_sw = ft.Switch(label="Режим сна (полный автопилот, без вопросов)", value=settings["gui_sleep"])
    turbo_sw = ft.Switch(label="Турбо-режим (3 потока на файлы мода)", value=settings["gui_turbo"])

    def on_mode_change(e):
        settings["gui_sleep"] = bool(sleep_sw.value)
        settings["gui_turbo"] = bool(turbo_sw.value)
        save_settings()
    sleep_sw.on_change = on_mode_change
    turbo_sw.on_change = on_mode_change

    async def pick_dir(e):
        try:
            path = await picker.get_directory_path()
        except Exception as ex:
            toast(f"Выбор папки недоступен: {ex}")
            return
        if path and str(path).startswith("content://"):
            toast("Android не даёт писать в эту папку. Оставь папку по умолчанию")
            return
        if path:
            save_dir_field.value = path
            settings["gui_save_dir"] = path
            save_settings()
            refresh_resume_banner()

    custom_sw = ft.Switch(label="Выбирать кастомную папку", value=bool(settings["gui_custom_dir"]))
    save_dir_field = ft.TextField(
        label="Папка для модов", expand=True, disabled=not settings["gui_custom_dir"],
        value=settings["gui_save_dir"] if settings["gui_custom_dir"] else dl.DEFAULT_SAVE_DIR)
    pick_btn = ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="Выбрать папку", on_click=pick_dir,
                             disabled=not settings["gui_custom_dir"])

    def effective_save_dir():
        """Обычная папка, если кастомная выключена; иначе путь из поля."""
        if custom_sw.value:
            p = (save_dir_field.value or "").strip().strip('"\'')
            if p:
                return p if os.path.isabs(p) else os.path.join(dl.BASE_DIR, p)
        return dl.DEFAULT_SAVE_DIR

    def on_custom_change(e):
        on = bool(custom_sw.value)
        settings["gui_custom_dir"] = on
        save_dir_field.value = (settings.get("gui_save_dir") or "") if on else dl.DEFAULT_SAVE_DIR
        save_dir_field.disabled = not on
        pick_btn.disabled = not on
        save_settings()
        refresh_resume_banner()
    custom_sw.on_change = on_custom_change

    def on_save_dir_change(e):
        if custom_sw.value:
            settings["gui_save_dir"] = (save_dir_field.value or "").strip()
            save_settings()
            refresh_resume_banner()
    save_dir_field.on_change = on_save_dir_change

    collection_field = ft.TextField(label="Ссылка на коллекцию GameBanana",
                                    hint_text="https://gamebanana.com/collections/12345", expand=True)

    total_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    total_bar = ft.ProgressBar(value=0, bar_height=8)

    preview_field = ft.TextField(expand=True, on_submit=lambda e: on_preview(e))
    btn_preview = ft.FilledButton(content="", icon=ft.Icons.VISIBILITY)
    btn_inspect = ft.OutlinedButton(content="Проверить", icon=ft.Icons.FACT_CHECK,
                                    tooltip="Только посмотреть: названия, файлы, размеры. Ничего не скачивается")
    btn_stop = ft.OutlinedButton(content="Стоп", icon=ft.Icons.STOP, disabled=True)
    btn_paste = ft.OutlinedButton(content="Вставить из буфера", icon=ft.Icons.CONTENT_PASTE,
                                  tooltip="То же, что Ctrl+Shift+V")
    btn_txt = ft.OutlinedButton(content="Из .txt файла", icon=ft.Icons.UPLOAD_FILE)
    btn_links = ft.OutlinedButton(content="Из links.txt", icon=ft.Icons.LIST_ALT)
    btn_updates = ft.OutlinedButton(content="Проверить обновления", icon=ft.Icons.UPDATE)
    btn_collection = ft.OutlinedButton(content="Добавить из коллекции", icon=ft.Icons.COLLECTIONS_BOOKMARK)
    btn_resume = ft.OutlinedButton(content="Продолжить прерванные", icon=ft.Icons.PLAY_CIRCLE)
    btn_verify = ft.OutlinedButton(content="Проверить целостность файлов", icon=ft.Icons.VERIFIED)
    btn_resume_banner = ft.FilledButton(content="Продолжить", icon=ft.Icons.PLAY_CIRCLE)
    resume_text = ft.Text("")
    resume_banner = ft.Container(
        visible=False, padding=12, border_radius=12,
        bgcolor=ft.Colors.with_opacity(0.15, ft.Colors.AMBER),
        content=ft.Row(controls=[
            ft.Icon(ft.Icons.WARNING_AMBER, color=ft.Colors.AMBER_700),
            resume_text,
            ft.Container(expand=True),
            btn_resume_banner,
        ]))

    def refresh_resume_banner():
        n = len(dl.find_check_files(effective_save_dir()))
        resume_banner.visible = n > 0
        resume_text.value = f"Найдено прерванных загрузок: {n} (файл «check» в папке мода)"

    def set_running(flag):
        S.running = flag
        for b in blocks:
            b["dl"].disabled = flag and not S.queue_mode
        btn_links.disabled = flag
        btn_updates.disabled = flag
        btn_inspect.disabled = flag
        btn_resume.disabled = flag
        btn_verify.disabled = flag
        btn_resume_banner.disabled = flag
        btn_stop.disabled = not flag

    def build_opts():
        opts = dict(settings)
        opts["save_dir"] = effective_save_dir()
        opts["sleep_mode"] = bool(sleep_sw.value)
        opts["turbo_mode"] = bool(turbo_sw.value)
        opts["vt_key"] = core.load_vt_key()
        try:
            opts["auto_unpack_check_speed"] = max(1, min(10, int(settings["auto_unpack_check_speed"])))
        except (TypeError, ValueError):
            opts["auto_unpack_check_speed"] = 2
        return opts

    # ---------------- связь рабочего потока с окном (диалоги)
    def ask_from_worker(cancel_event):
        def ask(kind, default=None, cancel_ev=None, **kw):
            ev = cancel_ev or cancel_event          # cancel_ev = общий Стоп ИЛИ отмена этого мода
            holder = Answer()
            ui_q.put(("ask", kind, kw, holder))
            while not holder.event.wait(0.3):
                if ev.is_set():
                    return default
            return holder.value if holder.value is not None else default
        return ask

    def make_hooks(cancel_event):
        return core.Hooks(
            log=lambda s: ui_q.put(("log", s)),
            task=lambda tid, **f: ui_q.put(("task", tid, f)),
            ask=ask_from_worker(cancel_event),
            cancel=cancel_event,
        )

    def refresh_history():
        S.history.clear()
        S.history.update(dl.load_history())
        render_history()

    def start_job(kind, urls=None):
        if S.running:
            toast("Загрузка уже идёт")
            return
        opts = build_opts()
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", opts["save_dir"]):
            toast("В поле папки вставлена ссылка. Укажи путь к папке на диске")
            return
        if kind != "inspect":                      # «Проверить» не создаёт папки
            try:
                os.makedirs(opts["save_dir"], exist_ok=True)
            except (OSError, ValueError) as ex:
                toast(f"Не удалось создать папку: {ex}")
                return
        S.cancel = threading.Event()
        cancel = S.cancel
        hooks = make_hooks(cancel)
        S.hooks = hooks
        queue_list.controls.clear()
        cards.clear()
        GLASS["cards"].clear()
        S.total = (0, 0)
        refresh_queue_view()
        total_bar.value = 0
        total_text.value = ""
        S.queue_mode = (kind == "queue")
        set_running(True)
        if kind == "queue":
            core.enqueue_urls(urls, opts, hooks, S.history, on_finish=lambda: ui_q.put(("job_done", None)))
            return True

        def run():
            try:
                todo = urls
                update_mode = False
                if kind == "inspect":
                    core.inspect_links(urls, hooks, S.history)
                    return
                if kind in ("resume", "verify"):
                    core.resume_all(opts["save_dir"], opts, hooks, S.history, deep=(kind == "verify"))
                    return
                if kind == "updates":
                    update_mode = True
                    todo = core.check_updates(S.history, hooks)
                    if not todo:
                        return
                core.run_batch(todo, opts, hooks, S.history, update_mode=update_mode)
            except core.Cancelled:
                hooks.log("[i] Остановлено пользователем.")
            except Exception:
                hooks.log("[КРИТИЧЕСКАЯ ОШИБКА]\n" + traceback.format_exc())
            finally:
                ui_q.put(("job_done", None))

        threading.Thread(target=run, daemon=True).start()
        return True

    def on_block_start(b):
        urls = core.parse_links(b["field"].value or "")
        if not urls:
            toast("Вставь хотя бы одну ссылку или ID мода")
            return
        if S.running and not S.queue_mode:             # идёт «Проверить» / обновления / докачка
            toast("Загрузка уже идёт")
            return
        if S.running:                                  # очередь уже работает — просто дописываем
            if S.cancel.is_set():
                toast("Останавливаем...")
                return
            started_new = core.enqueue_urls(urls, build_opts(), S.hooks, S.history,
                                            on_finish=lambda: ui_q.put(("job_done", None)))
        else:
            if not start_job("queue", urls):
                return
            started_new = True
        toast(t("download_started") if started_new else t("added_to_queue"))

    def on_inspect(e):
        urls = core.parse_links(all_text())
        if not urls:
            toast("Вставь хотя бы одну ссылку или ID мода")
            return
        start_job("inspect", urls)

    # ---------- 1. отмена одного мода по карточке ----------
    def cancel_one(card):
        if S.hooks is not None:
            S.hooks.cancel_task(card.tid)
            card.status.value = "Отменяем..."
            card.btn_cancel.disabled = True

    # ---------- пауза / продолжить одного мода ----------
    def pause_one(card):
        if S.hooks is not None:
            card.set_paused(S.hooks.toggle_pause(card.tid))

    # ---------- 5. «Открыть папку мода» ----------
    def open_card_folder(card):
        try:
            open_path(card.folder)
        except Exception as ex:
            toast(f"Не удалось открыть папку: {ex}")

    _clip = {}

    def get_clip():
        if "c" not in _clip:
            _clip["c"] = getattr(page, "clipboard", None) or ft.Clipboard()
            if _clip["c"] not in page.services:
                page.services.append(_clip["c"])
        return _clip["c"]

    async def copy_folder_async(card):
        try:
            await get_clip().set(card.folder)
            toast("Путь скопирован")
        except Exception as ex:
            toast(f"Не удалось скопировать: {ex}")

    def copy_card_folder(card):
        if card.folder:
            page.run_task(copy_folder_async, card)

    # ---------- 6. вставка ссылок: Ctrl+Shift+V и .txt файлы ----------
    def add_links_from_text(text, source):
        urls = core.parse_links(text or "")
        if not urls:
            toast(f"{source}: ссылок или ID не найдено")
            return
        have = set(core.parse_links(all_text()))
        new = [u for u in urls if u not in have]
        append_to_first(new)
        toast(f"{source}: добавлено {len(new)}" + (f" (дублей пропущено: {len(urls) - len(new)})" if len(new) != len(urls) else ""))
        page.update()

    async def paste_links(e=None):
        clip = getattr(page, "clipboard", None)
        if clip is None:
            clip = ft.Clipboard()
            page.services.append(clip)
        try:
            text = await clip.get()
        except Exception as ex:
            toast(f"Не удалось прочитать буфер обмена: {ex}")
            return
        add_links_from_text(text, "Буфер обмена")

    async def pick_txt(e=None):
        try:
            files = await picker.pick_files(allow_multiple=True, allowed_extensions=["txt"])
        except Exception as ex:
            toast(f"Не удалось открыть выбор файлов: {ex}")
            return
        for f in (files or []):
            path = getattr(f, "path", None)
            if not path:
                continue
            try:
                with open(path, "r", encoding="utf-8-sig", errors="ignore") as fh:
                    add_links_from_text(fh.read(), os.path.basename(path))
            except OSError as ex:
                toast(f"Не удалось прочитать {path}: {ex}")

    async def on_key(e):
        if str(e.key).lower() == "f11":
            try:
                page.window.full_screen = not page.window.full_screen
                page.update()
            except Exception:
                pass
        elif e.ctrl and e.shift and str(e.key).lower() == "v":
            await paste_links()
    page.on_keyboard_event = on_key

    def on_stop(e):
        S.cancel.set()
        if S.dialog_open:
            S.dialog_open = False
            page.pop_dialog()
        add_log("[i] Останавливаем...")

    def on_links_file(e):
        urls = core.read_links_file()
        if urls is None:
            toast("links.txt не найден — я создал его. Впиши туда ссылки и нажми кнопку ещё раз")
            return
        if not urls:
            toast("В links.txt нет ссылок")
            return
        append_to_first(urls)
        toast(f"Загружено ссылок из links.txt: {len(urls)}")

    def on_preview(e=None):
        txt = (preview_field.value or "").strip()
        if not txt:
            toast(t("pv_empty"))
            return
        toast(t("pv_loading"))

        def run():
            try:
                res = core.fetch_preview(txt)
            except Exception as ex:
                res = (None, str(ex))
            ui_q.put(("preview", res))
        threading.Thread(target=run, daemon=True).start()

    def show_preview(info):
        def close(e=None):
            page.pop_dialog()

        def download_this(e):
            page.pop_dialog()
            append_to_first([info["url"]])
            toast(t("pv_added"))

        def fsize(b):
            return f"{b / 1048576:.1f} МБ" if b >= 1048576 else (f"{b / 1024:.0f} КБ" if b else "?")

        muted = ft.Colors.ON_SURFACE_VARIANT
        body = []
        if info.get("image"):
            body.append(ft.Image(src=info["image"], width=600, height=240, border_radius=10))
        body.append(ft.Text(info["name"], size=20, weight=ft.FontWeight.BOLD, selectable=True))
        desc = info.get("desc") or t("pv_no_desc")
        body.append(ft.Text(desc[:3000] + ("…" if len(desc) > 3000 else ""), size=13, selectable=True))
        body.append(ft.Text(t("pv_files"), weight=ft.FontWeight.BOLD))
        for f in info.get("files", []):
            body.append(ft.Text(f"• {f['name']}  ({fsize(f['size'])})", size=12, color=muted, selectable=True))
        body.append(ft.Text(t("pv_updates"), weight=ft.FontWeight.BOLD))
        if not info.get("updates"):
            body.append(ft.Text(t("pv_no_updates"), size=12, color=muted))
        for u in info.get("updates", []):
            when = u["date"] + (f"  ·  {u['ago']}" if u["ago"] else "")
            inner = [ft.Row(controls=[ft.Text(u["title"], weight=ft.FontWeight.BOLD, expand=True, selectable=True),
                                      ft.Text(when, size=12, color=muted)])]
            if u["text"]:
                inner.append(ft.Text(u["text"][:1500], size=12, selectable=True))
            if u["files"]:
                inner.append(ft.Text("\n".join(f"• {n}" for n in u["files"]), size=12, color=muted))
            body.append(ft.Container(padding=10, border_radius=10, content=ft.Column(controls=inner, spacing=4),
                                     bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE)))
        page.show_dialog(glass_dialog(
            modal=True,
            content=ft.Container(width=640, height=520, content=ft.Column(controls=body, scroll=ft.ScrollMode.AUTO, spacing=10)),
            actions=[ft.TextButton(content=t("pv_close"), on_click=close),
                     ft.FilledButton(content=t("pv_download"), icon=ft.Icons.DOWNLOAD, on_click=download_this)],
            actions_alignment=ft.MainAxisAlignment.END))

    def on_collection(e):
        url = (collection_field.value or "").strip()
        if not url:
            toast("Вставь ссылку на коллекцию")
            return
        hooks = make_hooks(threading.Event())

        def run():
            urls = core.fetch_collection(url, hooks)
            if urls:
                ui_q.put(("links", urls))
        threading.Thread(target=run, daemon=True).start()
        toast("Получаю список модов коллекции...")

    btn_inspect.on_click = on_inspect
    btn_preview.on_click = lambda e: on_preview(e)
    btn_paste.on_click = paste_links
    btn_txt.on_click = pick_txt
    btn_stop.on_click = on_stop
    btn_links.on_click = on_links_file
    btn_updates.on_click = lambda e: start_job("updates")
    btn_resume.on_click = lambda e: start_job("resume")
    btn_verify.on_click = lambda e: start_job("verify")
    btn_resume_banner.on_click = lambda e: start_job("resume")
    btn_collection.on_click = on_collection

    download_tab = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=14, controls=[
        resume_banner,
        blocks_col,
        btn_add_block,
        ft.Row(controls=[btn_inspect, btn_stop, btn_links, btn_updates], wrap=True),
        ft.Row(controls=[btn_paste, btn_txt], wrap=True),
        ft.Row(controls=[btn_resume, btn_verify], wrap=True),
        ft.Row(controls=[sleep_sw, turbo_sw], wrap=True),
        custom_sw,
        ft.Row(controls=[
            save_dir_field,
            pick_btn,
            ft.IconButton(icon=ft.Icons.OPEN_IN_NEW, tooltip="Открыть папку",
                          on_click=lambda e: open_path(effective_save_dir())),
        ]),
        ft.Divider(),
        tr(ft.Text(weight=ft.FontWeight.BOLD), value="collection_title"),
        ft.Row(controls=[collection_field, btn_collection]),
        ft.Divider(),
        total_text,
        total_bar,
        ft.Divider(),
        tr(ft.Text(weight=ft.FontWeight.BOLD), value="preview_label"),
        ft.Row(controls=[preview_field, btn_preview]),
    ])

    # ============================================================ АВТО-РАСПАКОВЩИК
    watch_status = ft.Text("Остановлен", color=ft.Colors.ON_SURFACE_VARIANT)
    btn_watch = ft.FilledButton(content="Запустить слежение", icon=ft.Icons.PLAY_ARROW)

    def on_watch(e):
        if S.watching:
            S.watch_stop.set()
            return
        opts = build_opts()
        try:
            os.makedirs(opts["save_dir"], exist_ok=True)
        except (OSError, ValueError) as ex:
            toast(f"Не удалось создать папку: {ex}")
            return
        S.watch_stop = threading.Event()
        stop = S.watch_stop
        hooks = make_hooks(stop)
        S.watching = True
        btn_watch.content = "Остановить слежение"
        btn_watch.icon = ft.Icons.STOP
        watch_status.value = f"Слежу за папкой «{dl.AUTO_UNPACK_DIR}»…"
        watch_status.color = ft.Colors.GREEN_600

        def run():
            try:
                core.watch_folder(opts, hooks, S.history, stop)
            except Exception:
                hooks.log("[КРИТИЧЕСКАЯ ОШИБКА]\n" + traceback.format_exc())
            finally:
                ui_q.put(("watch_done", None))
        threading.Thread(target=run, daemon=True).start()
    btn_watch.on_click = on_watch

    watch_tab = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=14, controls=[
        ft.Text("Авто-распаковщик", weight=ft.FontWeight.BOLD, size=16),
        ft.Text(f"Кидай архивы (.zip, .rar, .7z) в папку «{dl.AUTO_UNPACK_DIR}». Программа сама подхватит их, "
                f"проверит на вирусы (если включено) и распакует в папку для модов (вкладка «Загрузка»).",
                color=ft.Colors.ON_SURFACE_VARIANT),
        ft.Row(controls=[
            btn_watch,
            ft.OutlinedButton(content="Открыть папку", icon=ft.Icons.FOLDER_OPEN,
                              on_click=lambda e: open_path(dl.AUTO_UNPACK_DIR)),
        ]),
        watch_status,
    ])

    # ============================================================ ИСТОРИЯ
    history_list = ft.ListView(expand=True, spacing=6)
    history_empty, history_empty_label = empty_state(ft.Icons.HISTORY, "")
    history_count = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    history_search = ft.TextField(
        label="Поиск по названию папки, ID или ссылке", prefix_icon=ft.Icons.SEARCH, dense=True,
        on_change=lambda e: render_history())

    def open_url(url):
        def h(e):
            launch(url)
        return h

    def render_history():
        history_list.controls.clear()
        items = list(S.history.items())
        q = (history_search.value or "").strip().lower()
        shown = 0
        for mod_id, data in reversed(items):
            files = data.get("files", [])
            url = data.get("url", "")
            folder = str(data.get("folder", mod_id))
            if q and q not in folder.lower() and q not in str(mod_id).lower() and q not in str(url).lower():
                continue
            shown += 1
            trailing = None
            if str(url).startswith("http"):
                trailing = ft.IconButton(icon=ft.Icons.OPEN_IN_NEW, tooltip="Открыть страницу",
                                         on_click=open_url(url))
            history_list.controls.append(ft.ListTile(
                title=ft.Text(folder),
                subtitle=ft.Text(f"{data.get('type', 'mods')} #{mod_id} · файлов: {len(files)}"),
                trailing=trailing,
            ))
        history_count.value = f"Найдено: {shown} из {len(items)}"
        history_list.visible = shown > 0
        history_empty.visible = shown == 0
        history_empty_label.value = ("Ничего не найдено" if items
                                     else "История пуста.\nСкачанные моды появятся здесь")

    history_tab = ft.Column(expand=True, controls=[
        ft.Row(controls=[
            tr(ft.Text(weight=ft.FontWeight.BOLD, size=16), value="history_title"),
            ft.Container(expand=True),
            history_count,
            ft.OutlinedButton(content="Обновить", icon=ft.Icons.REFRESH, on_click=lambda e: refresh_history()),
        ]),
        history_search,
        history_list,
        history_empty,
    ])

    # ============================================================ НАСТРОЙКИ
    vt_badge = ft.Text("", size=12)

    def update_vt_badge():
        on = settings["use_virustotal"] and bool(core.load_vt_key())
        vt_badge.value = "VirusTotal: ВКЛ" if on else "VirusTotal: ВЫКЛ"
        vt_badge.color = ft.Colors.GREEN_600 if on else ft.Colors.RED_600

    def bind_switch(key):
        def h(e):
            settings[key] = bool(e.control.value)
            save_settings()
            update_vt_badge()
        return h

    def on_proxy(e):
        settings["vt_proxy"] = (e.control.value or "").strip()
        save_settings()

    def on_vt_key(e):
        core.save_vt_key(e.control.value or "")
        update_vt_badge()

    def on_speed(e):
        try:
            v = int(e.control.value)
            if 1 <= v <= 10:
                settings["auto_unpack_check_speed"] = v
                save_settings()
        except ValueError:
            pass

    def on_light_change(e):
        S.light = bool(e.control.value)
        settings["gui_light_theme"] = S.light
        save_settings()
        page.theme_mode = ft.ThemeMode.LIGHT if S.light else ft.ThemeMode.DARK
        GLASS["light"] = S.light
        refresh_glass()                                # перекрасить панели «жидкого стекла»
        for c in log_list.controls:                    # перекрасить уже выведенные строки
            c.color = log_color(c.value or "", S.light)
        page.update()

    btn_ru = ft.OutlinedButton(content="Русский (RU)", disabled=(_LANG[0] == "ru"))
    btn_en = ft.OutlinedButton(content="English (EN)", disabled=(_LANG[0] == "en"))

    def set_lang(code):
        _LANG[0] = code
        settings["gui_lang"] = code
        save_settings()
        btn_ru.disabled = (code == "ru")
        btn_en.disabled = (code == "en")
        for ctrl, attr, key in I18N:
            setattr(ctrl, attr, t(key))
        refresh_blocks()
        page.update()
    btn_ru.on_click = lambda e: set_lang("ru")
    btn_en.on_click = lambda e: set_lang("en")

    settings_tab = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=10, controls=[
        tr(ft.Text(weight=ft.FontWeight.BOLD, size=16), value="s_title"),
        tr(ft.Text(weight=ft.FontWeight.BOLD), value="s_lang"),
        ft.Row(controls=[btn_ru, btn_en]),
        tr(ft.Switch(value=S.light, on_change=on_light_change), label="s_light"),
        tr(ft.Switch(value=bool(settings["gui_notify_front"]), on_change=bind_switch("gui_notify_front")),
           label="s_notify"),
        tr(ft.Switch(value=bool(settings["gui_clear_log"]), on_change=bind_switch("gui_clear_log")),
           label="s_clearlog"),
        ft.Divider(),
        tr(ft.Switch(value=settings["use_virustotal"], on_change=bind_switch("use_virustotal")), label="s_vt"),
        ft.TextField(label="API-ключ VirusTotal", value=core.load_vt_key(), password=True,
                     can_reveal_password=True, on_change=on_vt_key),
        ft.TextField(label="Прокси для VirusTotal (например, http://127.0.0.1:10809)",
                     value=settings["vt_proxy"], on_change=on_proxy),
        ft.Text("Бесплатный ключ VirusTotal: 4 запроса в минуту. Ключ берётся на virustotal.com → "
                "профиль → API Key. Если сайт заблокирован — укажи адрес локального VPN/прокси.",
                size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ft.Divider(),
        tr(ft.Switch(value=settings["disable_unpack"], on_change=bind_switch("disable_unpack")), label="s_nounpack"),
        tr(ft.Switch(value=settings["download_preview"], on_change=bind_switch("download_preview")),
           label="s_preview_img"),
        ft.Divider(),
        tr(ft.Text(weight=ft.FontWeight.BOLD), value="s_autounpack"),
        tr(ft.Switch(value=settings["auto_unpack_ask_name"], on_change=bind_switch("auto_unpack_ask_name")),
           label="s_askname"),
        ft.TextField(label="Задержка проверки файла, сек (1–10)", width=260,
                     value=str(settings["auto_unpack_check_speed"]),
                     keyboard_type=ft.KeyboardType.NUMBER, on_change=on_speed),
    ])

    # ============================================================ ДИАЛОГИ (ask)
    def show_next_ask():
        kind, kw, holder = S.pending_asks.popleft()
        S.dialog_open = True

        def finish(value):
            holder.value = value
            holder.event.set()
            S.dialog_open = False
            page.pop_dialog()

        if kind == "confirm":
            dlg = glass_dialog(
                modal=True,
                title=ft.Text(kw.get("title", "Подтверждение")),
                content=ft.Text(kw.get("text", ""), selectable=True),
                actions=[
                    ft.TextButton(content=kw.get("no", "Нет"), on_click=lambda e: finish(False)),
                    ft.FilledButton(content=kw.get("yes", "Да"), on_click=lambda e: finish(True)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        elif kind == "choose_files":
            items = kw.get("items", [])
            cbs = []
            for i, it in enumerate(items):
                label = f"{i + 1}. {it['name']}" + (f"   [{it['extra']}]" if it.get("extra") else "")
                cbs.append(ft.Checkbox(label=label, value=True))

            def selected(e):
                idx = [i for i, c in enumerate(cbs) if c.value]
                finish(idx if idx else "skip")

            dlg = glass_dialog(
                modal=True,
                title=ft.Text(kw.get("title", "Выбор файлов")),
                content=ft.Container(
                    width=560,
                    height=min(420, 100 + 42 * len(cbs)),
                    content=ft.Column(
                        scroll=ft.ScrollMode.AUTO,
                        controls=[ft.Text(f"Найдено файлов/обновлений: {len(items)}. Отметь, что скачать:")] + cbs,
                    ),
                ),
                actions=[
                    ft.TextButton(content="Пропустить мод", on_click=lambda e: finish("skip")),
                    ft.OutlinedButton(content="Скачать выбранные", on_click=selected),
                    ft.FilledButton(content="Скачать все", on_click=lambda e: finish("all")),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        else:  # text
            default = kw.get("default") or ""
            tf = ft.TextField(value=default, autofocus=True, width=420)
            dlg = glass_dialog(
                modal=True,
                title=ft.Text(kw.get("title", "Ввод")),
                content=ft.Column(tight=True, controls=[ft.Text(kw.get("text", "")), tf]),
                actions=[ft.FilledButton(content="OK", on_click=lambda e: finish((tf.value or "").strip() or default))],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        page.show_dialog(dlg)

    def play_done_sound():
        p = resource_path("collect.wav")
        if p and os.path.exists(p) and sys.platform.startswith("win"):
            try:
                import winsound
                winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:
                pass

    # ============================================================ ОБРАБОТКА СООБЩЕНИЙ ИЗ ПОТОКОВ
    def apply_task(tid, f):
        if tid == "total":
            total = f.get("total", 0)
            done = f.get("done", 0)
            S.total = (done, total)
            total_bar.value = (done / total) if total else 0
            total_text.value = f"{f.get('label', 'Модов обработано')}: {done} из {total}"
            return
        card = cards.get(tid)
        if card is None:
            card = TaskCard(tid, cancel_one, open_card_folder, copy_card_folder, pause_one)
            cards[tid] = card
            queue_list.controls.append(card.view)
        card.apply(f)

    def handle(msg):
        kind = msg[0]
        if kind == "log":
            add_log(msg[1])
        elif kind == "task":
            apply_task(msg[1], msg[2])
        elif kind == "ask":
            S.pending_asks.append((msg[1], msg[2], msg[3]))
        elif kind == "links":
            append_to_first(msg[1])
        elif kind == "preview":
            info, err = msg[1]
            if info:
                show_preview(info)
            else:
                toast(t("pv_err").format(e=err))
        elif kind == "job_done":
            if S.queue_mode and core.is_downloading():  # пришло «готово» от прошлой сессии очереди, а новая уже идёт
                return
            S.queue_mode = False
            set_running(False)
            refresh_history()
            refresh_resume_banner()
            # ---------- 4. итог для SnackBar ----------
            cnt = {}
            for c in cards.values():
                cnt[c.state] = cnt.get(c.state, 0) + 1
            if not cards:
                text = "Готово: задач не было"
            elif cnt.get("info") and not (cnt.get("done") or cnt.get("error")):
                text = f"Проверено ссылок: {cnt['info']}. Ничего не скачано"
            else:
                text = (f"Готово: скачано {cnt.get('done', 0)}, пропущено {cnt.get('skipped', 0)}, "
                        f"ошибок {cnt.get('error', 0)}")
                if cnt.get("cancelled"):
                    text += f", отменено {cnt['cancelled']}"
            S.notify = (text, bool(settings.get("gui_notify_front", True)))
            play_done_sound()
        elif kind == "tray_show":
            S.tray_show = True
        elif kind == "tray_quit":
            S.tray_quit = True
        elif kind == "watch_done":
            S.watching = False
            btn_watch.content = "Запустить слежение"
            btn_watch.icon = ft.Icons.PLAY_ARROW
            watch_status.value = "Остановлен"
            watch_status.color = ft.Colors.ON_SURFACE_VARIANT
            refresh_history()

    # ============================================================ ТРЕЙ
    def tray_title():
        done, total = S.total
        if not S.running or not total:
            return "GameBanana: простаивает"
        active = [c.bar.value or 0 for c in cards.values() if c.state == "running"]
        pct = min(100, (done + sum(active)) / total * 100)
        return f"GameBanana: {pct:.0f}% ({done}/{total}) · {core.fmt_speed(current_speed())}"

    def make_tray_image():
        p = resource_path("icon.ico")
        if p:
            try:
                return Image.open(p)
            except Exception:
                pass
        img = Image.new("RGB", (64, 64), "#673AB7")
        ImageDraw.Draw(img).polygon(
            [(32, 52), (12, 30), (25, 30), (25, 10), (39, 10), (39, 30), (52, 30)], fill="white")
        return img

    def start_tray():
        if pystray is None:
            return
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Открыть", lambda icon, item: ui_q.put(("tray_show", None)), default=True),
                pystray.MenuItem("Выход", lambda icon, item: ui_q.put(("tray_quit", None))))
            S.tray = pystray.Icon("gb_downloader", make_tray_image(), "GameBanana", menu)
            S.tray.run_detached()
        except Exception:
            S.tray = None

    def on_window_event(e):
        t = str(getattr(e.type, "value", e.type)).lower()
        if t == "minimize" and S.tray is not None:        # свернули -> прячем в трей
            page.window.visible = False
            page.update()
        elif t == "close" and S.tray is not None:
            try:
                S.tray.stop()
            except Exception:
                pass
    if not IS_ANDROID:
        try:
            page.window.on_event = on_window_event
        except Exception:
            pass

    async def ui_loop():
        while True:
            dirty = False
            for _ in range(300):
                try:
                    msg = ui_q.get_nowait()
                except queue.Empty:
                    break
                dirty = True
                try:
                    handle(msg)
                except Exception:
                    traceback.print_exc()
            if not S.dialog_open and S.pending_asks:
                try:
                    show_next_ask()
                except Exception:
                    traceback.print_exc()
                    S.dialog_open = False
                dirty = True
            if dirty:
                refresh_queue_view()
                page.update()
            if S.notify:                                   # уведомление по завершении job
                text, front = S.notify
                S.notify = None
                try:
                    page.show_dialog(ft.SnackBar(ft.Text(text), duration=6000))
                    page.update()
                    if front:                              # поднять окно (во Flet это может быть async)
                        r = page.window.to_front()
                        if asyncio.iscoroutine(r):
                            await r
                except Exception:
                    pass
            if S.tray is not None:                         # подсказка иконки в трее
                title = tray_title()
                if title != S.tray_title:
                    S.tray_title = title
                    try:
                        S.tray.title = title
                    except Exception:
                        pass
            if S.tray_show:
                S.tray_show = False
                try:
                    page.window.visible = True
                    page.window.minimized = False
                    page.update()
                    r = page.window.to_front()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass
            if S.tray_quit:
                S.tray_quit = False
                try:
                    if S.tray is not None:
                        S.tray.stop()
                    r = page.window.destroy()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass
            await asyncio.sleep(0.12)

    # ============================================================ СБОРКА ОКНА
    def pad(control):
        return glass(content=control, padding=16, radius=24, expand=True)

    update_vt_badge()
    render_history()
    refresh_resume_banner()

    title_text = ft.Text(
        size=22, weight=ft.FontWeight.BOLD,
        spans=[ft.TextSpan(text=ch, style=ft.TextStyle(color=rainbow(i * 0.03))) for i, ch in enumerate(TITLE)])
    signature = ft.Text(size=11, spans=[
        ft.TextSpan(text=TIKTOK_NICK, on_click=lambda e: launch(TIKTOK_URL),
                    style=ft.TextStyle(color=ft.Colors.BLUE_300, decoration=ft.TextDecoration.UNDERLINE)),
        ft.TextSpan(text=SIGNATURE[len(TIKTOK_NICK):], style=ft.TextStyle(color=ft.Colors.with_opacity(0.45, ft.Colors.ON_SURFACE))),
    ])
    key_hint = tr(ft.Text(size=10, opacity=0.5), value="hint_keys")
    header = glass(padding=14, radius=26, content=ft.Row(controls=[
        ft.Icon(ft.Icons.DOWNLOAD_FOR_OFFLINE, size=34, color=ft.Colors.DEEP_PURPLE_200),
        ft.Column(controls=[key_hint, title_text, signature], spacing=0),
        ft.Container(expand=True),
        vt_badge,
    ]))

    async def anim_loop():
        """Переливающийся заголовок, прогресс-бары и «дыхание» фоновых пятен."""
        hue = 0.0
        tick = 0
        while True:
            hue = (hue + 0.01) % 1.0
            tick += 1
            try:
                s, v = (0.85, 0.65) if S.light else (0.6, 1.0)   # на светлой теме цвета темнее
                for i, span in enumerate(title_text.spans):
                    span.style = ft.TextStyle(color=rainbow(hue + i * 0.03, s, v))
                total_bar.color = rainbow(hue, s, v)
                for k, card in enumerate(list(cards.values())):
                    if card.rainbow:
                        card.bar.color = rainbow(hue + k * 0.08, s, v)
                if tick % 5 == 0:                                # медленно плавают цветные пятна
                    for blob, left, top, i in GLASS["blobs"]:
                        blob.left = left + 34 * math.sin(hue * 6.283 + i * 1.7)
                        blob.top = top + 24 * math.cos(hue * 6.283 + i * 2.3)
                page.update()
            except Exception:
                pass
            await asyncio.sleep(0.1)

    tr(btn_inspect, content="btn_inspect", tooltip="tip_inspect")
    tr(btn_stop, content="btn_stop")
    tr(btn_links, content="btn_links")
    tr(btn_updates, content="btn_updates")
    tr(btn_paste, content="btn_paste", tooltip="tip_paste")
    tr(btn_txt, content="btn_txt")
    tr(btn_resume, content="btn_resume")
    tr(btn_verify, content="btn_verify")
    tr(btn_collection, content="btn_collection")
    tr(btn_add_block, content="btn_add_block", tooltip="tip_add_block")
    tr(btn_preview, content="btn_preview")
    tr(collection_field, label="collection_label")
    tr(preview_field, label="preview_field")
    tr(sleep_sw, label="sleep")
    tr(turbo_sw, label="turbo")
    tr(custom_sw, label="custom_dir")

    tabbar = ft.TabBar(
        tabs=[
            tr(ft.Tab(label="", icon=ft.Icons.DOWNLOAD), label="tab_dl"),
            tr(ft.Tab(label="", icon=ft.Icons.LIST_ALT), label="tab_queue"),
            tr(ft.Tab(label="", icon=ft.Icons.TERMINAL), label="tab_log"),
            tr(ft.Tab(label="", icon=ft.Icons.UNARCHIVE), label="tab_watch"),
            tr(ft.Tab(label="", icon=ft.Icons.HISTORY), label="tab_history"),
            tr(ft.Tab(label="", icon=ft.Icons.SETTINGS), label="tab_settings"),
        ],
        indicator_color=tab_indicator(),
        divider_color=ft.Colors.TRANSPARENT,
    )
    GLASS["tabbar"] = tabbar

    tabs = ft.Tabs(
        length=6,
        expand=True,
        content=ft.Column(expand=True, spacing=12, controls=[
            glass(content=tabbar, padding=4, radius=20, elevated=False),
            ft.TabBarView(expand=True, controls=[
                pad(download_tab), pad(queue_tab), pad(log_tab),
                pad(watch_tab), pad(history_tab), pad(settings_tab),
            ]),
        ]),
    )

    start_tray()
    shell = ft.Container(
        left=0, top=0, right=0, bottom=0, padding=16,
        content=ft.Column(expand=True, spacing=14, controls=[header, tabs]),
    )
    page.add(ft.Stack(expand=True, controls=build_background() + [shell]))
    page.run_task(ui_loop)
    page.run_task(anim_loop)
    add_log("[i] Программа запущена. Вставь ссылки или ID на вкладке «Загрузка».")


if __name__ == "__main__":
    ft.run(main)
