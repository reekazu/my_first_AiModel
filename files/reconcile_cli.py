# -*- coding: utf-8 -*-
"""
CLI: сверка акта сверки между нашей выгрузкой из 1С и данными контрагента.

Использование:
    python reconcile_cli.py наш_файл.xlsx файл_контрагента.xlsx -o результат.xlsx

Дополнительно:
    --our-sheet, --their-sheet   имя/номер листа, если не первый
    --mirror                     использовать зеркальное сравнение сумм
                                  (наш дебет сверяется с кредитом контрагента)
"""

import argparse
import sys

from reconcile_core import load_side, reconcile
from report_builder import build_report


def main():
    parser = argparse.ArgumentParser(description="Сверка акта сверки (наши данные vs контрагент)")
    parser.add_argument("our_file", help="Путь к нашей выгрузке из 1С (.xlsx)")
    parser.add_argument("their_file", help="Путь к выгрузке/акту контрагента (.xlsx)")
    parser.add_argument("-o", "--output", default="результат_сверки.xlsx", help="Файл результата")
    parser.add_argument("--our-sheet", default=0, help="Лист в нашем файле (имя или номер)")
    parser.add_argument("--their-sheet", default=0, help="Лист в файле контрагента (имя или номер)")
    parser.add_argument(
        "--mirror",
        action="store_true",
        help="Сравнивать зеркально: наш дебет = кредит контрагента и наоборот",
    )
    args = parser.parse_args()

    try:
        ours = load_side(args.our_file, "Наша организация", sheet_name=_sheet(args.our_sheet))
        theirs = load_side(args.their_file, "Контрагент", sheet_name=_sheet(args.their_sheet))
    except Exception as e:
        print(f"Ошибка при чтении файлов: {e}", file=sys.stderr)
        sys.exit(1)

    result = reconcile(ours, theirs, mirror_sign=args.mirror)
    build_report(result, args.output, ours.label, theirs.label)

    print("Готово. Сводка:")
    for k, v in result.summary.items():
        print(f"  {k}: {v}")
    print(f"\nОтчёт сохранён в: {args.output}")


def _sheet(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


if __name__ == "__main__":
    main()
