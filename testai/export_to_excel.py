import json
import pandas as pd
from datetime import datetime

# ============================================================
# 1. Очистка пробелов в ключах и значениях JSON
# ============================================================
def strip_keys(obj):
    if isinstance(obj, dict):
        return {k.strip(): strip_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [strip_keys(i) for i in obj]
    elif isinstance(obj, str):
        return obj.strip()
    return obj

# ============================================================
# 2. Чтение JSON
# ============================================================
with open('recognized_act.json', 'r', encoding='utf-8') as f:
    raw_data = json.load(f)

data = strip_keys(raw_data)

# ============================================================
# 3. Извлечение данных из структуры pages
# ============================================================
if 'pages' in data and len(data['pages']) > 0:
    page_data = data['pages'][0].get('data', {})
else:
    page_data = data  # fallback для плоской структуры

left_data = page_data.get('left', {})
right_data = page_data.get('right', {})

print(f"Левая компания: {page_data.get('left_company', 'не указано')}")
print(f"Правая компания: {page_data.get('right_company', 'не указано')}")

# ============================================================
# 4. Нормализация ключей (русские -> английские)
# ============================================================
def normalize_row(row):
    """Переводит русские ключи в английские."""
    key_map = {
        'Дата': 'date',
        'Документ': 'document',
        'Дебет': 'debit',
        'Кредит': 'credit',
        'type': 'type'
    }
    normalized = {}
    for k, v in row.items():
        new_key = key_map.get(k, k)
        # Строка "null" -> None
        if isinstance(v, str) and v.lower() == 'null':
            normalized[new_key] = None
        else:
            normalized[new_key] = v
    return normalized

def normalize_balance(balance):
    """Нормализует балансы."""
    if not balance:
        return {'debit': None, 'credit': None}
    key_map = {'Дебет': 'debit', 'Кредит': 'credit'}
    normalized = {}
    for k, v in balance.items():
        new_key = key_map.get(k, k)
        if isinstance(v, str) and v.lower() == 'null':
            normalized[new_key] = None
        else:
            normalized[new_key] = v
    return normalized

# ============================================================
# 5. Фильтрация итоговых строк из rows
# ============================================================
def clean_rows(rows):
    cleaned = []
    for row in rows:
        doc = str(row.get("document", "")).lower()
        if any(keyword in doc for keyword in ["сальдо", "оборот", "итого", "всего"]):
            continue
        cleaned.append(row)
    return cleaned

# ============================================================
# 6. Применяем нормализацию
# ============================================================
left_rows = clean_rows([normalize_row(r) for r in left_data.get('rows', [])])
right_rows = clean_rows([normalize_row(r) for r in right_data.get('rows', [])])

opening_balance_left = normalize_balance(left_data.get('opening_balance', {}))
opening_balance_right = normalize_balance(right_data.get('opening_balance', {}))
turnover_left = normalize_balance(left_data.get('turnover', {}))
turnover_right = normalize_balance(right_data.get('turnover', {}))
closing_balance_left = normalize_balance(left_data.get('closing_balance', {}))
closing_balance_right = normalize_balance(right_data.get('closing_balance', {}))

# ============================================================
# 7. Сборка всех операций
# ============================================================
all_operations = []

for row in left_rows:
    all_operations.append({
        "Дата": row.get('date', ''),
        "Документ (Левая сторона)": row.get('document', ''),
        "Дебет (Л)": row.get('debit', ''),
        "Кредит (Л)": row.get('credit', ''),
        "Документ (Правая сторона)": '',
        "Дебет (П)": '',
        "Кредит (П)": ''
    })

for row in right_rows:
    all_operations.append({
        "Дата": row.get('date', ''),
        "Документ (Левая сторона)": '',
        "Дебет (Л)": '',
        "Кредит (Л)": '',
        "Документ (Правая сторона)": row.get('document', ''),
        "Дебет (П)": row.get('debit', ''),
        "Кредит (П)": row.get('credit', '')
    })

# ============================================================
# 8. Сортировка по дате (поддержка обоих форматов)
# ============================================================
def parse_date(d):
    if not d:
        return datetime.min
    for fmt in ["%d.%m.%Y", "%d.%m.%y"]:
        try:
            return datetime.strptime(d, fmt)
        except ValueError:
            continue
    return datetime.min

all_operations.sort(key=lambda x: parse_date(x["Дата"]))

# ============================================================
# 9. Формируем итоговую таблицу
# ============================================================
merged_data = []

# Сальдо начальное
merged_data.append({
    "Дата": '',
    "Документ (Левая сторона)": 'Сальдо начальное',
    "Дебет (Л)": opening_balance_left.get('debit', ''),
    "Кредит (Л)": opening_balance_left.get('credit', ''),
    "Документ (Правая сторона)": 'Сальдо начальное',
    "Дебет (П)": opening_balance_right.get('debit', ''),
    "Кредит (П)": opening_balance_right.get('credit', '')
})

# Все операции
merged_data.extend(all_operations)

# Обороты за период
merged_data.append({
    "Дата": '',
    "Документ (Левая сторона)": 'Обороты за период',
    "Дебет (Л)": turnover_left.get('debit', ''),
    "Кредит (Л)": turnover_left.get('credit', ''),
    "Документ (Правая сторона)": 'Обороты за период',
    "Дебет (П)": turnover_right.get('debit', ''),
    "Кредит (П)": turnover_right.get('credit', '')
})

# Сальдо конечное
merged_data.append({
    "Дата": '',
    "Документ (Левая сторона)": 'Сальдо конечное',
    "Дебет (Л)": closing_balance_left.get('debit', ''),
    "Кредит (Л)": closing_balance_left.get('credit', ''),
    "Документ (Правая сторона)": 'Сальдо конечное',
    "Дебет (П)": closing_balance_right.get('debit', ''),
    "Кредит (П)": closing_balance_right.get('credit', '')
})

# ============================================================
# 10. Сохранение в Excel
# ============================================================
df = pd.DataFrame(merged_data)
output_file = "act_sverki.xlsx"

with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
    df.to_excel(writer, index=False, sheet_name='Акт сверки')
    
    workbook = writer.book
    worksheet = writer.sheets['Акт сверки']
    worksheet.column_dimensions['A'].width = 12
    worksheet.column_dimensions['B'].width = 40
    worksheet.column_dimensions['C'].width = 18
    worksheet.column_dimensions['D'].width = 18
    worksheet.column_dimensions['E'].width = 40
    worksheet.column_dimensions['F'].width = 18
    worksheet.column_dimensions['G'].width = 18

print(f"\n✅ Готово! Файл сохранён: '{output_file}'")
print(f"Всего строк с операциями: {len(all_operations)}")