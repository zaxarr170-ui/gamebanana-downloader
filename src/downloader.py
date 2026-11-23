# Необязательные зависимости (см. requirements.txt):
#   pip install py7zr rarfile tqdm colorama
import sys
import os

# Принудительно включаем UTF-8 для корректного отображения эмодзи в Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

import subprocess
import re
import time
import shutil
import hashlib
import json
import concurrent.futures
import traceback
import threading
from datetime import datetime
import requests
import urllib3

# Пытаемся импортировать tqdm для красивых многопоточных прогресс-баров
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# Пытаемся импортировать colorama для цветного текста
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False
    class Fore:
        RED = GREEN = YELLOW = CYAN = MAGENTA = WHITE = RESET = ""
    class Style:
        BRIGHT = RESET_ALL = ""

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Папка программы: рядом со скриптом или рядом с .exe (если собрано в exe)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Базовая папка по умолчанию
DEFAULT_SAVE_DIR = os.path.join(BASE_DIR, "downloaded_mods")
AUTO_UNPACK_DIR = os.path.join(BASE_DIR, "auto_unpack")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
LOG_FILE = os.path.join(BASE_DIR, "downloader.log")
VT_KEY_FILE = os.path.join(BASE_DIR, "vt_api_key.txt")
LINKS_FILE = os.path.join(BASE_DIR, "links.txt")

# Блокировка для безопасной записи в JSON из разных потоков
history_lock = threading.Lock()

# Настройки по умолчанию
default_settings = {
    "ask_sleep": True,
    "ask_turbo": True,
    "use_virustotal": True,
    "vt_proxy": "",
    "disable_unpack": False,
    "download_preview": True,
    "auto_unpack_ask_name": True,
    "auto_unpack_check_speed": 2
}

class TeeLogger:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def write(self, message):
        self.terminal.write(message)
        clean_message = self.ansi_escape.sub('', message)
        self.log.write(clean_message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**default_settings, **data}
        except Exception:
            pass
    return default_settings.copy()

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"{Fore.RED}[-] Ошибка сохранения настроек: {e}")

# ================= ИСТОРИЯ (JSON) =================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_history(history):
    with history_lock:
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"{Fore.RED}[-] Ошибка сохранения истории: {e}")

def check_history_url(url, history):
    for mod_id, data in history.items():
        if data.get("url") == url:
            return True
    return False

def add_to_history(mod_id, filename, folder, url, history, mod_type="mods"):
    if mod_id not in history:
        history[mod_id] = {"url": url, "files": [], "type": mod_type, "folder": folder}
    if filename not in history[mod_id]["files"]:
        history[mod_id]["files"].append(filename)
    save_history(history)
# ==================================================

ASCII_ART = r"""
                                                               .     ......                                                                  
                                                                .   ...+=..                                                                  
                                                                   ....=:..                                                                  
                                                                   ...-*:..                                                                  
                                                                   ...=+....                                                                 
                                                                    ..-+...                                                                  
                                                                    ..=+......                                                               
                                                               ..  ...-+.....                                                                
                                                        . ..... .......=:........ . ....    ..                                               
                                                      ..........:----====-::::......... .... ..                                              
                                               .     ......--==++=======-==-==-=--:......                                                    
                                               ..  ....:=++++++++======-=---------==--:.......                                               
                                               .  ..:++*****++++======------------++++++==:.....                                             
                                          .   ....-+*#**++++=========--------------------==++=....                                           
                                             ...:+#***++++++========-=----------------------=--:...  .                                       
                                         .  ...=*****+++-:=========------------------==::----=-:... . ...                                    
                                         . ..=*#*******=::-========-----------------++-::------=:.... ....                                   
                                        ....+###*****#*=:::-======-=---------------=*+-:::------=-...   ..   .                               
                                      ....-*###******#*=-::-=====-==---------------*#+=:::-=---=-=-:..                                       
                                      ...-#####*****##*#%#=-========---------------***#%*-------====:.....                                   
                                      ..-######*****##%@@@%========---------------=##%@@@%==-----====:.. .                                   
                                     ..:*#####******#%@@@@@#=====--=--------:-::--*#%@@@@@+==----=====:...   .                               
                                    ...+%%####******#%@@@@@#========------:::::::-+#@@@@@@#==---=-====-...                                   
                                    ..=#%%####****+*#%@@@@@%+======-------:::::::-+%@@@@@@#==---=======-....                                 
                                    .-*%%%####******#%@@@@@%+======------:::::::::+%@@@@@@#==---========:..                                  
                                   ..=#%%%%###******#%@@@@@#=======-=----:::::::::+%%@@@@@*==----========.....                               
                                  ..-#%%%%%####*****##%@@@@*=========----:::...:::+##@@@@%===---=======+=:..                                 
                                ....=#%%%%%####*****####@%*=========------::::::::-*##%@#==+===========+++....                               
                               ....:*%%%%%%####*****###+==-==========------::::::--*#*+======-=========+++....                               
                                 ..:#%%%%%%%####*****##*====+=========-------------+##+===++-=========++++-...                               
                                ...=#%%%%%%%####******#*+===++=====----------------=*#*+==+===========++++=...                               
                                ...=#%%%%%%%#####*****##++++++====------------------=#*++++==========++++++...                               
                             ......+##%%%%%%%#####*****#*++====---------------------=+***+==========+++++*+...                               
                               ....-*##%%%%%%%#####*++====-----------------------------===========++++++**+...                               
                               ....:*####%%%%%#*+===-----------------------------------------====++++++***:...                               
                                ....-*######*+======-==-------------------------:::::::::::::---=++++****+...                                
                                  ...=*###++=================-=--------------------::::::::::::::-=+*****:..                                 
                                   ..:=*##**+++++=====================================-===========+***#*-..                                  
                                    ..:+**#####*#**************+++++++++++++++*++++*++***************#*-.. .                                 
                                     ..:+**#####%%%###########****************************++++******##=....                                  
                                     ...-****####%%%%%###*++******++++++++++++++++++++++++++++****###+...                                    
                                      ...-****######%%%*=--=********+++++++++++++++++++++++******###*...  .                                  
                                       ...-****######*+--=+*##******+++++++++++++++++++++*******###*... . ..                                 
                                       ....:+*******=--=*######******++++++++++++++++++*******###*-......                                    
                                       ... ..-+**+=--=+#%%%######************##*+++*********####+...  ..                                     
                                          . ..:=+--=+*###%%%%%###%%#######%%##%*+*********#####=...                                          
                                   .....   ..:=**++++**####%%%###%@@@@@@@@@%#%#********######+....                                           
                             .  ... .......:++***==++++**####%##*%@@@@@@@@@%#%#****#########=..  ..                                          
                            .............:+****=...-++++**#######%@@@@@@@@@#%%#***######%#+:....                                             
                         . ...::======-:+***+-......:=+++**###%##%@@@@@@@@%#%#***#######*:... .             .                                
                    .. .....-+++++++++++++=:..........:+++**######@@@@@@@@%#%#***######=...  ..             ..                               
                    ......-+**+++++++++++==-............-+***#####%@@@@@@@##%#***####*-.. ....            . ... ..    . .                    
                    .. ..+****+++++==+++++==---=----:....:+***##%%##*****####****##*-...........   .  .   .   ..             .               
                   ....:+###**+++=======+++==-=****+=.. ...-**###%%%%%%%##*******#*:.....   ....................  .....                      
                . .....+#####*+++========+++===*##*-.......-*###%%##***++++++++***-..............:----:::::::::.............                 
                .....-=*###%##*+++=======++++=---------:....=##%%%##**++++++++++**-.....::----=-=---:::::::::::-===--::::....                
                ...=+=+###%%%#**++=======++++==++++++*+=---:=*##%%##**+++++++++*+=--=--======---:::::::::::::---==--:::::::...   ..          
                ..+++=*###%%%##**++++===+++++==#*+--+**=:--::-=+++**++++==+=====------:--:-::::::::::::::-:----===--::.:::--.......          
              ...+*+===##**%%%##***++++++++++==-----=+==----------------------------------::-:--:---:---------=++==--:::----:......          
              ..-**+=--*#*-=#%%##****+++++++++**++++================-----------------------------------------==*++==---::----:.. ..          
            ....=*+==--=##+--+#%%##*****+++++*###**+===============------------------------------------------==**++=----::--=:..             
             ..-**+=---*%##*=--+###****++++++======================------------------------------------------==**++==--::::---...            
            ...+*+==--+#%##***++*##*******+==+================-===-=-----------------------------------------==***+==----:---=:..   ..       
            ..-*+===-=#%##***++*###******+++=+=====================------------------------------------------==##*+===--:::---:..            
           ...**+==-=*%###**++*######*+++====+=====================------------------------------------------==*#*++==----:--=....          
         ....-**+==-*%%##**++++***+================================------------------------------------------==+#**+==----:----:...          
        .....**+==-+%%##***+++****++++=============================------------------------------------------===##*++==---:---==..           
        ....=**====####***++++**#*+================================------------------------------------------===##*++==---:-----.....        
          ..+*+===#%###***++++**#*+================================------------------------------------------===##**+===---:---==..          
        ...=#++==*%###***+++***###+================================------------------------------------------===+##*++==---:-----...         
        ...+*+==*%%##***++++**####+++==============================------------------------------------------===+##*++==---::---=-..         
    ..  ..=**==+%%##***++++***###*+================================------------------------------------------===+###*+===---::----..         
    . ...:+*+=+#%###***+++***####*+=++=============================------------------------------------------====*##*++==----:----:......    
.    ....-**==#%###***+++****####*+==========+=====================------------------------------------------====+##*++==----:---=:..    ..  
       ..+*++#%%##****+++***#####*+================================------------------------------------------====+###*++==----:---:...       
       .:**+*%%###***+++****#%###*+================================-=----------------------------------------=====*##*++==---:----=:.......  
    . ..+*+*#%###****++***++*####*+================================-=----------------------------------------=====+##**+===-------=:.. ..    
   . ..:***#%###****++****--*####*+================================------------------------------------------====--+##*++==---::----.....   .
     ..+**#%###****+++***+.-*####*+==========+=====================------------------------------------------====-:=##*++==---:---=:..      
    ..:***%%###***+++***+:.-*####*+================================------------------------------------------=====::*#**+==----::---:.....   
  . ..+**%%###****++****+..-*####*+==========+====================-------------------------------------------====-:.+##*++==----:--==...  .. 
   ..-**#%%##****+++***+:..-*####*+==========+=====================------------------------------------------=====:.=##*++==----:----...     
  ...=##%####***++++***-...-*####*+================================------------------------------------------=====:.:*##*++==---:-----..     
  ..-*#%%###***++++***=....-*####*+===========+====================------------------------------------------=====:..+##*++==----:--=-.....  
....=#%%####**+*++***+:....-*####*+==========+=========================+++++++++++++++++++++++++++++++++++++-----------=====:..:##**+==---------:.... .
...:*%%####***++++***-.....-*####*+======================++++++++++++++++++++++++++++++++++++++++++++++=-----=====:..:*##*++==---:---=-....  
...-#%%###****++****=......-*####*+=====================+++++++++++++++++++++++++++++++++++++++++++++++++----=====:...=##*++==----:----...   
..:*%%###****+++***+:... ..-*####*+=====================++++++++++++++++++++++++++++++++++++++++++++++++++---=====:...-##**+===---:----:...  
..-#%###****++++***+.......-*####*+=====================+++++++++++++++++++++++++++++++++++++++++++++++++=---=====:....=##*++==----:---:...  
..*%%###****++****+...   ..-*####*+=====================+++++++++++++++++++++++++++++++++++++++++++++++++----=====:....=##*++==---------...  
..*####****++*****:...   ..-*####*+=====================++++++++++++++++++++++++++++++++++++++++++++++++++---=====:....:*##*+==-----:--=:.. .
..=####***++*+***=.........-*####*+=====================+++++++++====----------------------------------------=====:.. ..=##*++==--------:... 
...=*###********+...    ...-*#####+=====================+++++++++====----------------------------------------=====:.. ..=##**+==---:--=++:.. 
......................   .................................................................................................................. .
by ziler (TT) и Gemini
"""

