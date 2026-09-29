# -*- coding: utf-8 -*-
"""
reconcile_core.py

Ядро программы сверки взаиморасчётов между нашей организацией и
контрагентом на основе выгрузок из 1С (акт сверки).

Идея работы:
1. Загружаем два файла Excel — нашу выгрузку и выгрузку/акт контрагента.
   1С обычно экспортирует акт сверки с "шапкой" (название организаций,
   период, реквизиты) над самой таблицей, поэтому строка заголовков
   таблицы определяется автоматически.
2. Приводим обе таблицы к единому виду: Дата, Документ, Дебет, Кредит.
3. Сопоставляем записи между сторонами (по номеру документа+дате+сумме,
   затем по дате+сумме, затем по номеру документа) и находим расхождения:
   - есть только у нас;
   - есть только у контрагента;
   - есть у обеих сторон, но отличается сумма и/или дата.
4. Формируем результат в виде набора таблиц, готовых к выгрузке в Excel.

Это рабочий прототип. Реальные выгрузки 1С отличаются по конфигурациям
(УТ, БП, ERP и т.д.), поэтому названия колонок ищутся по ключевым словам
и их можно расширить в COLUMN_KEYWORDS ниже.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Настройки распознавания колонок
# ---------------------------------------------------------------------------

COLUMN_KEYWORDS = {
    "date": ["дата"],
    "document": ["документ", "№ докум", "номер докум", "№п/п", "основание"],
    "debit": ["дебет", "приход"],
    "credit": ["кредит", "расход"],
}

MAX_HEADER_SEARCH_ROWS = 20
AMOUNT_TOLERANCE = 0.01          # копейки — расхождением не считаем
DATE_TOLERANCE_DAYS = 0          # 0 = даты должны совпадать точно на этапе 1


def _norm(s) -> str:
    """Нормализует строку ячейки для поиска ключевых слов."""
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    return s.strip().lower()


def _find_header_row(raw: pd.DataFrame) -> int:
    """Ищет строку, похожую на заголовок таблицы (Дата/Документ/Дебет/Кредит)."""
    best_row, best_score = 0, -1
    limit = min(MAX_HEADER_SEARCH_ROWS, len(raw))
    for i in range(limit):
        row_vals = [_norm(v) for v in raw.iloc[i].tolist()]
        score = 0
        for keywords in COLUMN_KEYWORDS.values():
            if any(any(kw in cell for kw in keywords) for cell in row_vals):
                score += 1
        if score > best_score:
            best_score, best_row = score, i
    if best_score < 2:
        raise ValueError(
            "Не удалось найти строку заголовков таблицы (Дата/Документ/Дебет/Кредит). "
            "Проверьте формат выгрузки или укажите номер строки заголовка вручную "
            "параметром header_row=..."
        )
    return best_row


def _map_columns(header_row_values: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, raw_name in enumerate(header_row_values):
        name = _norm(raw_name)
        for field_name, keywords in COLUMN_KEYWORDS.items():
            if field_name in mapping:
                continue
            if any(kw in name for kw in keywords):
                mapping[field_name] = idx
    missing = [f for f in ("date", "debit", "credit") if f not in mapping]
    if missing:
        raise ValueError(f"В таблице не найдены обязательные колонки: {missing}")
    return mapping


def _parse_amount(v) -> float:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    s = s.replace(",", ".")
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(v) -> Optional[datetime]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.replace(hour=0, minute=0, second=0, microsecond=0)
    if hasattr(v, "to_pydatetime"):
        return v.to_pydatetime().replace(hour=0, minute=0, second=0, microsecond=0)
    s = str(v).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).to_pydatetime()
    except Exception:
        return None


def _norm_doc(v) -> str:
    """Нормализует номер документа для сравнения (без учёта регистра/пробелов/ведущих нулей)."""
    s = _norm(v)
    s = re.sub(r"^(№|n|no)\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # убираем ведущие нули в числовых кусках: "0007" -> "7"
    s = re.sub(r"\b0+(\d)", r"\1", s)
    return s


@dataclass
class LoadedSide:
    label: str
    df: pd.DataFrame  # columns: date, document, debit, credit, amount, row_no


def load_side(path: str, label: str, sheet_name=0, header_row: int | None = None) -> LoadedSide:
    """Загружает одну сторону акта сверки (наш файл или файл контрагента)."""
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    hdr = header_row if header_row is not None else _find_header_row(raw)
    header_values = [str(v) for v in raw.iloc[hdr].tolist()]
    mapping = _map_columns(header_values)

    data = raw.iloc[hdr + 1:].reset_index(drop=True)

    records = []
    for i, row in data.iterrows():
        date_val = _parse_date(row[mapping["date"]])
        if date_val is None:
            continue  # пропускаем строки итогов/пустые строки без даты
        debit = _parse_amount(row[mapping["debit"]])
        credit = _parse_amount(row[mapping["credit"]])
        doc_raw = row[mapping["document"]] if "document" in mapping else ""
        records.append(
            {
                "row_no": i + hdr + 2,  # номер строки в исходном файле (для навигации)
                "date": date_val,
                "document": "" if doc_raw is None else str(doc_raw).strip(),
                "document_norm": _norm_doc(doc_raw),
                "debit": debit,
                "credit": credit,
                "amount": round(debit - credit, 2),
            }
        )
    df = pd.DataFrame(records)
    return LoadedSide(label=label, df=df)


# ---------------------------------------------------------------------------
# Сопоставление
# ---------------------------------------------------------------------------

@dataclass
class ReconciliationResult:
    matched: pd.DataFrame          # полностью совпавшие операции
    doc_mismatch: pd.DataFrame     # совпали дата+сумма, но разошёлся номер документа (информационно)
    amount_mismatch: pd.DataFrame  # совпал документ, но разошлась сумма и/или дата
    only_ours: pd.DataFrame        # есть только в наших данных
    only_theirs: pd.DataFrame      # есть только у контрагента
    summary: dict = field(default_factory=dict)


def reconcile(ours: LoadedSide, theirs: LoadedSide, mirror_sign: bool = False) -> ReconciliationResult:
    """
    Сопоставляет две таблицы операций.

    mirror_sign=True используется, если стороны отражают одну и ту же операцию
    зеркально (наш дебет = кредит контрагента и наоборот) — типичная ситуация
    для актов сверки "мы продавец / контрагент покупатель" и наоборот.
    По умолчанию mirror_sign=False: суммы сравниваются "как есть" (подходит,
    когда обе стороны ведут учёт с одинаковым знаком относительно друг друга,
    что чаще встречается в выгрузках 1С, где акт сверки уже сформирован
    в разрезе одного контрагента с обеих сторон).
    """
    a = ours.df.copy()
    b = theirs.df.copy()
    a["used"] = False
    b["used"] = False

    if mirror_sign:
        b_amount = -b["amount"]
    else:
        b_amount = b["amount"]
    b = b.assign(amount_cmp=b_amount)
    a = a.assign(amount_cmp=a["amount"])

    matched_rows = []
    amount_mismatch_rows = []
    doc_mismatch_rows = []

    # Этап 1: точное совпадение по документу + дате + сумме
    for i, ra in a.iterrows():
        if ra["used"]:
            continue
        candidates = b[
            (~b["used"])
            & (b["document_norm"] == ra["document_norm"])
            & (ra["document_norm"] != "")
            & (b["date"] == ra["date"])
            & ((b["amount_cmp"] - ra["amount_cmp"]).abs() <= AMOUNT_TOLERANCE)
        ]
        if len(candidates):
            j = candidates.index[0]
            a.at[i, "used"] = True
            b.at[j, "used"] = True
            matched_rows.append(_pair_row(ra, b.loc[j], "Документ+дата+сумма"))

    # Этап 2: совпадение по дате + сумме (номер документа может отличаться форматом)
    for i, ra in a.iterrows():
        if ra["used"]:
            continue
        candidates = b[
            (~b["used"])
            & (b["date"] == ra["date"])
            & ((b["amount_cmp"] - ra["amount_cmp"]).abs() <= AMOUNT_TOLERANCE)
        ]
        if len(candidates):
            j = candidates.index[0]
            a.at[i, "used"] = True
            b.at[j, "used"] = True
            doc_mismatch_rows.append(_pair_row(ra, b.loc[j], "Дата+сумма (номер документа отличается)"))

    # Этап 3: совпадение по номеру документа (сумма и/или дата отличаются) — расхождение
    for i, ra in a.iterrows():
        if ra["used"] or ra["document_norm"] == "":
            continue
        candidates = b[(~b["used"]) & (b["document_norm"] == ra["document_norm"])]
        if len(candidates):
            j = candidates.index[0]
            a.at[i, "used"] = True
            b.at[j, "used"] = True
            amount_mismatch_rows.append(_pair_row(ra, b.loc[j], "Найден документ, но сумма/дата отличаются"))

    only_ours = a[~a["used"]][["row_no", "date", "document", "debit", "credit", "amount"]].copy()
    only_theirs = b[~b["used"]][["row_no", "date", "document", "debit", "credit", "amount"]].copy()

    matched_df = pd.DataFrame(matched_rows)
    doc_mismatch_df = pd.DataFrame(doc_mismatch_rows)
    amount_mismatch_df = pd.DataFrame(amount_mismatch_rows)

    summary = {
        "Операций у нас": len(ours.df),
        "Операций у контрагента": len(theirs.df),
        "Полностью совпало": len(matched_df),
        "Совпало по сумме, отличается номер документа": len(doc_mismatch_df),
        "Найден документ, но расходится сумма/дата": len(amount_mismatch_df),
        "Есть только у нас": len(only_ours),
        "Есть только у контрагента": len(only_theirs),
        "Оборот по нашим данным (Дебет - Кредит)": round(ours.df["amount"].sum(), 2),
        "Оборот по данным контрагента (Дебет - Кредит)": round(theirs.df["amount"].sum(), 2),
    }

    return ReconciliationResult(
        matched=matched_df,
        doc_mismatch=doc_mismatch_df,
        amount_mismatch=amount_mismatch_df,
        only_ours=only_ours,
        only_theirs=only_theirs,
        summary=summary,
    )


def _pair_row(ra: pd.Series, rb: pd.Series, match_type: str) -> dict:
    return {
        "Дата (мы)": ra["date"].date(),
        "Документ (мы)": ra["document"],
        "Дебет (мы)": ra["debit"],
        "Кредит (мы)": ra["credit"],
        "Дата (контрагент)": rb["date"].date(),
        "Документ (контрагент)": rb["document"],
        "Дебет (контрагент)": rb["debit"],
        "Кредит (контрагент)": rb["credit"],
        "Расхождение суммы": round(ra["amount"] - rb["amount"], 2) if match_type != "Документ+дата+сумма" else 0,
        "Способ сопоставления": match_type,
        "Строка (наш файл)": ra["row_no"],
        "Строка (файл контрагента)": rb["row_no"],
    }
