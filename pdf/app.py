"""
Автосверка актов: наш экспорт из 1С vs акт от контрагента.

Запуск:  streamlit run app.py
"""
import io
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from reconcile import load_ledger, normalize_dataframe, reconcile
from pdf_import import extract_ledger_candidates

st.set_page_config(page_title="Сверка расчётов", layout="wide")
st.title("📊 Автоматическая сверка актов сверки")
st.caption(
    "Загрузите нашу выгрузку из 1С и акт от контрагента (Excel или PDF) — "
    "приложение само сопоставит операции по номеру документа, а затем по "
    "дате и сумме, и покажет только реальные расхождения."
)

col1, col2 = st.columns(2)
with col1:
    st.subheader("1️⃣ Наша выгрузка (1С)")
    our_file = st.file_uploader("Excel или PDF", type=["xlsx", "xls", "pdf"], key="ours")
with col2:
    st.subheader("2️⃣ Акт от контрагента")
    their_file = st.file_uploader("Excel или PDF", type=["xlsx", "xls", "pdf"], key="theirs")


def column_picker(df: pd.DataFrame, prefix: str) -> dict:
    """Даёт пользователю сопоставить свои колонки полям date/doc/amount."""
    cols = list(df.columns)
    st.write("Сопоставьте колонки:")
    date_col = st.selectbox("Дата", cols, key=f"{prefix}_date")
    doc_col = st.selectbox("Номер документа (необязательно)", ["—"] + cols, key=f"{prefix}_doc")

    mode = st.radio(
        "Как считать сумму?",
        ["Одна колонка со знаком", "Отдельно Дебет и Кредит"],
        key=f"{prefix}_mode",
        horizontal=True,
    )
    mapping = {"date": date_col}
    if doc_col != "—":
        mapping["doc_number"] = doc_col

    if mode == "Одна колонка со знаком":
        amount_col = st.selectbox("Сумма", cols, key=f"{prefix}_amount")
        mapping["amount"] = amount_col
    else:
        debit_col = st.selectbox("Дебет / Приход", cols, key=f"{prefix}_debit")
        credit_col = st.selectbox("Кредит / Расход", cols, key=f"{prefix}_credit")
        mapping["debit"] = debit_col
        mapping["credit"] = credit_col
    return mapping


def get_raw_table(uploaded_file, prefix: str):
    """Возвращает "сырой" DataFrame из загруженного файла (Excel или PDF),
    показывая по пути весь UI, специфичный для формата: выбор таблицы
    для PDF с текстовым слоем, редактируемую черновую таблицу для
    сканов/OCR."""
    name = uploaded_file.name.lower()

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)

    # PDF: сохраняем во временный файл — pdftoppm/pdfplumber нужен путь на диске
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    with st.spinner("Разбираю PDF (для скана это может занять время — идёт распознавание текста)…"):
        try:
            result = extract_ledger_candidates(tmp_path)
        except RuntimeError as e:
            Path(tmp_path).unlink(missing_ok=True)
            st.error(str(e))
            st.stop()
    Path(tmp_path).unlink(missing_ok=True)

    if result["method"] == "table":
        st.success(f"Нашёл {len(result['tables'])} таблицу(и) с текстовым слоем — распознавание не потребовалось.")
        if len(result["tables"]) > 1:
            idx = st.selectbox(
                "В PDF несколько таблиц — какая нужна?",
                range(len(result["tables"])),
                format_func=lambda i: f"Таблица {i + 1} ({len(result['tables'][i])} строк)",
                key=f"{prefix}_table_idx",
            )
        else:
            idx = 0
        return result["tables"][idx]

    # Скан / нет текстового слоя — потребовался OCR, или текст есть,
    # но не оформлен как таблица. В обоих случаях — черновой построчный
    # разбор, который ОБЯЗАТЕЛЬНО нужно проверить и поправить руками.
    if result["method"] == "ocr":
        st.warning(
            "В этом PDF нет текстового слоя (похоже на скан) — текст распознан "
            "через OCR. Проверьте таблицу ниже: OCR может путать похожие "
            "символы (0/О, 5/S, разделители разрядов). Поправьте ошибки "
            "прямо в таблице перед сверкой."
        )
    else:
        st.info(
            "Текст в PDF есть, но таблицы с чёткими границами не нашлось — "
            "строки разобраны эвристически по датам и суммам. Проверьте "
            "результат перед сверкой."
        )
    with st.expander("Показать распознанный сырой текст"):
        st.text(result["raw_text"])

    edited = st.data_editor(
        result["draft_rows"],
        use_container_width=True,
        num_rows="dynamic",
        key=f"{prefix}_draft_editor",
    )
    return edited


ours_df = None
theirs_df = None

if our_file is not None:
    with col1:
        raw_ours = get_raw_table(our_file, "ours")
        with st.expander("Предпросмотр", expanded=False):
            st.dataframe(raw_ours.head())
        map_ours = column_picker(raw_ours, "ours")
        ours_df = normalize_dataframe(raw_ours, map_ours)

if their_file is not None:
    with col2:
        raw_theirs = get_raw_table(their_file, "theirs")
        with st.expander("Предпросмотр", expanded=False):
            st.dataframe(raw_theirs.head())
        map_theirs = column_picker(raw_theirs, "theirs")
        theirs_df = normalize_dataframe(raw_theirs, map_theirs)

st.divider()

date_tol = st.slider("Допуск по дате при сопоставлении, дней", 0, 14, 3)
amount_tol = st.number_input("Допуск по сумме, руб.", min_value=0.0, value=0.01, step=0.01)

if st.button("🔍 Сверить", type="primary", disabled=ours_df is None or theirs_df is None):
    result = reconcile(ours_df, theirs_df, date_tolerance_days=date_tol, amount_tolerance=amount_tol)

    st.subheader("Итог")
    summary_cols = st.columns(len(result.summary))
    for c, (k, v) in zip(summary_cols, result.summary.items()):
        c.metric(k, v)

    if len(result.mismatched_amount):
        st.subheader("⚠️ Совпали по документу, но разошлись в сумме")
        st.dataframe(result.mismatched_amount, use_container_width=True)

    if len(result.only_ours):
        st.subheader("📕 Есть только у нас (нет в акте контрагента)")
        st.dataframe(result.only_ours, use_container_width=True)

    if len(result.only_counterparty):
        st.subheader("📗 Есть только у контрагента (нет у нас)")
        st.dataframe(result.only_counterparty, use_container_width=True)

    with st.expander("✅ Полностью совпавшие операции"):
        st.dataframe(result.matched, use_container_width=True)

    # Выгрузка отчёта в Excel
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([result.summary]).T.rename(columns={0: "Значение"}).to_excel(writer, sheet_name="Итог")
        result.mismatched_amount.to_excel(writer, sheet_name="Расхождения по сумме", index=False)
        result.only_ours.to_excel(writer, sheet_name="Только у нас", index=False)
        result.only_counterparty.to_excel(writer, sheet_name="Только у контрагента", index=False)
        result.matched.to_excel(writer, sheet_name="Совпало", index=False)

    st.download_button(
        "⬇️ Скачать отчёт (Excel)",
        data=buffer.getvalue(),
        file_name="отчет_сверки.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