def check_virustotal(file_path, api_key, proxy_url=""):
    if not api_key:
        return "no_key", []
        
    print(f"{Fore.CYAN}   [*] Вычисляем хэш и проверяем через VirusTotal...")
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        file_hash = sha256.hexdigest()
    except Exception as e:
        return "error", [f"Ошибка чтения файла: {e}"]

    url = f"https://www.virustotal.com/api/v3/files/{file_hash}"
    headers = {"x-apikey": api_key, "Accept": "application/json"}
    
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
    
    try:
        res = requests.get(url, headers=headers, timeout=15, proxies=proxies)
        
        if res.status_code == 200:
            data = res.json()
            stats = data.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
            malicious = stats.get("malicious", 0)
            
            if malicious > 0:
                results = data.get("data", {}).get("attributes", {}).get("last_analysis_results", {})
                threats = [f"{v['engine_name']} ({v['result']})" for v in results.values() if v.get('category') == 'malicious']
                return "malicious", threats
            return "clean", []
            
        elif res.status_code == 404:
            return "unknown", []
        elif res.status_code == 429:
            return "rate_limit", ["Превышен лимит запросов (4 в минуту для бесплатного API)."]
        else:
            return "error", [f"Код ответа API: {res.status_code}"]
            
    except Exception as e:
        return "error", [f"Ошибка сети: {e}"]

def download_preview_image(data, save_path):
    preview_url = ""
    
    if "_aPreviewMedia" in data and isinstance(data["_aPreviewMedia"], dict):
        media = data["_aPreviewMedia"]
        if "_aImages" in media and isinstance(media["_aImages"], list) and media["_aImages"]:
            img = media["_aImages"][0]
            base = img.get("_sBaseUrl", "")
            file = img.get("_sFile", "")
            if base and file:
                if not base.endswith("/"): base += "/"
                if file.startswith("/"): file = file[1:]
                preview_url = base + file
                
    if not preview_url and "_aImages" in data and isinstance(data["_aImages"], list) and data["_aImages"]:
        img = data["_aImages"][0]
        base = img.get("_sBaseUrl", "")
        file = img.get("_sFile", "")
        if base and file:
            if not base.endswith("/"): base += "/"
            if file.startswith("/"): file = file[1:]
            preview_url = base + file

    if not preview_url and "_sImageUrl" in data and isinstance(data["_sImageUrl"], str):
        preview_url = data["_sImageUrl"]

    if not preview_url:
        def recursive_find(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, str) and v.startswith("http") and any(x in k.lower() for x in ["image", "preview", "thumb", "screenshot"]):
                        if v.lower().split('?')[0].endswith((".jpg", ".png", ".jpeg", ".webp")):
                            return v
                    res = recursive_find(v)
                    if res:
                        return res
            elif isinstance(obj, list):
                for item in obj:
                    res = recursive_find(item)
                    if res:
                        return res
            return None
        preview_url = recursive_find(data)

    if preview_url:
        parts = preview_url.split("://")
        if len(parts) == 2:
            preview_url = f"{parts[0]}://{re.sub(r'/+', '/', parts[1])}"

    if preview_url:
        img_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://gamebanana.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Connection": "keep-alive"
        }
        
        for attempt in range(3):
            try:
                img_res = requests.get(preview_url, headers=img_headers, timeout=15, verify=False)
                
                if img_res.status_code == 200:
                    ext = "jpg"
                    if preview_url.lower().endswith(".png") or "image/png" in img_res.headers.get("Content-Type", ""):
                        ext = "png"
                    elif preview_url.lower().endswith(".webp") or "image/webp" in img_res.headers.get("Content-Type", ""):
                        ext = "webp"
                    
                    with open(os.path.join(save_path, f"preview.{ext}"), "wb") as f:
                        f.write(img_res.content)
                    return True, ext
                else:
                    err_msg = f"Сервер вернул код {img_res.status_code}"
            except Exception as e:
                err_msg = str(e)
                time.sleep(1)
                
        return False, f"Не удалось скачать ({preview_url}): {err_msg}"
            
    return False, "Ссылка на превью не найдена в API."

