"""
Логика автоматической сверки взаиморасчётов.

Идея: обе стороны (наш экспорт из 1С и акт от контрагента) приводятся
к единому формату — по одной строке на операцию с полями:
    date        — дата операции
    doc_number  — номер документа (накладной, платёжки и т.п.)
    amount      — сумма со знаком (+ дебет/приход, - кредит/расход)
    description — произвольное описание (для отчёта человеку)

Дальше строки сопоставляются в два прохода:
    1) точное совпадение по нормализованному номеру документа
    2) для того, что не сопоставилось — по дате (+/- допуск в днях)
       и сумме (+/- допуск в копейках/рублях)

Всё, что не нашло пару ни на одном из шагов, попадает в
"расхождения" — это и есть то, что реально интересует бухгалтера.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd


def normalize_doc_number(value) -> str:
    """Приводит номер документа к сравнимому виду: убирает пробелы,
    ведущие нули, знак '№', приводит к верхнему регистру.
    '№ 0000123 от 01.01' и '123' после этого совпадут."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).upper()
    s = re.sub(r"[№#]", "", s)
    s = re.sub(r"\s+", "", s)
    # отрезаем ведущие нули у чисто числовых номеров
    if s.isdigit():
        s = str(int(s))
    return s


def to_number(series: pd.Series) -> pd.Series:
    """Превращает колонку в числа, независимо от того, пришли ли уже
    настоящие числа (из Excel) или строки вроде '150 000,00' (из PDF/OCR,
    где пробел — разделитель разрядов, а запятая — копейки)."""
    def clean(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip().replace("\xa0", "").replace(" ", "")
        if not s:
            return None
        if "," in s and "." in s:
            # последний из двух разделителей — дробная часть
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        return s

    return pd.to_numeric(series.apply(clean), errors="coerce")


def normalize_dataframe(df: pd.DataFrame, column_map: dict) -> pd.DataFrame:
    """Приводит уже загруженный DataFrame (неважно, из Excel, из таблицы
    PDF или из построчного OCR-разбора) к единому формату.

    column_map — соответствие "наше_имя -> имя_колонки_в_df", например:
        {
            "date": "Дата",
            "doc_number": "Документ",
            "debit": "Дебет",
            "credit": "Кредит",
        }
    Если в данных уже есть готовая колонка с суммой со знаком,
    можно передать "amount" вместо "debit"/"credit".
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[column_map["date"]], dayfirst=True, errors="coerce")
    out["doc_number_raw"] = df.get(column_map.get("doc_number"), "")
    out["doc_number"] = out["doc_number_raw"].apply(normalize_doc_number)

    if "amount" in column_map:
        out["amount"] = to_number(df[column_map["amount"]]).fillna(0)
    else:
        debit = to_number(df.get(column_map.get("debit"), pd.Series(dtype=float))).fillna(0)
        credit = to_number(df.get(column_map.get("credit"), pd.Series(dtype=float))).fillna(0)
        out["amount"] = debit - credit

    desc_col = column_map.get("description")
    out["description"] = df[desc_col].astype(str) if desc_col in (df.columns if desc_col else []) else ""

    out = out.dropna(subset=["date"]).reset_index(drop=True)
    out["_row_id"] = out.index
    return out


def load_ledger(path_or_buffer, column_map: dict, sheet_name=0) -> pd.DataFrame:
    """Загружает Excel-файл и приводит его к единому формату.
    Тонкая обёртка над normalize_dataframe для случая 'на входе Excel'."""
    df = pd.read_excel(path_or_buffer, sheet_name=sheet_name)
    return normalize_dataframe(df, column_map)


@dataclass
class ReconciliationResult:
    matched: pd.DataFrame
    mismatched_amount: pd.DataFrame
    only_ours: pd.DataFrame
    only_counterparty: pd.DataFrame
    summary: dict = field(default_factory=dict)


def reconcile(
    ours: pd.DataFrame,
    theirs: pd.DataFrame,
    date_tolerance_days: int = 3,
    amount_tolerance: float = 0.01,
) -> ReconciliationResult:
    ours = ours.copy()
    theirs = theirs.copy()
    ours["_matched"] = False
    theirs["_matched"] = False

    matched_rows = []
    mismatched_rows = []

    # Проход 1: точное совпадение по номеру документа
    for i, o in ours.iterrows():
        if not o["doc_number"]:
            continue
        candidates = theirs[(theirs["doc_number"] == o["doc_number"]) & (~theirs["_matched"])]
        if len(candidates) == 0:
            continue
        t = candidates.iloc[0]
        ours.at[i, "_matched"] = True
        theirs.at[t.name, "_matched"] = True
        diff = round(o["amount"] - t["amount"], 2)
        row = {
            "doc_number": o["doc_number_raw"],
            "date_ours": o["date"], "date_theirs": t["date"],
            "amount_ours": o["amount"], "amount_theirs": t["amount"],
            "diff": diff, "match_type": "по номеру документа",
        }
        (matched_rows if abs(diff) <= amount_tolerance else mismatched_rows).append(row)

    # Проход 2: по дате + сумме, для всего, что не сопоставилось выше
    for i, o in ours[~ours["_matched"]].iterrows():
        window = theirs[
            (~theirs["_matched"])
            & (theirs["date"].sub(o["date"]).abs().dt.days <= date_tolerance_days)
            & (theirs["amount"].sub(o["amount"]).abs() <= amount_tolerance)
        ]
        if len(window) == 0:
            continue
        t = window.iloc[0]
        ours.at[i, "_matched"] = True
        theirs.at[t.name, "_matched"] = True
        matched_rows.append({
            "doc_number": o["doc_number_raw"] or t["doc_number_raw"],
            "date_ours": o["date"], "date_theirs": t["date"],
            "amount_ours": o["amount"], "amount_theirs": t["amount"],
            "diff": round(o["amount"] - t["amount"], 2),
            "match_type": "по дате и сумме",
        })

    only_ours = ours[~ours["_matched"]][["date", "doc_number_raw", "amount", "description"]]
    only_counterparty = theirs[~theirs["_matched"]][["date", "doc_number_raw", "amount", "description"]]

    summary = {
        "Сумма по нашим данным": round(ours["amount"].sum(), 2),
        "Сумма по данным контрагента": round(theirs["amount"].sum(), 2),
        "Расхождение по итогу": round(ours["amount"].sum() - theirs["amount"].sum(), 2),
        "Операций у нас": len(ours),
        "Операций у контрагента": len(theirs),
        "Совпало полностью": len(matched_rows),
        "Совпало, но сумма отличается": len(mismatched_rows),
        "Есть только у нас": len(only_ours),
        "Есть только у контрагента": len(only_counterparty),
    }

    return ReconciliationResult(
        matched=pd.DataFrame(matched_rows),
        mismatched_amount=pd.DataFrame(mismatched_rows),
        only_ours=only_ours.rename(columns={"doc_number_raw": "doc_number"}),
        only_counterparty=only_counterparty.rename(columns={"doc_number_raw": "doc_number"}),
        summary=summary,
    )
