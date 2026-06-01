import os
import sys
import base64
from io import BytesIO
from PIL import Image
from lxml import etree
import random

# ==================== НАСТРОЙКИ ОБРАБОТКИ ====================
CONFIG = {
    "max_size": 800,
    "min_size": 600,
    "quality": 85,
    "tolerance": 10,
    "sample_size": 1000,
}
# =============================================================

def is_color_image(img, tolerance=None, sample_size=None):
    if tolerance is None:
        tolerance = CONFIG["tolerance"]
    if sample_size is None:
        sample_size = CONFIG["sample_size"]

    if img.mode in ('L', 'LA'):
        return False

    if img.mode == 'P' and img.palette:
        palette = img.getpalette()
        colors = []
        for i in range(0, len(palette), 3):
            r, g, b = palette[i:i+3]
            colors.append((r, g, b))
        for r, g, b in colors:
            if abs(r - g) > tolerance or abs(r - b) > tolerance or abs(g - b) > tolerance:
                return True
        return False

    if img.mode in ('RGB', 'RGBA'):
        pixels = list(img.getdata())
        if len(pixels) > sample_size:
            pixels = random.sample(pixels, sample_size)
        for pixel in pixels:
            r, g, b = pixel[:3]
            if abs(r - g) > tolerance or abs(r - b) > tolerance or abs(g - b) > tolerance:
                return True
        return False

    return False

def compress_image(image_data):
    max_size = CONFIG["max_size"]
    min_size = CONFIG["min_size"]
    quality = CONFIG["quality"]

    with Image.open(BytesIO(image_data)) as img:
        print(f"Исходный размер изображения: {img.size}")

        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
        width, height = img.size

        ratio = 1.0
        if max(width, height) > max_size:
            ratio = max_size / max(width, height)
        if min(width * ratio, height * ratio) > min_size:
            if width > height:
                ratio2 = min_size / height
            else:
                ratio2 = min_size / width
            ratio = min(ratio, ratio2)

        if ratio < 1.0:
            new_size = (int(width * ratio), int(height * ratio))
            print(f"Масштабируем изображение до {new_size}")
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        else:
            print("Изображение не требует масштабирования")

        final_width, final_height = img.size
        print(f"Финальный размер: {final_width}x{final_height}")

        if has_alpha:
            print("Обнаружена прозрачность.")
            if is_color_image(img):
                print("Изображение цветное. Конвертируем в JPG с белым фоном.")
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'RGBA':
                    background.paste(img, mask=img.split()[3])
                else:
                    img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[3])
                img = background
            else:
                print("Изображение монохромное. Сохраняем как PNG.")
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                img = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
                output_buffer = BytesIO()
                img.save(output_buffer, format='PNG', optimize=True)
                return output_buffer.getvalue(), "image/png"
        else:
            print("Прозрачности нет.")

        if img.mode != 'RGB':
            print(f"Конвертируем в RGB")
            img = img.convert('RGB')

        output_buffer = BytesIO()
        img.save(output_buffer, format='JPEG', quality=quality)
        print(f"Сжатие JPEG качество {quality}%")
        return output_buffer.getvalue(), "image/jpeg"