def save_mod_info(data, mod_id, url_type, save_path, headers):
    """Создает txt файл с описанием и обновлениями мода"""
    try:
        mod_name = data.get("_sName", "Неизвестный мод")
        desc = data.get("_sText", "Описание отсутствует.")

        def clean_html(raw_html):
            if not raw_html: return ""
            text = re.sub(r'<br\s*/?>', '\n', str(raw_html))
            text = re.sub(r'<[^>]+>', '', text)
            text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#039;', "'")
            text = re.sub(r'\n{3,}', '\n\n', text)
            return text.strip()

        desc_clean = clean_html(desc)
        content = f"=== {mod_name} ===\n\n"
        content += f"--- ОПИСАНИЕ ---\n{desc_clean}\n\n"
        content += f"--- ОБНОВЛЕНИЯ ---\n"

        # === ФИКС: Делаем отдельный запрос за обновлениями ===
        api_model = "Wip" if url_type == "wips" else "Mod"
        updates_url = f"https://gamebanana.com/apiv11/{api_model}/{mod_id}/Updates?_nPerpage=50"
        
        updates = []
        try:
            u_res = requests.get(updates_url, headers=headers, timeout=10, verify=False)
            if u_res.status_code == 200:
                u_data = u_res.json()
                updates = u_data.get("_aRecords", [])
        except Exception:
            pass
        # ===================================================

        if not updates:
            content += "Обновлений пока нет.\n"
        else:
            # Проходим по обновлениям в обратном порядке (новые сверху)
            for upd in reversed(updates):
                date = upd.get("_tsDateAdded", 0)
                dt_str = datetime.fromtimestamp(date).strftime("%Y-%m-%d %H:%M") if date else "Дата неизвестна"
                
                title = upd.get("_sTitle", "Без названия")
                text = upd.get("_sText", "")
                text_clean = clean_html(text)
                
                content += f"\n[{dt_str}] {title}\n{text_clean}\n{'-' * 50}\n"

        file_path = os.path.join(save_path, "Описание + обновления.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"{Fore.RED}   [-] Ошибка при создании txt файла с описанием: {e}")
        return False

# ================= РАСПАКОВКА (V6.5: кроссплатформенная) =================
# Порядок: внешние архиваторы (7z/7za/7zz, unrar, unar; на Windows ещё 7-Zip и WinRAR
# из стандартных папок) -> py7zr для .7z -> rarfile для .rar -> zipfile (финальный fallback).
# Необязательные зависимости: pip install py7zr rarfile   (см. requirements.txt)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # не показывать чёрное окно в exe без консоли


def _find_archivers():
    """Возвращает [(тип, путь)] найденных внешних архиваторов в порядке приоритета."""
    found, seen = [], set()

    def add(kind, path):
        if path and os.path.exists(path) and os.path.normcase(path) not in seen:
            seen.add(os.path.normcase(path))
            found.append((kind, path))

    for name in ("7z", "7za", "7zz"):
        add("7z", shutil.which(name))
    add("unrar", shutil.which("unrar"))
    add("unar", shutil.which("unar"))
    if os.name == "nt":   # старые пути Windows (если архиватор не добавлен в PATH)
        for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
            add("7z", p)
        for p in (r"C:\Program Files\WinRAR\WinRAR.exe", r"C:\Program Files (x86)\WinRAR\WinRAR.exe"):
            add("winrar", p)
    return found


def _archive_kind(path):
    """Определяет формат по первым байтам файла (расширение у модов часто врёт)."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return "unknown"
    if head.startswith(b"PK"):
        return "zip"
    if head.startswith(b"Rar!"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    return "unknown"


def _clear_dir(path):
    """Очищает временную папку между попытками."""
    for item in os.listdir(path):
        full = os.path.join(path, item)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass


def _extract_with_tool(kind, tool, archive_path, out_dir):
    if kind == "7z":
        cmd = [tool, "x", archive_path, f"-o{out_dir}", "-y"]
    elif kind == "unrar":
        cmd = [tool, "x", "-y", "-idq", archive_path, out_dir + os.sep]
    elif kind == "winrar":
        cmd = [tool, "x", "-y", archive_path, out_dir + os.sep]
    else:  # unar
        cmd = [tool, "-q", "-f", "-o", out_dir, archive_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WINDOW)
    return result.returncode == 0


def smart_extract(archive_path, extract_path, file_index=1):
    temp_extract_path = os.path.join(extract_path, f"_temp_unpack_{file_index}_")
    if os.path.exists(temp_extract_path):
        shutil.rmtree(temp_extract_path)
    os.makedirs(temp_extract_path, exist_ok=True)

    success = False
    problems = []
    kind = _archive_kind(archive_path)

    # 1) внешние архиваторы
    for tool_kind, tool in _find_archivers():
        try:
            if _extract_with_tool(tool_kind, tool, archive_path, temp_extract_path):
                success = True
                break
        except Exception as e:
            problems.append(f"{os.path.basename(tool)}: {e}")
        _clear_dir(temp_extract_path)

    # 2) py7zr для .7z
    if not success and kind == "7z":
        try:
            import py7zr
            with py7zr.SevenZipFile(archive_path, "r") as z:
                z.extractall(temp_extract_path)
            success = True
        except ImportError:
            problems.append("для .7z нужен 7-Zip или пакет py7zr (pip install py7zr)")
        except Exception as e:
            problems.append(f"py7zr: {e}")
            _clear_dir(temp_extract_path)

    # 3) rarfile для .rar (ему самому нужен unrar/unar/bsdtar в системе)
    if not success and kind == "rar":
        try:
            import rarfile
            with rarfile.RarFile(archive_path) as rf:
                rf.extractall(temp_extract_path)
            success = True
        except ImportError:
            problems.append("для .rar нужен unrar/WinRAR или пакет rarfile (pip install rarfile)")
        except Exception as e:
            problems.append(f"rarfile: {e} (rarfile требует установленный unrar или unar)")
            _clear_dir(temp_extract_path)

    # 4) zipfile — финальный fallback
    if not success:
        import zipfile
        try:
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extractall(temp_extract_path)
                success = True
        except Exception as e:
            problems.append(f"zipfile: {e}")
            _clear_dir(temp_extract_path)

    if success:
        items = os.listdir(temp_extract_path)
        if len(items) == 1 and os.path.isdir(os.path.join(temp_extract_path, items[0])):
            inner_folder = os.path.join(temp_extract_path, items[0])
            print(f"{Fore.CYAN}   [i] Обнаружена вложенная папка '{items[0]}'. Вытаскиваем файлы на уровень выше...")
            for item in os.listdir(inner_folder):
                shutil.move(os.path.join(inner_folder, item), extract_path)
        else:
            for item in items:
                shutil.move(os.path.join(temp_extract_path, item), extract_path)

        shutil.rmtree(temp_extract_path)
        return True
    else:
        if os.path.exists(temp_extract_path):
            shutil.rmtree(temp_extract_path)
        for p in problems:
            print(f"{Fore.RED}   [-] {p}")
        if not problems and kind in ("7z", "rar"):
            print(f"{Fore.RED}   [-] Архиватор не найден. Установите 7-Zip (7z/7za/7zz), unrar или unar "
                  f"либо: pip install py7zr rarfile")
        return False

# ================= ПРОДОЛЖЕНИЕ ЗАГРУЗКИ ПРИ ОБРЫВЕ (файл "check") =================
# В папке каждого мода во время скачивания лежит файл "check" (JSON).
# Он обновляется каждые 0.5 сек и хранит: ссылку на мод, ссылки на файлы и
# сколько байт уже скачано. Если загрузка оборвалась (свет, интернет, Ctrl+C),
# по этому файлу можно докачать: меню (8) или кнопка «Продолжить» в GUI.
# Когда всё скачано и проверено, файл "check" удаляется автоматически.
CHECK_FILE_NAME = "check"
CHECK_INTERVAL = 0.5
CHUNK_SIZE = 256 * 1024

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://gamebanana.com/"
}


def _now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def console_log(msg):
    """Цветной вывод в консоль по тегу в начале строки ([+], [-], [!], [i], [*])."""
    s = str(msg).lstrip()
    color = ""
    if s.startswith("[-]"):
        color = Fore.RED
    elif s.startswith("[+]"):
        color = Fore.GREEN
    elif s.startswith("[!]"):
        color = Fore.YELLOW
    elif s.startswith("[i]") or s.startswith("[*]"):
        color = Fore.CYAN
    print(f"{color}   {s}")


def console_confirm(title, text, yes="Да", no="Нет"):
    print(f"\n{Fore.YELLOW}   {title}")
    for line in str(text).splitlines():
        print(f"{Fore.WHITE}   {line}")
    ans = input(f"{Fore.YELLOW}   > {yes} (y) / {no} (n): {Fore.WHITE}").strip().lower()
    return ans == "y"


class CheckFile:
    """Файл 'check' в папке мода. Пишется каждые CHECK_INTERVAL секунд."""

    def __init__(self, mod_path, url, mod_id, url_type, mod_name, is_single, data=None):
        self.mod_path = mod_path
        self.path = os.path.join(mod_path, CHECK_FILE_NAME)
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.thread = None
        self.dirty = True
        self.data = data or {
            "version": 1, "url": url, "mod_id": str(mod_id), "type": url_type,
            "mod_name": mod_name, "is_single": bool(is_single),
            "created": _now_iso(), "updated": _now_iso(), "files": {}
        }

    @classmethod
    def from_file(cls, path):
        data = load_check(path)
        if not data:
            return None
        return cls(os.path.dirname(path), data.get("url", ""), data.get("mod_id", ""),
                   data.get("type", "mods"), data.get("mod_name", ""), data.get("is_single", True), data=data)

    def register(self, name, download_url, archive_path, extract_path, expected_size=0, md5="", index=1):
        with self.lock:
            self.data["files"][name] = {
                "index": index,
                "download_url": download_url,
                "rel_path": os.path.relpath(archive_path, self.mod_path),
                "rel_extract": os.path.relpath(extract_path, self.mod_path),
                "expected_size": int(expected_size or 0),
                "md5": md5 or "",
                "downloaded": 0,
                "total": int(expected_size or 0),
                "status": "queued",   # queued / downloading / downloaded / paused / failed / done
            }
            self.dirty = True

    def update(self, name, **kw):
        with self.lock:
            f = self.data["files"].get(name)
            if f is not None:
                f.update(kw)
                self.dirty = True

    def start(self):
        self.flush(force=True)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_evt.wait(CHECK_INTERVAL):
            self.flush()

    def flush(self, force=False):
        with self.lock:
            if not (self.dirty or force):
                return
            self.data["updated"] = _now_iso()
            snapshot = json.dumps(self.data, ensure_ascii=False, indent=2)
            self.dirty = False
        try:
            os.makedirs(self.mod_path, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(snapshot)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def close(self):
        """Останавливает запись. Если всё готово — удаляет файл check. Возвращает True, если удалён."""
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout=2)
        with self.lock:
            all_done = all(f.get("status") == "done" for f in self.data["files"].values())
        if all_done:
            for p in (self.path, self.path + ".tmp"):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
            return True
        self.flush(force=True)
        return False


def load_check(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except Exception:
        pass
    return None


def find_check_files(save_dir):
    """Ищет файлы 'check' в папках модов внутри save_dir."""
    found = []
    if not save_dir or not os.path.isdir(save_dir):
        return found
    try:
        for entry in os.scandir(save_dir):
            if entry.is_dir():
                p = os.path.join(entry.path, CHECK_FILE_NAME)
                if os.path.isfile(p):
                    data = load_check(p)
                    if data:
                        found.append((p, data))
    except OSError:
        pass
    return found


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_archive(path, expected_size=0, md5="", check_md5=True):
    """
    Сверяет файл на диске с данными GameBanana.
    Возвращает (статус, описание): ok / incomplete / corrupt / missing.
    """
    if not os.path.exists(path):
        return "missing", "Файл не найден"
    size = os.path.getsize(path)
    if expected_size:
        if size < expected_size:
            return "incomplete", f"Недокачан: {size} из {expected_size} байт"
        if size > expected_size:
            return "corrupt", f"Размер больше ожидаемого ({size} > {expected_size} байт)"
    if md5 and check_md5:
        if md5_file(path).lower() != md5.lower():
            return "corrupt", "Контрольная сумма MD5 не совпала с данными GameBanana"
        return "ok", "Целостность подтверждена (размер + MD5)"
    if expected_size:
        return "ok", "Размер совпадает с GameBanana"
    return "ok", "Сверить не с чем (GameBanana не дал размер/MD5), файл на месте"


def fetch_mod_data(url, headers=None):
    """Данные страницы мода из API GameBanana. Возвращает (data, ошибка)."""
    m = re.search(r'/(mods|wips)/(\d+)', str(url))
    if not m:
        return None, "неверная ссылка"
    api_model = "Wip" if m.group(1) == "wips" else "Mod"
    try:
        res = requests.get(f"https://gamebanana.com/apiv11/{api_model}/{m.group(2)}/ProfilePage",
                           headers=headers or API_HEADERS, timeout=10, verify=False)
        if res.status_code != 200:
            return None, f"код {res.status_code}"
        return res.json(), ""
    except Exception as e:
        return None, str(e)


def _wait_pause(pause_event, should_cancel):
    # Блокирует поток, пока включена пауза (Event установлен). True - отмена во время паузы.
    while pause_event is not None and pause_event.is_set():
        if should_cancel and should_cancel():
            return True
        time.sleep(0.2)
    return False


def stream_download(download_url, archive_path, headers, expected_size=0,
                    on_progress=None, should_cancel=None, log=None, max_fail=5, pause_event=None):
    """
    Скачивает файл с докачкой (HTTP Range): если файл уже частично лежит на диске
    или соединение оборвалось, продолжает с того же байта.
    Возвращает 'ok' | 'failed' | 'cancelled'. При отмене недокачанный файл остаётся на диске.
    """
    log = log or (lambda m: None)
    os.makedirs(os.path.dirname(archive_path) or ".", exist_ok=True)
    fails = 0
    loops = 0
    while fails < max_fail and loops < 500:
        loops += 1
        if should_cancel and should_cancel():
            return "cancelled"
        if _wait_pause(pause_event, should_cancel):
            return "cancelled"
        existing = os.path.getsize(archive_path) if os.path.exists(archive_path) else 0
        if expected_size and existing > expected_size:
            os.remove(archive_path)
            existing = 0
        if expected_size and existing == expected_size:
            if on_progress:
                on_progress(existing, expected_size)
            return "ok"

        hdr = dict(headers)
        hdr["Accept-Encoding"] = "identity"
        if existing > 0:
            hdr["Range"] = f"bytes={existing}-"
            log(f"[*] Докачиваем с {existing / 1048576:.1f} МБ...")

        try:
            response = requests.get(download_url, headers=hdr, stream=True, timeout=30, verify=False)
        except Exception as e:
            fails += 1
            log(f"[-] Ошибка сети ({e}). Повтор {fails}/{max_fail}...")
            time.sleep(1.5)
            continue

        code = response.status_code
        if code == 416:   # сервер говорит, что дальше качать нечего
            response.close()
            if existing > 0 and not expected_size:
                return "ok"
            if os.path.exists(archive_path):
                os.remove(archive_path)
            fails += 1
            continue
        if code not in (200, 206):
            response.close()
            fails += 1
            log(f"[-] Сервер вернул код {code}. Повтор {fails}/{max_fail}...")
            time.sleep(1.5)
            continue

        if code == 206:
            mode, offset = "ab", existing
        else:                      # сервер не поддерживает докачку — начинаем сначала
            mode, offset = "wb", 0
        clen = int(response.headers.get("content-length", 0) or 0)
        total = (offset + clen) if clen else expected_size
        got = offset
        progressed = False
        broken = False
        try:
            with open(archive_path, mode) as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if should_cancel and should_cancel():
                        response.close()
                        return "cancelled"
                    if pause_event is not None and pause_event.is_set():   # ПАУЗА: байты уже на диске, чанк ещё в руках
                        f.flush()
                        if _wait_pause(pause_event, should_cancel):
                            response.close()
                            return "cancelled"
                    if chunk:
                        f.write(chunk)
                        got += len(chunk)
                        progressed = True
                        if on_progress:
                            on_progress(got, total)
        except Exception as e:
            broken = True
            log(f"[!] Соединение оборвалось: {e}")
        finally:
            response.close()

        size_now = os.path.getsize(archive_path) if os.path.exists(archive_path) else 0
        if not broken and (not total or size_now >= total):
            return "ok"
        fails = 0 if progressed else fails + 1
        log(f"[!] Загрузка прервалась на {size_now / 1048576:.1f} МБ, пробуем продолжить...")
        time.sleep(1)
    return "failed"


def vt_gate(archive_path, name, vt_key, vt_proxy, log=console_log, confirm=console_confirm, interactive=True):
    """Проверка через VirusTotal. True — можно продолжать, False — архив удалён."""
    status, info = check_virustotal(archive_path, vt_key, vt_proxy)
    if status == "malicious":
        log("[!] ВНИМАНИЕ! VIRUSTOTAL ОБНАРУЖИЛ УГРОЗЫ:")
        for t in info[:5]:
            log(f"[!]     - {t}")
        proceed = False
        if interactive:
            proceed = bool(confirm(
                "VirusTotal обнаружил угрозу",
                f"Файл: {name}\n" + "\n".join(f"• {t}" for t in info[:5]) +
                "\nПодтвердить, что архив безопасен, нельзя. Всё равно продолжить распаковку?",
                "Всё равно продолжить", "Удалить и пропустить"))
        else:
            log("[i] [Режим сна] Угроза найдена, архив удаляется автоматически.")
        if not proceed:
            try:
                os.remove(archive_path)
            except OSError:
                pass
            log("[i] Архив удалён, мод пропущен.")
            return False
    elif status == "clean":
        log("[+] VirusTotal: файл чистый (0 угроз).")
    elif status == "unknown":
        log("[i] VirusTotal: файл неизвестен базе (новый или малоизвестный).")
    elif status == "rate_limit":
        log("[-] VirusTotal: превышен лимит запросов (4 в минуту). Проверка пропущена.")
    elif status != "no_key":
        msg = info[0] if info else "Неизвестно"
        log(f"[-] VirusTotal: ошибка проверки ({msg}).")
        if not vt_proxy and "timed out" in str(msg).lower():
            log("[i] Совет: возможно, VirusTotal заблокирован. Укажите прокси в настройках.")
    return True


def finalize_archive(archive_path, extract_path, file_index, name, mod_id, url, main_mod_path,
                     history, settings, vt_key, log=console_log, confirm=console_confirm, interactive=True):
    """VirusTotal -> распаковка -> история. Возвращает 'ok' или 'skipped' (удалён по VirusTotal)."""
    if settings.get("use_virustotal") and vt_key:
        if not vt_gate(archive_path, name, vt_key, settings.get("vt_proxy", ""), log, confirm, interactive):
            return "skipped"
    mod_type = "wips" if "/wips/" in str(url) else "mods"
    folder = os.path.basename(main_mod_path)
    if settings.get("disable_unpack"):
        log("[i] Распаковка отключена в настройках. Архив оставлен как есть.")
        add_to_history(mod_id, name, folder, url, history, mod_type)
        return "ok"
    log("[*] Распаковываем архив...")
    try:
        success = smart_extract(archive_path, extract_path, file_index)
    except Exception as e:
        log(f"[-] Ошибка распаковки: {e}")
        success = False
    if success:
        try:
            os.remove(archive_path)
        except OSError:
            pass
        log("[+] Архив успешно распакован и удалён!")
    else:
        log("[-] Не удалось распаковать автоматически. Архив сохранён в папке.")
    add_to_history(mod_id, name, folder, url, history, mod_type)
    return "ok"


def resume_from_check(check_path, settings, vt_key, history, log=console_log, confirm=console_confirm,
                      on_progress=None, should_cancel=None, deep=False, interactive=True, pause_event=None):
    """
    Докачивает и проверяет всё, что записано в файле check.
    deep=True: MD5 проверяется всегда (иначе только если размер уже совпал).
    Возвращает (состояние, текст): done / partial / error / cancelled.
    """
    cf = CheckFile.from_file(check_path)
    if cf is None:
        log("[-] Файл check повреждён или не читается.")
        return "error", "Файл check повреждён"
    mod_path = cf.mod_path
    url = cf.data.get("url", "")
    mod_id = cf.data.get("mod_id", "")
    log(f"[*] Продолжаем: {cf.data.get('mod_name') or url}")

    api, err = fetch_mod_data(url)
    api_files = {}
    if api:
        api_files = {f.get("_sFile"): f for f in api.get("_aFiles", [])}
    else:
        log(f"[!] GameBanana недоступен ({err}), работаю по данным из файла check.")

    cf.start()
    any_fail = False
    cancelled = False
    try:
        for name, f in list(cf.data["files"].items()):
            if f.get("status") == "done":
                continue
            if should_cancel and should_cancel():
                cancelled = True
                break
            info = api_files.get(name, {})
            dl_url = info.get("_sDownloadUrl") or f.get("download_url")
            expected = int(info.get("_nFilesize") or f.get("expected_size") or 0)
            md5 = info.get("_sMd5Checksum") or f.get("md5") or ""
            archive = os.path.join(mod_path, f["rel_path"])
            extract_path = os.path.join(mod_path, f["rel_extract"])
            cf.update(name, download_url=dl_url, expected_size=expected, md5=md5, total=expected)

            status, msg = verify_archive(archive, expected, md5, check_md5=deep or not expected or
                                         (os.path.exists(archive) and os.path.getsize(archive) == expected))
            if status == "ok":
                log(f"[+] {name}: {msg}")
            else:
                if status == "corrupt":
                    log(f"[!] {name}: {msg}. Файл будет скачан заново.")
                    try:
                        os.remove(archive)
                    except OSError:
                        pass
                elif status == "incomplete":
                    log(f"[i] {name}: {msg}. Докачиваем.")
                if not dl_url:
                    log(f"[-] {name}: нет ссылки для скачивания.")
                    cf.update(name, status="failed")
                    any_fail = True
                    continue
                cf.update(name, status="downloading")

                def cb(done, total, _n=name):
                    cf.update(_n, downloaded=done, total=total)
                    if on_progress:
                        on_progress(_n, done, total)

                res = stream_download(dl_url, archive, API_HEADERS, expected, cb, should_cancel, log, pause_event=pause_event)
                if res == "cancelled":
                    cf.update(name, status="paused")
                    cancelled = True
                    break
                if res == "failed":
                    log(f"[-] {name}: не удалось докачать. Файл check сохранён, можно повторить позже.")
                    cf.update(name, status="failed")
                    any_fail = True
                    continue
                status, msg = verify_archive(archive, expected, md5)
                if status != "ok":
                    log(f"[-] {name}: {msg}")
                    if status == "corrupt":
                        try:
                            os.remove(archive)
                        except OSError:
                            pass
                    cf.update(name, status="failed")
                    any_fail = True
                    continue
                log(f"[+] {name}: докачан. {msg}")

            cf.update(name, status="downloaded")
            finalize_archive(archive, extract_path, f.get("index", 1), name, mod_id, url, mod_path,
                             history, settings, vt_key, log, confirm, interactive)
            cf.update(name, status="done")
    finally:
        removed = cf.close()
    if cancelled:
        return "cancelled", "Остановлено"
    if removed:
        log("[+] Всё в порядке, файл check удалён.")
        return "done", "Готово"
    return ("partial" if any_fail else "error"), "Часть файлов не докачана"


def verify_history_archives(save_dir, history, log=console_log):
    """Проверяет архивы, оставшиеся на диске (если распаковка отключена), по данным GameBanana."""
    checked = 0
    for mod_id, data in history.items():
        if not str(mod_id).isdigit():
            continue
        folder = os.path.join(save_dir, data.get("folder", ""))
        if not os.path.isdir(folder):
            continue
        api = None
        for name in data.get("files", []):
            candidates = [os.path.join(folder, name)]
            try:
                candidates += [os.path.join(folder, d, name) for d in os.listdir(folder)
                               if os.path.isdir(os.path.join(folder, d))]
            except OSError:
                pass
            path = next((c for c in candidates if os.path.isfile(c)), None)
            if not path:
                continue
            if api is None:
                api, err = fetch_mod_data(data.get("url", ""))
                api = api or {}
            info = next((x for x in api.get("_aFiles", []) if x.get("_sFile") == name), {})
            status, msg = verify_archive(path, int(info.get("_nFilesize") or 0), info.get("_sMd5Checksum") or "")
            checked += 1
            tag = "[+]" if status == "ok" else "[-]"
            log(f"{tag} {os.path.basename(folder)} / {name}: {msg}")
    if not checked:
        log("[i] Сохранённых архивов для проверки нет (они распакованы и удалены).")
    return checked


def console_resume_all(save_dir, settings, vt_key, history, deep=False):
    checks = find_check_files(save_dir)
    if not checks:
        print(f"{Fore.CYAN}   [i] Прерванных загрузок не найдено в: {save_dir}")
    for i, (path, data) in enumerate(checks, 1):
        print(f"\n{Fore.MAGENTA}[{i}/{len(checks)}]{Fore.WHITE} {data.get('mod_name') or data.get('url')}")
        last = {"t": 0}

        def prog(name, done, total):
            if time.time() - last["t"] < 0.3:
                return
            last["t"] = time.time()
            pct = f" ({int(done * 100 / total)}%)" if total else ""
            sys.stdout.write(f"\r{Fore.CYAN}   -> {name[:30]}: {done / 1048576:.1f} МБ{pct}      ")
            sys.stdout.flush()
        try:
            state, text = resume_from_check(path, settings, vt_key, history, on_progress=prog, deep=deep)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}   [!] Прервано. Файл check сохранён.")
            return
        print()
    if deep:
        print(f"\n{Fore.CYAN}   [*] Проверяем сохранённые архивы по данным GameBanana...")
        verify_history_archives(save_dir, history)


def download_single_file(mod_id, target_file, file_index, main_mod_path, is_single_choice, save_dir, headers, history, url, turbo_mode, vt_api_key, vt_proxy, use_vt, disable_unpack, check=None):
    download_url = target_file.get("_sDownloadUrl")
    original_filename = target_file.get("_sFile", f"file_{mod_id}.zip")

    if not download_url:
        if not turbo_mode:
            print(f"{Fore.RED}   [-] У файла {original_filename} нет прямой ссылки, пропускаем.")
        return

    if is_single_choice:
        extract_path = main_mod_path
    else:
        sub_folder_name = f"{file_index} - {os.path.splitext(original_filename)[0]}"
        extract_path = os.path.join(main_mod_path, sub_folder_name)
        os.makedirs(extract_path, exist_ok=True)

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', os.path.basename(original_filename)) or f"file_{mod_id}.zip"
    archive_path = os.path.join(extract_path, safe_name)
    expected = int(target_file.get("_nFilesize") or 0)
    md5 = target_file.get("_sMd5Checksum") or ""

    if check:
        check.register(original_filename, download_url, archive_path, extract_path, expected, md5, file_index)
        check.update(original_filename, status="downloading")

    if not turbo_mode and not HAS_TQDM:
        print(f"\n{Fore.CYAN}   [*] Скачиваем: {original_filename}")

    short_name = (original_filename[:20] + '..') if len(original_filename) > 20 else original_filename
    state = {"pbar": None, "last": 0, "t0": time.time()}

    def on_progress(done, total):
        if check:
            check.update(original_filename, downloaded=done, total=total)
        if turbo_mode and HAS_TQDM:
            if state["pbar"] is None:
                state["pbar"] = tqdm(total=total or None, unit='B', unit_scale=True, desc=short_name, leave=False)
            state["pbar"].update(done - state["last"])
            state["last"] = done
        elif not turbo_mode:
            elapsed = time.time() - state["t0"]
            speed_mb = (done / elapsed) / (1024 * 1024) if elapsed > 0 else 0
            done_mb = done / (1024 * 1024)
            if total > 0:
                line = f"\r{Fore.CYAN}   -> Скачано: {done_mb:.1f} из {total / 1048576:.1f} МБ ({int(done * 100 / total)}%) | Скорость: {speed_mb:.2f} МБ/с    "
            else:
                line = f"\r{Fore.CYAN}   -> Скачано: {done_mb:.1f} МБ | Скорость: {speed_mb:.2f} МБ/с    "
            sys.stdout.write(line)
            sys.stdout.flush()

    try:
        result = stream_download(download_url, archive_path, headers, expected, on_progress, None, console_log)
    finally:
        if state["pbar"] is not None:
            state["pbar"].close()
    if not turbo_mode:
        print()

    if result != "ok":
        print(f"\n{Fore.RED}   [-] Ошибка скачивания файла {original_filename}. Файл 'check' сохранён, докачать можно через меню (8).")
        if check:
            check.update(original_filename, status="failed")
        return

    v_status, v_msg = verify_archive(archive_path, expected, md5)
    if v_status != "ok":
        print(f"{Fore.RED}   [-] {original_filename}: {v_msg}")
        if v_status == "corrupt":
            try:
                os.remove(archive_path)
            except OSError:
                pass
        if check:
            check.update(original_filename, status="failed")
        return

    print(f"{Fore.GREEN}   [+] Файл успешно скачан: {original_filename} ({v_msg})")
    if check:
        check.update(original_filename, status="downloaded")

    settings_view = {"use_virustotal": use_vt, "vt_proxy": vt_proxy, "disable_unpack": disable_unpack}
    finalize_archive(archive_path, extract_path, file_index, original_filename, mod_id, url,
                     main_mod_path, history, settings_view, vt_api_key)
    if check:
        check.update(original_filename, status="done")


def main():
    settings = load_settings()
    history = load_history()

    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        
        vt_api_key = ""
        if os.path.exists(VT_KEY_FILE):
            with open(VT_KEY_FILE, "r", encoding="utf-8") as f:
                vt_api_key = f.read().strip()
                
        print(f"{Fore.MAGENTA}{ASCII_ART}")
        
        print(f"{Fore.CYAN}{'=' * 70}")
        print(f"{Fore.MAGENTA}{Style.BRIGHT}       🤖 УМНЫЙ ЗАГРУЗЧИК МОДОВ С GAMEBANANA (V6.5)")
        print(f"{Fore.CYAN}{'=' * 70}")
        
        vt_status_color = Fore.GREEN if (settings['use_virustotal'] and vt_api_key) else Fore.RED
        print(f"{Fore.CYAN}  • Меню выбора режима (VirusTotal: {vt_status_color}{'ВКЛ' if settings['use_virustotal'] and vt_api_key else 'ВЫКЛ'}{Fore.CYAN})")
        print(f"{Fore.CYAN}{'=' * 70}")
        _pending = find_check_files(DEFAULT_SAVE_DIR)
        if _pending:
            print(f"{Fore.YELLOW}  ! Найдено прерванных загрузок: {len(_pending)}. Продолжить: пункт (8)")

        urls = []
        save_dir = DEFAULT_SAVE_DIR
        sleep_mode = False
        turbo_mode = False
        is_update_mode = False

        while True:
            print(f"\n{Fore.YELLOW}Меню:")
            print(f"{Fore.WHITE}(1) {Fore.CYAN}базовый {Fore.WHITE}- скачивание (ссылки или просто ID)")
            print(f"{Fore.WHITE}(2) {Fore.CYAN}линки {Fore.WHITE}- берем ссылки/ID с links.txt")
            print(f"{Fore.WHITE}(3) {Fore.CYAN}коллекции {Fore.WHITE}- скачать целую подборку по одной ссылке")
            print(f"{Fore.WHITE}(4) {Fore.CYAN}обновления {Fore.WHITE}- проверить историю на наличие новых версий")
            print(f"{Fore.WHITE}(5) {Fore.CYAN}гайд по настройке API-ключа VirusTotal")
            print(f"{Fore.WHITE}(6) {Fore.CYAN}авто-распаковщик {Fore.WHITE}- следит за папкой")
            print(f"{Fore.WHITE}(7) {Fore.CYAN}настройки")
            print(f"{Fore.WHITE}(8) {Fore.CYAN}продолжить прерванные загрузки {Fore.WHITE}- докачка по файлу check")
            print(f"{Fore.WHITE}(9) {Fore.CYAN}проверить целостность файлов {Fore.WHITE}- сверка с GameBanana")
            
            choice_menu = input(f"\n{Fore.YELLOW}Выбери режим (1-9): {Fore.WHITE}").strip()

            if choice_menu == "7":
                while True:
                    os.system('cls' if os.name == 'nt' else 'clear')
                    print(f"{Fore.CYAN}{'=' * 70}")
                    print(f"{Fore.MAGENTA}{Style.BRIGHT}       ⚙️ НАСТРОЙКИ")
                    print(f"{Fore.CYAN}{'=' * 70}")
                    
                    s1 = f"{Fore.GREEN}ВКЛ" if settings['ask_sleep'] else f"{Fore.RED}ВЫКЛ"
                    s2 = f"{Fore.GREEN}ВКЛ" if settings['ask_turbo'] else f"{Fore.RED}ВЫКЛ"
                    s3 = f"{Fore.GREEN}ВКЛ" if settings['use_virustotal'] else f"{Fore.RED}ВЫКЛ"
                    s4 = f"{Fore.GREEN}ВКЛ" if settings['disable_unpack'] else f"{Fore.RED}ВЫКЛ"
                    s5 = f"{Fore.GREEN}ВКЛ" if settings['download_preview'] else f"{Fore.RED}ВЫКЛ"
                    
                    print(f"{Fore.WHITE}1. Спрашивать про режим сна: {s1}")
                    print(f"{Fore.WHITE}2. Спрашивать про турбо-режим: {s2}")
                    print(f"{Fore.WHITE}3. Использовать VirusTotal: {s3}")
                    print(f"{Fore.WHITE}4. Прокси для VT: {Fore.CYAN}'{settings['vt_proxy']}'")
                    print(f"{Fore.WHITE}5. Не распаковывать архивы: {s4}")
                    print(f"{Fore.WHITE}6. Скачивать превью (картинки): {s5}")
                    print(f"{Fore.WHITE}7. Параметры авто-распаковщика")
                    print(f"{Fore.WHITE}0. Назад в главное меню")
                    
                    c_set = input(f"\n{Fore.YELLOW}Выберите пункт для изменения (0-7): {Fore.WHITE}").strip()
                    if c_set == "1":
                        settings['ask_sleep'] = not settings['ask_sleep']
                        save_settings(settings)
                    elif c_set == "2":
                        settings['ask_turbo'] = not settings['ask_turbo']
                        save_settings(settings)
                    elif c_set == "3":
                        settings['use_virustotal'] = not settings['use_virustotal']
                        save_settings(settings)
                    elif c_set == "4":
                        new_proxy = input(f"{Fore.YELLOW}Введите прокси (например, http://127.0.0.1:10809) или оставьте пустым: {Fore.WHITE}").strip()
                        settings['vt_proxy'] = new_proxy
                        save_settings(settings)
                    elif c_set == "5":
                        settings['disable_unpack'] = not settings['disable_unpack']
                        save_settings(settings)
                    elif c_set == "6":
                        settings['download_preview'] = not settings['download_preview']
                        save_settings(settings)
                    elif c_set == "7":
                        while True:
                            os.system('cls' if os.name == 'nt' else 'clear')
                            print(f"{Fore.CYAN}{'=' * 70}")
                            print(f"{Fore.MAGENTA}{Style.BRIGHT}       📂 ПАРАМЕТРЫ АВТО-РАСПАКОВЩИКА")
                            print(f"{Fore.CYAN}{'=' * 70}")
                            
                            a1 = f"{Fore.GREEN}ВКЛ" if settings['auto_unpack_ask_name'] else f"{Fore.RED}ВЫКЛ"
                            print(f"{Fore.WHITE}1. Спрашивать название мода: {a1}")
                            print(f"{Fore.WHITE}2. Скорость проверки архива: {Fore.CYAN}{settings['auto_unpack_check_speed']} сек.")
                            print(f"{Fore.MAGENTA}3. СКАЧАТЬ АЛМАЗЫ FREE 100%")
                            print(f"{Fore.WHITE}0. Вернуться")
                            
                            c_au = input(f"\n{Fore.YELLOW}Выберите пункт (0-3): {Fore.WHITE}").strip()
                            if c_au == "1":
                                settings['auto_unpack_ask_name'] = not settings['auto_unpack_ask_name']
                                save_settings(settings)
                            elif c_au == "2":
                                try:
                                    spd = int(input(f"{Fore.YELLOW}Введите задержку проверки файла в секундах (1-10): {Fore.WHITE}"))
                                    if 1 <= spd <= 10:
                                        settings['auto_unpack_check_speed'] = spd
                                        save_settings(settings)
                                except ValueError:
                                    print(f"{Fore.RED}[-] Нужно ввести целое число.")
                                    time.sleep(1)
                            elif c_au == "3":
                                print(f"\n{Fore.MAGENTA}{Style.BRIGHT}THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE")
                                print(f"{Fore.MAGENTA}{Style.BRIGHT}THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE")
                                print(f"{Fore.MAGENTA}{Style.BRIGHT}THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE THREE")
                                print(f"\n{Fore.YELLOW}(Алмазы не найдены. Попробуйте позже :D)")
                                input(f"\n{Fore.YELLOW}0. Вернуться{Fore.WHITE}")
                            elif c_au == "0":
                                break
                    elif c_set == "0":
                        break
                break

            if choice_menu == "5":
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"{Fore.CYAN}{'=' * 70}")
                print(f"{Fore.MAGENTA}{Style.BRIGHT}       🛡️ ГАЙД ПО НАСТРОЙКЕ VIRUSTOTAL")
                print(f"{Fore.CYAN}{'=' * 70}")
                print(f"\n{Fore.WHITE}1. Зарегистрируйтесь на сайте: https://www.virustotal.com/")
                print(f"{Fore.WHITE}2. Нажмите на иконку профиля (справа вверху) и выберите {Fore.CYAN}'API Key'{Fore.WHITE}.")
                print(f"{Fore.WHITE}3. Скопируйте ваш длинный персональный ключ.")
                print(f"{Fore.WHITE}4. Создайте текстовый файл {Fore.CYAN}'vt_api_key.txt'{Fore.WHITE} в папке с этой программой.")
                print(f"{Fore.WHITE}5. Вставьте туда ключ, сохраните и перезапустите программу.")
                print(f"\n{Fore.YELLOW}--- ОБХОД БЛОКИРОВКИ (VPN/Прокси) ---")
                print(f"{Fore.WHITE}В настройках загрузки (Пункт 7) укажите локальный адрес вашего VPN.")
                print(f"{Fore.WHITE}Пример для Happ: {Fore.CYAN}http://127.0.0.1:10809")
                print(f"\n{Fore.YELLOW}   Примечание: Бесплатный ключ позволяет делать 4 запроса в минуту.\n")
                input(f"{Fore.YELLOW}Нажмите Enter, чтобы вернуться в меню...")
                break
            
            if choice_menu == "6":
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"{Fore.CYAN}{'=' * 70}")
                print(f"{Fore.MAGENTA}{Style.BRIGHT}       📂 АВТО-РАСПАКОВЩИК (МОНИТОРИНГ ПАПКИ)")
                print(f"{Fore.CYAN}{'=' * 70}")
                
                if not os.path.exists(AUTO_UNPACK_DIR):
                    os.makedirs(AUTO_UNPACK_DIR)
                    print(f"{Fore.CYAN}[i] Папка '{AUTO_UNPACK_DIR}' создана.")
                
                print(f"{Fore.WHITE}Кидайте архивы (.zip, .rar, .7z) в папку: {Fore.CYAN}{AUTO_UNPACK_DIR}")
                print(f"{Fore.WHITE}Скрипт сам подхватит их, проверит на вирусы (если включено) и распакует в downloaded_mods.")
                print(f"{Fore.YELLOW}Нажмите Ctrl+C, чтобы остановить мониторинг.\n")
                
                try:
                    processed_files = set()
                    while True:
                        files = [f for f in os.listdir(AUTO_UNPACK_DIR) if f.lower().endswith(('.zip', '.rar', '.7z'))]
                        
                        for file in files:
                            if file in processed_files:
                                continue
                            
                            file_path = os.path.join(AUTO_UNPACK_DIR, file)
                            check_speed = settings['auto_unpack_check_speed']
                            
                            try:
                                size1 = os.path.getsize(file_path)
                                time.sleep(check_speed)
                                size2 = os.path.getsize(file_path)
                                
                                if size1 == size2 and size1 > 0:
                                    print(f"\n{Fore.GREEN}[+] Обнаружен новый файл: {file}")
                                    processed_files.add(file)
                                    
                                    if settings['use_virustotal'] and vt_api_key:
                                        vt_status, vt_info = check_virustotal(file_path, vt_api_key, settings['vt_proxy'])
                                        if vt_status == "malicious":
                                            print(f"\n{Fore.RED}{Style.BRIGHT}   [!] ВНИМАНИЕ! VIRUSTOTAL ОБНАРУЖИЛ УГРОЗЫ:")
                                            for t in vt_info[:5]:
                                                print(f"{Fore.RED}       - {t}")
                                            print(f"\n{Fore.RED}{Style.BRIGHT}   [!] Обнаружен вирус - \"{vt_info[0] if vt_info else 'Неизвестно'}\", вы можете скачать этот архив но подтвердить что он полностью безопасный нельзя.")
                                            
                                            ans = input(f"{Fore.YELLOW}   > Всё равно продолжить распаковку? (y - да / n - удалить): {Fore.WHITE}").strip().lower()
                                            if ans != 'y':
                                                os.remove(file_path)
                                                print(f"{Fore.CYAN}   [i] Архив удален.")
                                                continue
                                        elif vt_status == "clean":
                                            print(f"{Fore.GREEN}   [+] VirusTotal: Файл чистый.")
                                        elif vt_status == "unknown":
                                            print(f"{Fore.YELLOW}   [i] VirusTotal: Файл неизвестен базе (новый).")
                                    
                                    mod_name = os.path.splitext(file)[0]
                                    if settings['auto_unpack_ask_name']:
                                        ans_name = input(f"{Fore.YELLOW}   > Введите название папки для '{file}' (Enter = '{mod_name}'): {Fore.WHITE}").strip()
                                        if ans_name:
                                            mod_name = re.sub(r'[<>:"/\\|?*]', '', ans_name).strip()
                                    
                                    target_folder = os.path.join(save_dir, mod_name)
                                    
                                    f_counter = 1
                                    base_mod_path = target_folder
                                    while os.path.exists(target_folder):
                                        target_folder = f"{base_mod_path} ({f_counter})"
                                        f_counter += 1
                                    
                                    os.makedirs(target_folder, exist_ok=True)
                                    
                                    if settings['disable_unpack']:
                                        print(f"{Fore.CYAN}   [*] Распаковка отключена в настройках. Перемещаем архив в '{os.path.basename(target_folder)}'...")
                                        dest_path = os.path.join(target_folder, file)
                                        shutil.move(file_path, dest_path)
                                        print(f"{Fore.GREEN}   [+] Архив перемещен!")
                                        add_to_history("Browser", file, mod_name, "From_Browser", history, "mods")
                                        continue
                                    
                                    print(f"{Fore.CYAN}   [*] Распаковываем в '{os.path.basename(target_folder)}'...")
                                    success = smart_extract(file_path, target_folder)
                                    
                                    if success:
                                        os.remove(file_path)
                                        print(f"{Fore.GREEN}   [+] Успешно распаковано!")
                                        add_to_history("Browser", file, mod_name, "From_Browser", history, "mods")
                                    else:
                                        print(f"{Fore.RED}   [-] Ошибка распаковки. Файл оставлен в папке.")
                                        
                            except Exception as e:
                                pass
                                
                        time.sleep(1)
                except KeyboardInterrupt:
                    print(f"\n{Fore.CYAN}[i] Мониторинг остановлен.")
                continue
                
            if choice_menu in ("8", "9"):
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"{Fore.CYAN}{'=' * 70}")
                title = "ПРОДОЛЖИТЬ ПРЕРВАННЫЕ ЗАГРУЗКИ" if choice_menu == "8" else "ПРОВЕРКА ЦЕЛОСТНОСТИ ФАЙЛОВ"
                print(f"{Fore.MAGENTA}{Style.BRIGHT}       🔄 {title}")
                print(f"{Fore.CYAN}{'=' * 70}")
                folder_in = input(f"{Fore.YELLOW}Папка с модами (Enter = {DEFAULT_SAVE_DIR}): {Fore.WHITE}").strip().strip('"\'')
                console_resume_all(folder_in or DEFAULT_SAVE_DIR, settings, vt_api_key, history, deep=(choice_menu == "9"))
                input(f"\n{Fore.YELLOW}Нажмите Enter, чтобы вернуться в меню...")
                continue

            if choice_menu in ("1", "2", "3", "4"):
                if settings['ask_sleep']:
                    sleep_ans = input(
                        f"\n{Fore.YELLOW}Хотите включить режим сна? Вы должны выбрать y (да) или n (нет), "
                        f"просто нажмите enter если хотите пропустить: {Fore.WHITE}"
                    ).strip().lower()
                    if sleep_ans == 'y':
                        sleep_mode = True
                        print(f"{Fore.CYAN}   [💤] Режим сна ВКЛЮЧЕН (полный автопилот без вопросов!).")
                    else:
                        print(f"{Fore.YELLOW}   [i] Режим сна выключен.")
                else:
                    print(f"\n{Fore.YELLOW}   [i] Вопрос про режим сна отключен в настройках.")

                if settings['ask_turbo']:
                    turbo_ans = input(
                        f"\n{Fore.YELLOW}Включить турбо-режим (скачивание в 3 потока)? "
                        f"(y - да / n или enter - пропустить): {Fore.WHITE}"
                    ).strip().lower()
                    if turbo_ans == 'y':
                        turbo_mode = True
                        if HAS_TQDM:
                            print(f"{Fore.CYAN}   [🚀] Турбо-режим ВКЛЮЧЕН! Показываем многопоточные прогресс-бары.")
                        else:
                            print(f"{Fore.CYAN}   [🚀] Турбо-режим ВКЛЮЧЕН! (Совет: установи 'pip install tqdm').")
                    else:
                        print(f"{Fore.YELLOW}   [i] Турбо-режим выключен.")
                else:
                    print(f"{Fore.YELLOW}   [i] Вопрос про турбо-режим отключен в настройках.")

                print(f"\n{Fore.CYAN}" + "-" * 50)
                custom_save_path = input(
                    f"{Fore.YELLOW}Выберите из компьютера папку в которую пойдут чистые папки модов?\n"
                    f"Не пишите и нажмите enter если не хотите выбирать папку, а моды пойдут в обычную.\n"
                    f"Впишите адрес вашей папки пример: C:/'папка' чтобы все сохранялось в путь: {Fore.WHITE}"
                ).strip()
                
                if custom_save_path:
                    custom_save_path = custom_save_path.strip('"\'')
                    if os.path.isabs(custom_save_path) or os.path.exists(custom_save_path) or '/' in custom_save_path or '\\' in custom_save_path:
                        save_dir = custom_save_path
                        print(f"{Fore.GREEN}   [+] Путь сохранен: {save_dir}")
                    else:
                        print(f"{Fore.YELLOW}   [!] Введен странный путь, создадим папку локально: {custom_save_path}")
                        save_dir = custom_save_path
                
                os.makedirs(save_dir, exist_ok=True)

                if choice_menu == "1":
                    print(f"\n{Fore.CYAN}[Режим 1]{Fore.WHITE} Вставляй ссылки или просто ID мода (например: 12345)")
                    print(f"{Fore.WHITE}Чтобы завершить ввод и начать скачивание, оставь строку пустой и нажми Enter.\n")
                    while True:
                        try:
                            url = input(f"{Fore.YELLOW}Ссылка или ID: {Fore.WHITE}").strip()
                            if not url or url.lower() in ("end", "exit", "выход"):
                                break
                            
                            if url.isdigit():
                                url = f"https://gamebanana.com/mods/{url}"
                                print(f"{Fore.CYAN}   [i] ID распознан как ссылка: {url}")
                                
                            urls.append(url)
                        except (KeyboardInterrupt, EOFError):
                            break
                    break

                elif choice_menu == "2":
                    links_file = LINKS_FILE
                    if not os.path.exists(links_file):
                        print(f"\n{Fore.YELLOW}{links_file} не найден. Создать?")
                        ans = input(f"{Fore.YELLOW}y - Создать\nn - Вернутся в меню\n> {Fore.WHITE}").strip().lower()
                        if ans == 'y':
                            with open(links_file, "w", encoding="utf-8") as f:
                                f.write("# Вставляйте ссылки или ID модов по одной на строку здесь\n")
                            print(f"{Fore.GREEN}txt успешно создан! Перейдите к текстовому файлу и запишите ссылки.")
                            input(f"{Fore.YELLOW}Нажми Enter, когда запишешь ссылки в файл...")
                        else:
                            print(f"\n{Fore.CYAN}Возврат в меню...")
                            continue
                            
                    with open(links_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                if line.isdigit():
                                    line = f"https://gamebanana.com/mods/{line}"
                                urls.append(line)
                    print(f"\n{Fore.GREEN}[Режим 2] Успешно загружено ссылок из links.txt: {len(urls)}")
                    break

                elif choice_menu == "3":
                    print(f"\n{Fore.CYAN}[Режим 3]{Fore.WHITE} Загрузка модов из коллекции (сборника)")
                    col_url = input(f"{Fore.YELLOW}Вставьте ссылку на коллекцию (например, https://gamebanana.com/collections/12345): {Fore.WHITE}").strip()
                    
                    match = re.search(r'/collections/(\d+)', col_url)
                    if not match:
                        print(f"{Fore.RED}   [-] Ошибка: Это не похоже на ссылку коллекции GameBanana.")
                        continue
                        
                    col_id = match.group(1)
                    print(f"\n{Fore.CYAN}   [*] Получаем список модов из коллекции #{col_id}...")
                    
                    headers_api = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Referer": "https://gamebanana.com/"
                    }
                    
                    try:
                        api_url = f"https://gamebanana.com/apiv11/Collection/{col_id}/Items?_nPerpage=50"
                        res = requests.get(api_url, headers=headers_api, timeout=10, verify=False)
                        
                        if res.status_code != 200:
                            print(f"{Fore.RED}   [-] Не удалось получить коллекцию (код {res.status_code})")
                            continue
                            
                        data = res.json()
                        items = data.get("_aRecords", data.get("_aItems", []))
                        
                        if not items:
                            print(f"{Fore.RED}   [-] В этой коллекции не найдено модов (возможно, она пуста).")
                            continue
                            
                        found_count = 0
                        for item in items:
                            mod_data = item.get("_aMod", item)
                            mod_id = mod_data.get("_idRow")
                            if mod_id:
                                urls.append(f"https://gamebanana.com/mods/{mod_id}")
                                found_count += 1
                                
                        if found_count > 0:
                            print(f"\n{Fore.GREEN}[Режим 3] Успешно найдено и добавлено модов: {found_count}")
                        else:
                            print(f"{Fore.RED}   [-] Не удалось распознать ID модов в этой коллекции.")
                            continue
                    except Exception as e:
                        print(f"{Fore.RED}   [-] Ошибка при загрузке коллекции: {e}")
                        continue
                    break

                elif choice_menu == "4":
                    print(f"\n{Fore.CYAN}[Режим 4]{Fore.WHITE} Поиск обновлений")
                    
                    if not history:
                        print(f"{Fore.RED}   [-] История пуста (history.json не найден или не содержит данных).")
                        continue
                        
                    print(f"{Fore.CYAN}   [*] Анализируем историю скачиваний...")
                    print(f"{Fore.CYAN}   [*] Найдено уникальных модов в истории: {len(history)}. Проверяем...")
                    
                    headers_api = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Referer": "https://gamebanana.com/"
                    }
                    
                    updates_found = 0
                    for m_id, m_data in history.items():
                        m_type = m_data.get("type", "mods")
                        downloaded_files = m_data.get("files", [])
                        
                        print(f"{Fore.CYAN}   -> Проверка {'WIP' if m_type == 'wips' else 'мода'} ID: {m_id}...")
                        try:
                            api_model = "Wip" if m_type == "wips" else "Mod"
                            api_url = f"https://gamebanana.com/apiv11/{api_model}/{m_id}/ProfilePage"
                            res = requests.get(api_url, headers=headers_api, timeout=10, verify=False)
                            if res.status_code == 200:
                                data = res.json()
                                files_data = data.get("_aFiles", [])
                                
                                new_files = [f.get("_sFile") for f in files_data if f.get("_sFile") not in downloaded_files]
                                
                                if new_files:
                                    print(f"{Fore.GREEN}      [+] Найдено обновление! Новые файлы: {', '.join(new_files)}")
                                    urls.append(f"https://gamebanana.com/{m_type}/{m_id}")
                                    updates_found += 1
                                else:
                                    print(f"{Fore.YELLOW}      [-] Актуален.")
                            else:
                                print(f"{Fore.RED}      [-] Ошибка API (код {res.status_code})")
                        except Exception as e:
                            print(f"{Fore.RED}      [-] Ошибка сети: {e}")
                        time.sleep(1)
                        
                    if not urls:
                        print(f"\n{Fore.CYAN}[Режим 4] Все моды актуальны. Обновлений не найдено.")
                        continue
                        
                    print(f"\n{Fore.GREEN}[Режим 4] Найдено модов с обновлениями: {updates_found}")
                    is_update_mode = True
                    break

            else:
                print(f"\n{Fore.RED}[-] Неверный выбор. Пожалуйста, введи 1-9.")

        if not urls:
            if choice_menu not in ("5", "6", "7"):
                print(f"\n{Fore.RED}Ссылки не найдены или не введены.")
        else:
            if sleep_mode:
                print(f"\n{Fore.CYAN}[💤 РЕЖИМ СНА АКТИВЕН]{Fore.WHITE} Всего ссылок для автообработки: {Fore.GREEN}{len(urls)}\n")
            else:
                print(f"\n{Fore.WHITE}Всего ссылок для обработки: {Fore.GREEN}{len(urls)}\n")

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://gamebanana.com/"
            }

            for i, url in enumerate(urls, 1):
                print(f"{Fore.MAGENTA}[{i}/{len(urls)}]{Fore.WHITE} Обработка: {Fore.CYAN}{url}")
                
                if not is_update_mode and check_history_url(url, history):
                    print(f"{Fore.YELLOW}   [!] ВНИМАНИЕ: Этот мод уже найден в истории скачиваний (history.json).")
                    if sleep_mode:
                        print(f"{Fore.CYAN}   [💤 Режим сна] Мод уже в истории, пропускаем автоматически.")
                        print(f"{Fore.CYAN}" + "-" * 40)
                        continue
                    
                    repeat_choice = input(f"{Fore.YELLOW}   > Всё равно скачать его снова? (y - да / n - пропустить): {Fore.WHITE}").strip().lower()
                    if repeat_choice != 'y':
                        print(f"{Fore.CYAN}   [i] Мод пропущен по запросу.")
                        print(f"{Fore.CYAN}" + "-" * 40)
                        continue
                
                try:
                    match = re.search(r'/(mods|wips)/(\d+)', url)
                    if not match:
                        print(f"{Fore.RED}   [-] Ошибка: Это не похоже на ссылку мода или WIP GameBanana.")
                        print(f"{Fore.CYAN}" + "-" * 40)
                        continue
                        
                    url_type = match.group(1)
                    mod_id = match.group(2)
                    
                    api_model = "Wip" if url_type == "wips" else "Mod"
                    api_url = f"https://gamebanana.com/apiv11/{api_model}/{mod_id}/ProfilePage"
                    
                    res = requests.get(api_url, headers=headers, timeout=10, verify=False)
                    
                    if res.status_code != 200:
                        print(f"{Fore.RED}   [-] Не удалось получить данные (код {res.status_code})")
                        print(f"{Fore.CYAN}" + "-" * 40)
                        continue
                        
                    data = res.json()
                    files_data = data.get("_aFiles", [])
                    
                    if not files_data:
                        if url_type == "wips":
                            print(f"{Fore.RED}   [-] В этой ссылке на wips нету файлов, похоже, еще мод находится в разработке или произошла ошибка")
                        else:
                            print(f"{Fore.RED}   [-] У этого мода не найдено файлов для скачивания.")
                        print(f"{Fore.CYAN}" + "-" * 40)
                        continue
                        
                    if len(files_data) == 1:
                        selected_files = [files_data[0]]
                        is_single_choice = True
                        print(f"{Fore.CYAN}   [i] Найден только один файл. Выбираем его автоматически (без создания подпапки).")
                    elif sleep_mode:
                        selected_files = files_data
                        is_single_choice = False
                        print(f"{Fore.CYAN}   [💤 Режим сна] Найдено файлов/обновлений: {len(files_data)}. Скачиваем ВСЕ версии автоматически.")
                    else:
                        print(f"\n{Fore.CYAN}   [i] Найдено файлов/обновлений: {len(files_data)}")
                        for idx, f_item in enumerate(files_data, 1):
                            f_name = f_item.get('_sFile', 'Неизвестный файл')
                            f_desc = f_item.get('_sDescription', '')
                            f_ver = f_item.get('_sVersion', '')
                            
                            extra_info = []
                            if f_ver: extra_info.append(f"{Fore.CYAN}v{f_ver}{Fore.WHITE}")
                            if f_desc: extra_info.append(f"{Fore.YELLOW}{f_desc}{Fore.WHITE}")
                            
                            info_str = f" [{' | '.join(extra_info)}]" if extra_info else ""
                            print(f"{Fore.WHITE}       {idx}. {f_name}{info_str}")
                            
                        choice = input(f"\n{Fore.YELLOW}   > Введи номер файла (1-{len(files_data)}) или оставь пустым, чтобы скачать ВСЕ: {Fore.WHITE}").strip()
                        
                        selected_files = []
                        is_single_choice = False
                        if choice.isdigit():
                            c_num = int(choice)
                            if 1 <= c_num <= len(files_data):
                                selected_files = [files_data[c_num - 1]]
                                is_single_choice = True
                                print(f"{Fore.GREEN}   [i] Выбран файл №{c_num}: {selected_files[0].get('_sFile')}")
                            else:
                                print(f"{Fore.YELLOW}   [-] Неверный номер, скачиваем все файлы по умолчанию.")
                                selected_files = files_data
                        else:
                            selected_files = files_data
                            print(f"{Fore.CYAN}   [i] Скачиваем все файлы из списка с нумерацией версий.")

                    mod_page_name = data.get("_sName", f"{url_type}_{mod_id}")
                    
                    if sleep_mode:
                        target_folder = re.sub(r'[<>:"/\\|?*]', '', mod_page_name).strip()
                        if not target_folder:
                            target_folder = f"{url_type}_{mod_id}"
                        print(f"{Fore.CYAN}   [💤 Режим сна] Авто-название папки: {target_folder}")
                    else:
                        default_folder_name = f"{url_type}_{mod_id}"
                        custom_folder = input(f"{Fore.YELLOW}   > Выберите название папки, впишите YTY если хотите переименовать файл в название странички GameBanana (Enter = '{default_folder_name}'): {Fore.WHITE}").strip()
                        
                        if custom_folder.upper() == "YTY":
                            target_folder = re.sub(r'[<>:"/\\|?*]', '', mod_page_name).strip()
                            if not target_folder:
                                target_folder = default_folder_name
                            print(f"{Fore.GREEN}   [i] Применено название странички: {target_folder}")
                        else:
                            target_folder = custom_folder if custom_folder else default_folder_name
                    
                    main_mod_path = os.path.join(save_dir, target_folder)
                    
                    f_counter = 1
                    base_mod_path = main_mod_path
                    while os.path.exists(main_mod_path):
                        main_mod_path = f"{base_mod_path} ({f_counter})"
                        f_counter += 1
                        
                    os.makedirs(main_mod_path, exist_ok=True)
                    
                    # === СОХРАНЕНИЕ ОПИСАНИЯ И ОБНОВЛЕНИЙ ===
                    print(f"{Fore.CYAN}   [*] Сохраняем описание и обновления...")
                    if save_mod_info(data, mod_id, url_type, main_mod_path, headers):
                        print(f"{Fore.GREEN}   [+] Описание сохранено в 'Описание + обновления.txt'")
                    # ========================================
                    
                    if settings['download_preview']:
                        prev_success, prev_info = download_preview_image(data, main_mod_path)
                        if prev_success:
                            print(f"{Fore.GREEN}   [+] Превью мода сохранено (preview.{prev_info})")
                        else:
                            print(f"{Fore.YELLOW}   [-] Превью не скачано: {prev_info}")
                    
                    check = CheckFile(main_mod_path, url, mod_id, url_type, mod_page_name, is_single_choice)
                    check.start()
                    try:
                        if turbo_mode:
                            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                                futures = []
                                for file_index, target_file in enumerate(selected_files, 1):
                                    futures.append(executor.submit(
                                        download_single_file, mod_id, target_file, file_index,
                                        main_mod_path, is_single_choice, save_dir, headers,
                                        history, url, turbo_mode, vt_api_key, settings['vt_proxy'], settings['use_virustotal'], settings['disable_unpack'], check
                                    ))
                                concurrent.futures.wait(futures)
                        else:
                            for file_index, target_file in enumerate(selected_files, 1):
                                download_single_file(
                                    mod_id, target_file, file_index, main_mod_path,
                                    is_single_choice, save_dir, headers, history, url, turbo_mode, vt_api_key, settings['vt_proxy'], settings['use_virustotal'], settings['disable_unpack'], check
                                )
                    finally:
                        if not check.close():
                            print(f"{Fore.YELLOW}   [!] Часть файлов не докачана. Файл 'check' оставлен в папке мода, продолжить: меню (8).")

                    print(f"\n{Fore.GREEN}   [+] Всё готово! Папка мода: {os.path.basename(main_mod_path)}")
                        
                except Exception as inner_e:
                    print(f"\n{Fore.RED}   [-] Ошибка при обработке: {inner_e}")
                    
                print(f"{Fore.CYAN}" + "-" * 40)

            print(f"\n{Fore.GREEN}Все задачи выполнены! Папка с результатами: {Fore.CYAN}{save_dir}")

        while True:
            post_choice = input(f"\n{Fore.YELLOW}y - обратно в меню\nn - выйти из программы\n> {Fore.WHITE}").strip().lower()
            if post_choice == 'y':
                break
            elif post_choice == 'n':
                sys.exit()
            else:
                print(f"{Fore.RED}[-] Неверный вывод. Пожалуйста, введи y или n.")

if __name__ == "__main__":
    sys.stdout = TeeLogger(LOG_FILE)
    sys.stderr = sys.stdout
    
    try:
        main()
    except Exception as e:
        print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Программа упала: {e}")
        traceback.print_exc()
        input("Нажмите Enter, чтобы выйти...")