import os
import cv2
import json
import torch
import pymupdf
import numpy as np
import pandas as pd
from PIL import Image
from datetime import datetime
from qwen_vl_utils import process_vision_info
from transformers import AutoProcessor, Qwen2VLForConditionalGeneration # или Qwen3VL...

# ============================================================
# НАСТРОЙКИ
# ============================================================
PDF_PATH = "doc06833320260910055351.pdf"
OUTPUT_EXCEL = "act_sverki.xlsx"
MODEL_NAME = "Qwen/Qwen2-VL-2B-Instruct" # Укажите вашу модель (2B или 7B)

# ============================================================
# 1. PDF -> Изображение
# ============================================================
def pdf_to_image(pdf_path, dpi=300):
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    zoom = dpi / 72
    matrix = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8)
    img = img.reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    else:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img

# ============================================================
# 2. Очистка JSON (пробелы в ключах)
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
# 3. Нормализация данных из JSON
# ============================================================
def normalize_data(raw_data):
    data = strip_keys(raw_data)
    
    # Извлекаем данные из вложенной структуры pages
    if 'pages' in data and len(data['pages']) > 0:
        page_data = data['pages'][0].get('data', {})
    else:
        page_data = data

    left_data = page_data.get('left', {})
    right_data = page_data.get('right', {})

    def norm_row(row):
        key_map = {'Дата': 'date', 'Документ': 'document', 'Дебет': 'debit', 'Кредит': 'credit'}
        res = {}
        for k, v in row.items():
            new_k = key_map.get(k, k)
            if isinstance(v, str) and v.lower() == 'null': res[new_k] = None
            else: res[new_k] = v
        return res

    def clean_ops(rows):
        return [norm_row(r) for r in rows if not any(k in str(r.get('Документ', r.get('document', ''))).lower() for k in ['сальдо', 'оборот', 'итого'])]

    return {
        'left_ops': clean_ops(left_data.get('rows', [])),
        'right_ops': clean_ops(right_data.get('rows', [])),
        'left_open': norm_row(left_data.get('opening_balance', {})),
        'right_open': norm_row(right_data.get('opening_balance', {})),
        'left_turn': norm_row(left_data.get('turnover', {})),
        'right_turn': norm_row(right_data.get('turnover', {})),
        'left_close': norm_row(left_data.get('closing_balance', {})),
        'right_close': norm_row(right_data.get('closing_balance', {}))
    }

# ============================================================
# 4. Сборка Excel
# ============================================================
def build_excel(norm_data, output_file):
    ops = []
    for r in norm_data['left_ops']:
        ops.append({
            "Дата": r.get('date', ''),
            "Документ (Л)": r.get('document', ''),
            "Дебет (Л)": r.get('debit', ''),
            "Кредит (Л)": r.get('credit', ''),
            "Документ (П)": '', "Дебет (П)": '', "Кредит (П)": ''
        })
    for r in norm_data['right_ops']:
        ops.append({
            "Дата": r.get('date', ''),
            "Документ (Л)": '', "Дебет (Л)": '', "Кредит (Л)": '',
            "Документ (П)": r.get('document', ''),
            "Дебет (П)": r.get('debit', ''),
            "Кредит (П)": r.get('credit', '')
        })

    def p_date(d):
        if not d: return datetime.min
        for f in ["%d.%m.%Y", "%d.%m.%y"]:
            try: return datetime.strptime(d, f)
            except: pass
        return datetime.min
    
    ops.sort(key=lambda x: p_date(x["Дата"]))

    final_data = [{
        "Дата": '', "Документ (Л)": 'Сальдо начальное',
        "Дебет (Л)": norm_data['left_open'].get('debit', ''), "Кредит (Л)": norm_data['left_open'].get('credit', ''),
        "Документ (П)": 'Сальдо начальное',
        "Дебет (П)": norm_data['right_open'].get('debit', ''), "Кредит (П)": norm_data['right_open'].get('credit', '')
    }]
    final_data.extend(ops)
    final_data.append({
        "Дата": '', "Документ (Л)": 'Обороты за период',
        "Дебет (Л)": norm_data['left_turn'].get('debit', ''), "Кредит (Л)": norm_data['left_turn'].get('credit', ''),
        "Документ (П)": 'Обороты за период',
        "Дебет (П)": norm_data['right_turn'].get('debit', ''), "Кредит (П)": norm_data['right_turn'].get('credit', '')
    })
    final_data.append({
        "Дата": '', "Документ (Л)": 'Сальдо конечное',
        "Дебет (Л)": norm_data['left_close'].get('debit', ''), "Кредит (Л)": norm_data['left_close'].get('credit', ''),
        "Документ (П)": 'Сальдо конечное',
        "Дебет (П)": norm_data['right_close'].get('debit', ''), "Кредит (П)": norm_data['right_close'].get('credit', '')
    })

    df = pd.DataFrame(final_data)
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Акт сверки')
    print(f"✅ Excel сохранен: {output_file}")

# ============================================================
# MAIN
# ============================================================
def main():
    print("1. Конвертация PDF в изображение...")
    img_np = pdf_to_image(PDF_PATH)
    img_pil = Image.fromarray(cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB))

    print("2. Загрузка модели Qwen-VL...")
    model = Qwen2VLForConditionalGeneration.from_pretrained(MODEL_NAME, torch_dtype="auto", device_map="auto")
    processor = AutoProcessor.from_pretrained(MODEL_NAME)

    PROMPT = """Ты занимаешься распознаванием актов сверки.
Верни ТОЛЬКО валидный JSON без markdown-обёрток.
Формат: {"pages": [{"data": {"left_company": "...", "right_company": "...", "left": {"rows": [{"Дата": "...", "Документ": "...", "Дебет": "...", "Кредит": "..."}], "opening_balance": {...}, "turnover": {...}, "closing_balance": {...}}, "right": {...}}}]}
Используй "null" для пустых ячеек. Суммы и даты оставляй как строки."""

    # ВАЖНО: Ключи без пробелов!
    messages = [
        {"role": "user", "content": [
            {"type": "image", "image": img_pil},
            {"type": "text", "text": PROMPT}
        ]}
    ]

    print("3. Распознавание (инференс)...")
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(text=[text], images=image_inputs, padding=True, return_tensors='pt').to(model.device)

    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=4096)
    
    output_text = processor.batch_decode(generated_ids[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
    
    # Очистка от markdown
    clean_text = output_text.strip().replace("```json", "").replace("```", "").strip()
    
    print("4. Парсинг и сохранение в Excel...")
    try:
        json_data = json.loads(clean_text)
        norm_data = normalize_data(json_data)
        build_excel(norm_data, OUTPUT_EXCEL)
    except Exception as e:
        print(f"❌ Ошибка при обработке JSON: {e}")
        print("Сырой ответ модели:", output_text)

if __name__ == "__main__":
    main()