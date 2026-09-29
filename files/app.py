# -*- coding: utf-8 -*-
"""
Простой веб-интерфейс для сверки актов (без командной строки).

Запуск:
    streamlit run app.py

Открывает страницу в браузере, где можно загрузить два Excel-файла
и скачать готовый отчёт.
"""

import tempfile
import os

import streamlit as st

from reconcile_core import load_side, reconcile
from report_builder import build_report

st.set_page_config(page_title="Сверка актов с контрагентом", page_icon="🧾")
st.title("🧾 Сверка акта сверки (1С vs контрагент)")
st.write(
    "Загрузите нашу выгрузку из 1С и файл, полученный от контрагента. "
    "Программа найдёт совпадения и расхождения автоматически."
)

col1, col2 = st.columns(2)
with col1:
    our_file = st.file_uploader("Наша выгрузка (1С)", type=["xlsx"], key="our")
with col2:
    their_file = st.file_uploader("Файл контрагента", type=["xlsx"], key="theirs")

mirror = st.checkbox(
    "Зеркальное сравнение (наш дебет = кредит контрагента)",
    value=False,
    help="Включите, если контрагент присылает акт со своей точки зрения "
    "(там, где у нас дебет, у него по этой же операции — кредит).",
)

if st.button("Сверить", type="primary", disabled=not (our_file and their_file)):
    with tempfile.TemporaryDirectory() as tmp:
        our_path = os.path.join(tmp, "our.xlsx")
        their_path = os.path.join(tmp, "theirs.xlsx")
        with open(our_path, "wb") as f:
            f.write(our_file.getbuffer())
        with open(their_path, "wb") as f:
            f.write(their_file.getbuffer())

        try:
            ours = load_side(our_path, "Наша организация")
            theirs = load_side(their_path, "Контрагент")
        except Exception as e:
            st.error(f"Не удалось прочитать файлы: {e}")
            st.stop()

        result = reconcile(ours, theirs, mirror_sign=mirror)
        out_path = os.path.join(tmp, "результат_сверки.xlsx")
        build_report(result, out_path, ours.label, theirs.label)

        st.success("Сверка выполнена")
        st.subheader("Сводка")
        st.table(
            {"Показатель": list(result.summary.keys()), "Значение": list(result.summary.values())}
        )

        diff = (
            result.summary["Оборот по нашим данным (Дебет - Кредит)"]
            - result.summary["Оборот по данным контрагента (Дебет - Кредит)"]
        )
        if abs(diff) > 0.01:
            st.warning(f"Итоговое расхождение оборота: {diff:,.2f}")
        else:
            st.success("Обороты сходятся полностью")

        with open(out_path, "rb") as f:
            st.download_button(
                "⬇️ Скачать полный отчёт (Excel)",
                data=f.read(),
                file_name="результат_сверки.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
