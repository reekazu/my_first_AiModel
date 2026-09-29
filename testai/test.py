import torch
import json
import re
import fitz

from PIL import Image
from qwen_vl_utils import process_vision_info

from transformers import (
    AutoProcessor,
    Qwen3VLForConditionalGeneration
)

MODEL_NAME = "Qwen/Qwen3-VL-2B-Instruct"
IMAGE_PATH = r"C:/testai/page_1.png"
OUTPUT_JSON = 'recognized_act.json'

print("CUDA:", torch.cuda.is_available())

if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))

print("Загрузка модели...")

model = Qwen3VLForConditionalGeneration.from_pretrained(
    MODEL_NAME,
    torch_dtype = "auto",
    device_map = "auto"
)

processor = AutoProcessor.from_pretrained(
    MODEL_NAME
)

print("Модель загружена!")

# PROMPT

prompt = r"""
Ты занимаешься распознаванием актов сверки взаимных расчетов.

На изображении находится таблица акта сверки с ДВУМЯ сторонами.

Левая сторона имеет колонки:

Дата | Документ | Дебет | Кредит

ПРАВАЯ сторона имеет колонки:

Дата | Документ | Дебет | Кредит

ВАЖНО:

1. Нужно распознать ВСЕ строки таблицы.
2. Левая и правая стороны являются независимыми.
3. Никогда не переносить сумму из одной стороны в другую.
4. Не объединять строки левой и правой стороны.
5. Если ячейка пустая - используй null.
6. Суммы возвращай числами без пробелов и без разделителей тысяч.
7. Десятичный разделитель всегда точка.
8. Например: "40 000 000,00" -> 40000000.00; "430 310,14" -> 430310.14
9. Даты возвращай в формате ДД.ММ.ГГ.
10. Текст документов распознавай максимально точно.
11. Не придумывай данные, которых нет на изображении.
12. сохрани строки "Сальдо начальное", "Обороты за период", "Сальдо конечное".
13. Для строк без даты используй null.
14. Для итоговых строк используй поле "type"

Верни ТОЛЬКО валидный JSON.
Никакого Markdown.
Никаких ```json.
Никаких пояснений до или после JSON.

Формат ответа должен быть строго таким:
{
    "left_company": "название организации",
    "right_company": "название организации",

    "left": {
        "rows": [
            {
                "type":"operation",
                "date": "ДД.ММ.ГГ",
                "document": "текст документа",
                "debit": 0.00,
                "credit": 0.00
            }
        ],
        "opening_balance": {
            "debit": 0.00,
            "credit": 0.00
        },
        "turnover": {
            "debit": 0.00,
            "credit": 0.00
        },
        "closing_balance": {
            "debit": 0.00,
            "credit": 0.00        
        }
    },

    "right": {
        "rows": [
            {
                "type":"operation",
                "date": "ДД.ММ.ГГ",
                "document": "текст документа",
                "debit": 0.00,
                "credit": 0.00
            }
        ],
        "opening_balance": {
            "debit": 0.00,
            "credit": 0.00
        },
        "turnover": {
            "debit": 0.00,
            "credit": 0.00
        },
        "closing_balance": {
            "debit": 0.00,
            "credit": 0.00        
        }
    }
} 

Для обычных операций: "type":"operation" 

Для "Сальдо начальное": не добавляй эту строку в rows, а положи значения в opening_balance.

Для "Обороты за период": не добавляй эту строку в rows, а положи значения в turnover.

Для "Сальдо конечное": не добавляй эту строку в rows, а положи значения в closing_balance.

Если значение отсутствует - null."""

# PROMPT:END

image = Image.open(IMAGE_PATH).convert("RGB")

messages = [
    {
        "role":"user",
        "content": [
            {
                "type":"image",
                "image":image
            },
            {
                "type":"text",
                "text": prompt
            }
        ]
    }
]

text = processor.apply_chat_template(
    messages,
    tokenize = False,
    add_generation_prompt = True
)

image_inputs, video_inputs = process_vision_info(messages)

inputs = processor(
    text = [text],
    images = image_inputs,
    video = video_inputs,
    padding = True,
    return_tensors = 'pt'
)

inputs = inputs.to(model.device)

print("Распознавание таблицы...")

with torch.no_grad():
    generated_ids = model.generate(
        **inputs,
        max_new_tokens = 4096,
        do_sample = False
    )

generated_ids_trimmed = [
    out_ids[len(in_ids):]
    for in_ids, out_ids
    in zip(inputs.input_ids, generated_ids)
]

output_text = processor.batch_decode(
    generated_ids_trimmed,
    skip_special_tokens = True,
    clean_up_tokenization_spaces = False
)[0]

print(f"Ответ Qwen: {output_text}")

try:
    clean_text = output_text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.replace("```json", "", 1)
        clean_text = clean_text.replace("```", "", 1)
    clean_text = clean_text.strip()
    data = json.loads(clean_text)
    print(json.dumps(
        data,
        ensure_ascii = False,
        indent = 2
    ))

    with open(
        "recognized_act.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii = False,
            indent = 2
        )
    print("JSON сохранен в recognized_act.json")

except json.JSONDecodeError as e:
    print("QWEN вернула невалидный JSON")
    print("Ошибка: ", e)