def process_fb2(file_path, output_path=None):
    """
    Обрабатывает FB2 файл.
    Если output_path указан, сохраняет туда (новый файл).
    Если output_path is None, перезаписывает исходный файл.
    """
    parser = etree.XMLParser(huge_tree=True)

    try:
        tree = etree.parse(file_path, parser=parser)
        root = tree.getroot()
        print(f"Загружен: {file_path}")
    except Exception as e:
        print(f"Ошибка загрузки: {e}")
        return False

    namespaces = {
        "fb": "http://www.gribuser.ru/xml/fictionbook/2.0",
        "l": "http://www.w3.org/1999/xlink"
    }

    binaries = root.xpath("//fb:binary", namespaces=namespaces)
    images = root.xpath("//fb:image", namespaces=namespaces)

    if not binaries:
        print("Нет тегов <binary> → нет изображений.")
        return False

    print(f"Найдено <binary>: {len(binaries)}, </td>: {len(images)}")

    id_mapping = {}

    for binary in binaries:
        image_id = binary.get("id")
        image_type = binary.get("content-type")

        if not image_id or not image_type:
            print("Пропуск: нет id или content-type")
            continue

        print(f"\nОбработка {image_id} ({image_type})")

        if not binary.text:
            print("Пустой тег")
            continue

        if image_type.startswith("image/"):
            try:
                image_data = base64.b64decode(binary.text)
            except Exception as e:
                print(f"Ошибка декодирования base64: {e}")
                continue

            try:
                compressed_data, new_type = compress_image(image_data)
            except Exception as e:
                print(f"Ошибка сжатия: {e}")
                continue

            try:
                binary.text = base64.b64encode(compressed_data).decode('ascii')
                binary.set("content-type", new_type)

                old_ext = image_id.split('.')[-1].lower()
                new_ext = new_type.split('/')[-1].lower()
                if old_ext != new_ext:
                    new_id = f"{image_id.rsplit('.', 1)[0]}.{new_ext}"
                    binary.set("id", new_id)
                    id_mapping[image_id] = new_id
                    print(f"ID изменён: {image_id} → {new_id}")
                else:
                    id_mapping[image_id] = image_id

                print("Обновлено")
            except Exception as e:
                print(f"Ошибка обновления: {e}")
        else:
            print(f"Пропуск: неподдерживаемый тип {image_type}")

    # Обновляем ссылки в тегах image
    for img_tag in images:
        href = img_tag.get(f"{{{namespaces['l']}}}href")
        if href and href.startswith("#"):
            old_id = href[1:]
            if old_id in id_mapping:
                new_href = f"#{id_mapping[old_id]}"
                img_tag.set(f"{{{namespaces['l']}}}href", new_href)
                print(f"Обновлена ссылка: {href} → {new_href}")

    # Сохранение
    try:
        if output_path is None:
            # Перезапись оригинала (безопасная: временный файл)
            temp_path = file_path + ".tmp"
            tree.write(temp_path, encoding="utf-8", xml_declaration=True)
            os.replace(temp_path, file_path)
            print(f"\nФайл перезаписан: {file_path}")
        else:
            tree.write(output_path, encoding="utf-8", xml_declaration=True)
            print(f"\nФайл сохранён как: {output_path}")
        return True
    except Exception as e:
        print(f"Ошибка сохранения: {e}")
        return False

def process_directory(root_dir):
    """Рекурсивно обрабатывает все .fb2 в каталоге с ЗАМЕНОЙ оригинала."""
    success = 0
    errors = 0
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.lower().endswith(".fb2"):
                file_path = os.path.join(dirpath, filename)
                print(f"\n{'='*60}\nОбработка (замена): {file_path}\n{'='*60}")
                if process_fb2(file_path, output_path=None):
                    success += 1
                else:
                    errors += 1
    print(f"\nСтатистика: успешно {success}, ошибок {errors}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python compress_fb2.py <файл.fb2>                 # создаст compress_файл.fb2")
        print("  python compress_fb2.py <каталог>                 # рекурсивно заменит все .fb2")
        sys.exit(1)

    path = sys.argv[1]

    if os.path.isfile(path) and path.lower().endswith(".fb2"):
        # Обработка одного файла → сохраняем как compress_имя
        directory, filename = os.path.split(path)
        new_filename = f"compress_{filename}"
        output_path = os.path.join(directory, new_filename)
        print(f"Обработка одного файла: {path}")
        print(f"Результат будет сохранён в: {output_path}")
        process_fb2(path, output_path=output_path)

    elif os.path.isdir(path):
        print(f"Рекурсивная обработка каталога с заменой оригиналов: {path}")
        process_directory(path)

    else:
        print(f"Ошибка: '{path}' не является .fb2 файлом или каталогом.")
        sys.exit(1)
