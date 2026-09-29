"""
Извлечение таблицы операций (дата / документ / сумма) из PDF-акта.

Есть два принципиально разных случая:

1. PDF с текстовым слоем — сформирован из 1С/Word/Excel "в PDF".
   Тут текст уже есть внутри файла, распознавание не нужно,
   нужно только аккуратно вытащить таблицу (pdfplumber).

2. PDF без текстового слоя — скан или сфотографированный акт.
   Тут текста в файле нет вообще, есть только картинка страницы —
   и вот для этого нужен OCR (распознавание текста по изображению,
   Tesseract).

Стратегия:
    a) Пробуем pdfplumber.extract_tables() — если в PDF есть таблица
       с линиями/границами, это даёт наиболее надёжный результат.
    b) Если таблиц не нашлось, берём текст страницы: либо это
       текстовый слой (page.extract_text()), либо, если он пустой —
       OCR по рендеру страницы в картинку.
    c) Построчно разбираем текст простым эвристическим парсером:
       ищем дату, номер документа и суммы по регуляркам.

Результат шага (b)+(c) — "черновая" таблица с низкой уверенностью,
её обязательно нужно проверить и поправить руками (в приложении для
этого есть редактируемая таблица) — OCR по бухгалтерским документам
на кириллице ошибается: путает "0"/"О", "5"/"S", разделители разрядов.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pdfplumber
import pytesseract
from PIL import Image

# --- Настройка путей к внешним программам (poppler, tesseract) ---------
# На Linux/macOS обычно всё уже в PATH после установки через apt/brew.
# На Windows и poppler, и tesseract нередко ставятся, но НЕ попадают в
# PATH автоматически — тогда Python не может их найти, хотя они стоят.
#
# Если у вас именно так — не трогайте системный PATH, а просто впишите
# сюда полные пути к папкам с exe-файлами (двойной обратный слэш или
# сырая строка r"..."), например:
#
#   POPPLER_BIN_DIR = r"C:\poppler-24.08.0\Library\bin"
#   TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
#
# Можно и через переменные окружения POPPLER_BIN_DIR / TESSERACT_EXE —
# что зададите, то и используется, ниже переопределять не обязательно.
POPPLER_BIN_DIR = os.environ.get("POPPLER_BIN_DIR", "")
TESSERACT_EXE = os.environ.get("TESSERACT_EXE", "")

if TESSERACT_EXE:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE


def _pdftoppm_path() -> str:
    exe_name = "pdftoppm.exe" if os.name == "nt" else "pdftoppm"
    if POPPLER_BIN_DIR:
        return str(Path(POPPLER_BIN_DIR) / exe_name)
    return exe_name

DATE_RE = re.compile(r"\b(\d{1,2}[./]\d{1,2}[./]\d{2,4})\b")
# число с разделителями разрядов (пробел/точка) и опциональной копейкой через запятую или точку
# число с разделителями разрядов (частый случай) ИЛИ подряд идущие
# 4+ цифр без разделителей (частый случай для OCR, где пробелы теряются)
AMOUNT_RE = re.compile(
    r"-?\d{1,3}(?:[ .]\d{3})+(?:[.,]\d{2})?"
    r"|-?\d{4,}(?:[.,]\d{2})?"
    r"|-?\d{1,3}[.,]\d{2}"
)
DOC_RE = re.compile(r"№\s?[-\wА-Яа-я/]+|[Аа]кт\s?№?\s?[-\wА-Яа-я/]+")


def has_text_layer(pdf_path: str) -> bool:
    """Быстрая проверка: есть ли в PDF извлекаемый текст, или это скан."""
    with pdfplumber.open(pdf_path) as pdf:
        sample = "".join((p.extract_text() or "") for p in pdf.pages[:3])
    return len(sample.strip()) > 20


def extract_tables_pdfplumber(pdf_path: str) -> list[pd.DataFrame]:
    """Пытается вытащить настоящие таблицы (по линиям/границам ячеек).
    Лучший случай — работает точно, без всякой эвристики."""
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for raw_table in page.extract_tables():
                if len(raw_table) < 2:
                    continue
                header, *rows = raw_table
                header = [str(h or f"col_{i}") for i, h in enumerate(header)]
                df = pd.DataFrame(rows, columns=header)
                tables.append(df)
    return tables


def rasterize_and_ocr(pdf_path: str, lang: str = "rus+eng", dpi: int = 300) -> str:
    """Рендерит каждую страницу PDF в картинку и прогоняет через Tesseract.
    Использует внешний pdftoppm (poppler) — он уже стоит почти везде,
    где есть Linux/Mac, и не тянет тяжёлых Python-зависимостей."""
    pdftoppm = _pdftoppm_path()
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "page")
        try:
            subprocess.run(
                [pdftoppm, "-jpeg", "-r", str(dpi), pdf_path, prefix],
                check=True, capture_output=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Не найдена программа '{pdftoppm}' (это часть Poppler, "
                "нужна для превращения PDF в картинку перед распознаванием).\n\n"
                "Похоже, Poppler не установлен или не виден системе. Варианты:\n"
                "  1) Windows: скачайте сборку Poppler для Windows "
                "(например, с https://github.com/oschwartz10612/poppler-windows/releases), "
                "распакуйте и впишите путь к папке .../Library/bin "
                "в переменную POPPLER_BIN_DIR в начале файла pdf_import.py.\n"
                "  2) Или добавьте эту папку в системный PATH и перезапустите терминал.\n"
                "  3) Linux: sudo apt-get install poppler-utils   |   macOS: brew install poppler"
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Poppler (pdftoppm) нашёлся, но завершился с ошибкой:\n"
                f"{e.stderr.decode(errors='ignore') if e.stderr else e}"
            ) from e

        pages_text = []
        for img_path in sorted(Path(tmp).glob("page*.jpg")):
            img = Image.open(img_path)
            # psm 6: "единый блок текста" — подходит для табличных строк лучше,
            # чем режим по умолчанию, который иногда ломает порядок строк
            try:
                text = pytesseract.image_to_string(img, lang=lang, config="--psm 6")
            except FileNotFoundError as e:
                raise RuntimeError(
                    f"Не найдена программа Tesseract ('{pytesseract.pytesseract.tesseract_cmd}').\n\n"
                    "Варианты:\n"
                    "  1) Windows: установите Tesseract (сборка UB-Mannheim: "
                    "https://github.com/UB-Mannheim/tesseract/wiki), при установке "
                    "отметьте галочку 'Russian' в списке языков, затем впишите "
                    "полный путь к tesseract.exe в переменную TESSERACT_EXE "
                    "в начале файла pdf_import.py.\n"
                    "  2) Или добавьте папку с tesseract.exe в системный PATH.\n"
                    "  3) Linux: sudo apt-get install tesseract-ocr tesseract-ocr-rus   |   "
                    "macOS: brew install tesseract tesseract-lang"
                ) from e
            except pytesseract.TesseractError as e:
                if "Failed loading language" in str(e) or "rus" in str(e).lower():
                    raise RuntimeError(
                        "Tesseract нашёлся, но не хватает русского языкового пакета "
                        "(tesseract-ocr-rus / traineddata для 'rus').\n"
                        "Windows: переустановите Tesseract (UB-Mannheim), отметив язык "
                        "'Russian'. Linux: sudo apt-get install tesseract-ocr-rus."
                    ) from e
                raise
            pages_text.append(text)
    return "\n".join(pages_text)


def get_raw_text(pdf_path: str, lang: str = "rus+eng", dpi: int = 300) -> tuple[str, str]:
    """Возвращает (текст, источник), где источник — 'text_layer' или 'ocr'."""
    if has_text_layer(pdf_path):
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        return text, "text_layer"
    return rasterize_and_ocr(pdf_path, lang=lang, dpi=dpi), "ocr"


def parse_lines_to_rows(text: str) -> pd.DataFrame:
    """Эвристический построчный разбор: строка считается операцией,
    если в ней нашлась дата. Номер документа и суммы вытаскиваются
    по остаткам строки. Это черновик для последующей ручной правки,
    не окончательный результат."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        date_matches = list(DATE_RE.finditer(line))
        if len(date_matches) != 1:
            # 0 дат — не операция; 2+ дат — почти всегда строка вида
            # "за период с ... по ...", а не конкретная проводка
            continue
        date_match = date_matches[0]

        doc_match = DOC_RE.search(line)
        # суммы ищем во всей строке, кроме уже найденной даты и номера документа
        # (иначе цифры из "№101" сами попадут в кандидаты на сумму)
        rest = line[:date_match.start()] + line[date_match.end():]
        if doc_match:
            rest = rest.replace(doc_match.group(0), "")
        amounts = [a for a in AMOUNT_RE.findall(rest) if len(a.replace(" ", "").replace(".", "")) >= 3]

        rows.append({
            "date_raw": date_match.group(1),
            "doc_number_raw": doc_match.group(0) if doc_match else "",
            "amount_1": amounts[0] if len(amounts) > 0 else "",
            "amount_2": amounts[1] if len(amounts) > 1 else "",
            "raw_line": line,
        })
    return pd.DataFrame(rows)


def extract_ledger_candidates(pdf_path: str, lang: str = "rus+eng", dpi: int = 300) -> dict:
    """Главная точка входа. Возвращает словарь с диагностикой и
    черновой таблицей, готовой для показа пользователю на правку:

        {
            "method": "table" | "text_layer" | "ocr",
            "tables": [DataFrame, ...],   # если method == "table"
            "draft_rows": DataFrame,      # если method in ("text_layer", "ocr")
            "raw_text": str,
        }
    """
    tables = extract_tables_pdfplumber(pdf_path)
    if tables:
        return {"method": "table", "tables": tables, "draft_rows": None, "raw_text": None}

    text, source = get_raw_text(pdf_path, lang=lang, dpi=dpi)
    draft = parse_lines_to_rows(text)
    return {"method": source, "tables": None, "draft_rows": draft, "raw_text": text}
