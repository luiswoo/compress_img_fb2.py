import os
import sys
import base64
from io import BytesIO
from PIL import Image
from lxml import etree

def compress_image(image_data, max_size=800, quality=85):
    """Сжимает и масштабирует изображение."""
    with Image.open(BytesIO(image_data)) as img:
        # Логируем исходный размер изображения
        print(f"Исходный размер изображения: {img.size}")

        # Проверяем, есть ли у изображения прозрачность
        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)

        # Масштабируем изображение, если большая сторона больше max_size
        width, height = img.size
        if max(width, height) > max_size:
            ratio = max_size / max(width, height)
            new_size = (int(width * ratio), int(height * ratio))
            print(f"Масштабируем изображение до {new_size}")
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        else:
            print("Изображение не требует масштабирования")

        # Обрабатываем изображения с прозрачностью
        if has_alpha:
            print("Обнаружена прозрачность. Конвертируем в PNG и сжимаем с потерями.")
            # Конвертируем в RGBA, если это еще не сделано
            img = img.convert("RGBA")
            
            # Уменьшаем количество цветов с помощью quantize
            img = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
            
            # Сохраняем изображение в формате PNG с оптимизацией
            output_buffer = BytesIO()
            img.save(output_buffer, format='PNG', optimize=True)
            return output_buffer.getvalue(), "image/png"
        else:
            # Конвертируем изображение в RGB, если оно в формате, который не поддерживает режим RGB (например, GIF)
            if img.mode != 'RGB':
                print(f"Конвертируем изображение из режима {img.mode} в RGB")
                img = img.convert('RGB')
            
            # Сохраняем изображение в формате JPG с заданным качеством
            output_buffer = BytesIO()
            img.save(output_buffer, format='JPEG', quality=quality)
            print(f"Сжатие изображения с качеством {quality}%")
            return output_buffer.getvalue(), "image/jpeg"

def process_fb2(file_path, max_size=800, quality=85):
    """Обрабатывает FB2 файл, сжимает и конвертирует изображения."""
    # Увеличиваем лимит для текстовых узлов
    parser = etree.XMLParser(huge_tree=True)

    # Парсим FB2 файл
    try:
        tree = etree.parse(file_path, parser=parser)
        root = tree.getroot()
        print("FB2 файл успешно загружен.")
    except Exception as e:
        print(f"Ошибка при загрузке FB2 файла: {e}")
        return

    # Определяем пространство имен FB2
    namespaces = {
        "fb": "http://www.gribuser.ru/xml/fictionbook/2.0"
    }

    # Находим все теги <binary> с учетом пространства имен
    binaries = root.xpath("//fb:binary", namespaces=namespaces)
    if not binaries:
        print("Теги <binary> не найдены. Возможно, файл не содержит изображений.")
        return

    print(f"Найдено тегов <binary>: {len(binaries)}")

    # Обрабатываем каждый тег <binary>
    for binary in binaries:
        image_id = binary.get("id")
        image_type = binary.get("content-type")

        if not image_id or not image_type:
            print(f"Пропуск тега <binary> без атрибутов id или content-type.")
            continue

        print(f"\nОбработка изображения {image_id} (тип: {image_type})")

        # Проверяем, есть ли данные внутри тега <binary>
        if not binary.text:
            print(f"Тег <binary> пустой. Пропуск.")
            continue

        # Проверяем, является ли изображение допустимым типом
        if image_type.startswith("image/"):
            try:
                # Декодируем base64 в бинарные данные
                print(f"Декодируем base64...")
                image_data = base64.b64decode(binary.text)
                print(f"Изображение успешно декодировано из base64.")
            except Exception as e:
                print(f"Ошибка при декодировании base64: {e}")
                continue

            # Сжимаем и масштабируем изображение
            try:
                print(f"Сжимаем и масштабируем изображение...")
                compressed_image_data, new_image_type = compress_image(image_data, max_size, quality)
                print(f"Изображение успешно сжато и масштабировано.")
            except Exception as e:
                print(f"Ошибка при сжатии изображения: {e}")
                continue

            # Кодируем сжатое изображение обратно в base64
            try:
                print(f"Кодируем сжатое изображение в base64...")
                compressed_image_base64 = base64.b64encode(compressed_image_data).decode('ascii')
                binary.text = compressed_image_base64
                binary.set("content-type", new_image_type)
                print(f"Изображение успешно обновлено в FB2 файле.")
            except Exception as e:
                print(f"Ошибка при обновлении тега <binary>: {e}")
        else:
            print(f"Пропуск тега <binary> (неподдерживаемый тип: {image_type})")

    # Определяем путь для сохранения выходного файла
    directory, filename = os.path.split(file_path)
    new_filename = f"compress_{filename}"
    output_path = os.path.join(directory, new_filename)

    # Сохраняем измененный FB2 файл
    try:
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        print(f"\nФайл успешно сохранен как: {output_path}")
    except Exception as e:
        print(f"Ошибка при сохранении файла: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python compress_image_fb2.py <input_fb2_file>")
        sys.exit(1)

    input_fb2_file = sys.argv[1]

    # Проверяем, существует ли файл
    if not os.path.isfile(input_fb2_file):
        print(f"Ошибка: файл '{input_fb2_file}' не найден.")
        sys.exit(1)

    process_fb2(input_fb2_file)
