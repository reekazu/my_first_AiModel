import json
import re
import torch
import fitz

from PIL import Image
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

MODEL_NAME = 'Qwen/Qwen3-VL-2B-Instruct'
PDF_PATH = r'C:/testai/doc03491520260929112832.pdf'
OUTPUT_JSON = 'recognized_act.json'

DPI = 220

PROMPT = r"""
Ты занимаешься распознаванием актов сверки взаимных расчетов.

На изображении находится одна страница акта сверки.

В акте может находиться таблица с ДВУМЯ сторонами.

ЛЕВАЯ сторона:
Дата | Документ | Дебет | Кредит

ПРАВАЯ сторона:
Дата | Документ | Дебет | Кредит

ТВОЯ ЗАДАЧА:

Максимально точно перепиши информацию, которая ФИЗИЧЕСКИ ВИДНА на изображении.

НЕ ПРИДУМЫВАЙ данные.

НЕ ВЫЧИСЛЯЙ отсутствующие данные.

НЕ ПЕРЕНОСИ данные из левой части в правую.

НЕ ПЕРЕНОСИ данные из правой части в левую.

Если ячейка пустая - используй null.

Если на этой странице нет операций в правой части - rows должен быть [].

ОЧЕНЬ ВАЖНО:

Не создавай фиктивные строки только потому, что слева есть строки.

Каждая сторона должна содержать только реално существующие на изображении операции этой стороны

---------------------------------
СУММЫ
---------------------------------

Суммы преобразуй в числа.

Например:

"40 000 000,00" -> 40000000.00
"430 310,14" -> 430310.14
"1 271 479,58" -> 1271479.58

Не используй пробелы в числах.

Не используй запятую как десятичный разделитель.

----------------------------------
ДАТЫ
----------------------------------

Если дата указан как: 01.01.26
можно вернуть: 01.01.2026

----------------------------------
ОБОРОТЫ И САЛЬДО
----------------------------------

Отдельно распознавай:

Сальдо начальное

Обороты за период

Сальдо конечное

Не помещай эти строки в rows.

-----------------------------------
СТРУКТУРА JSON
-----------------------------------

Верни строго такой JSON:
{
    "left_company":null,
    "right_company":null,

    "left":{
        "rows":[],

        "opening_balance": {
            "debit":null,
            "credit":null
        },

        "turnover":{
            "debit":null,
            "credit":null
        }
    },

    "right":{
        "rows":[],

        "opening_balance":{
            "debit":null,
            "credit":null
        },

        "turnover":{
            "debit":null,
            "credit":null
        },

        "closing_balance":{
            "debit":null,
            "credit":null
        }
    }
}
-----------------------------------------
ВАЖНО
-----------------------------------------

Если какого-либо значения на этой странице нет, оставь null.

Если операции на этой странице отсутствуют, верни пустой массив rows.

Верни ТОЛЬКО JSON
НЕ используй:
```json

Не добавляй никаких пояснений до или после JSON. """

def clean_json_text(text):
    text = text.strip()

    text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)

    text = re.sub(r"\s*```$", "", text)

    text = text.strip()

    return text

print("Загрузка модели Qwen3-VL...")

model = Qwen3VLForConditionalGeneration.from_pretrained(MODEL_NAME, torch_dtype="auto", device_map="auto")

processor = AutoProcessor.from_pretrained(MODEL_NAME)

print("Модель загружена.")

if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
    print("VRAM:", round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2), "GB")
else:
    print("ВНИММАНИЕ: CUDA НЕ ОБНАРУЖЕНА.")

print("Открвыаем PDF:")
print(PDF_PATH)

pdf = fitz.open(PDF_PATH)

print("Количество страниц: ", len(pdf))

all_pages = []

for page_number in range(len(pdf)):
    print()
    print("=" * 60)
    print(f"СТРАНИЦА {page_number + 1} / {len(pdf)}")
    print("=" * 60)

    page = pdf[page_number]

    zoom = DPI/72

    matrix = fitz.Matrix(zoom, zoom)

    pix = page.get_pixmap(matrix=matrix, alpha=False)

    image_path = (f"page_{page_number + 1}.png")

    pix.save(image_path)

    print("Изображение сохранено: ", image_path)

    image = Image.open(image_path).convert("RGB")

    print("Размер изображения: ", image.size)

    messages = [
        {
            "role":"user",
            "content":[
                {
                    "type":"image",
                    "image":image
                },
                {
                    "type":"text",
                    "text":PROMPT
                }
            ]
        }
    ]

    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    inputs = processor(
        text = [text],
        images = [image],
        padding = True,
        return_tensors ="pt"
    )

    inputs = inputs.to(model.device)

    print("Qwen распознает страницу...")

    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=4096, do_sample=False)


    generated_ids_trimmed = [
        out_ids[len(in_ids):]

        for in_ids, out_ids

        in zip(
            inputs.input_ids, generated_ids
        )
    ]

    output_text=processor.batch_decode(generated_ids_trimmed, skip_special_tokens=True,clean_up_tokenization_spaces=False)[0]

    print()
    print("Ответ Qwen:")
    print("-"*60)
    print(output_text)
    print("-"*60)

    clean_text = clean_json_text(output_text)

    try:
        page_data = json.loads(clean_text)
        print("JSON страницы успешно распознаны")
    except json.JSONDecodeError as e:
        print()
        print("Ошибка JSON")
        print("Страница: ", page_number + 1)
        print("Ошибка: ", e)
        print()
        print("Полученный текст: ")
        print(clean_text)

        with open(
            f"page_{page_number + 1}_ERROR.txt",
            "w",
            encoding="utf-8"
        ) as f:
            f.write(output_text)
    continue

all_pages.append(
    {
        "page":page_number+1,
        "data":page_data
    }
)

# pdf.close()

result = {"pages":all_pages}

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(
        result,
        f,
        ensure_ascii=False,
        indent=2
    )
print()
print("="*60)
print("Готово")
print("="*60)
print("Результат сохранён: ", OUTPUT_JSON)
print("Обработано страниц: ", len(all_pages), "/", len(pdf) if 'pdf' in locals() else "?")