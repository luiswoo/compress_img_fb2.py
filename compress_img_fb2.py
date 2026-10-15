import os
import sys
import argparse
import base64
from io import BytesIO
from PIL import Image
from lxml import etree
import random

# ==================== НАСТРОЙКИ ПО УМОЛЧАНИЮ ====================
# Каждый параметр можно переопределить из командной строки (см. --help).
# Подробное описание и примеры: COLOR_SETTINGS.md
#
# max_size:      максимальный размер по длинной стороне (px)
# min_size:      минимальный размер по короткой стороне (px)
# quality:       качество JPEG (1-100)
# colors:        цветов для PNG авто-выбора (1-256). None — только JPEG
# mono_quantize: метод для монохромных с прозрачностью
#                "LA"            — grayscale+alpha без потерь, макс. качество
#                "LIBIMAGEQUANT" — лучшее квантование (если доступно в Pillow)
#                "FASTOCTREE"    — быстрый, всегда доступен
#                "MEDIANCUT"     — только для непрозрачных
# mono_colors:   цветов при квантовании монохромных (значимо только если
#                mono_quantize != "LA")
# tolerance:     порог определения цветности (разница R/G/B). Поднимите,
#                если серая картинка определяется как цветная (шум JPEG)
# sample_size:   сколько пикселей проверять для определения цветности
CONFIG = {
    "max_size": 800,
    "min_size": 600,
    "quality": 85,
    "colors": 128,
    "mono_quantize": "LA",
    "mono_colors": 32,    # 32 - 32 оттенка серого (для чб)
    "tolerance": 15,      # 3 - строго чб, 15 градаций серого для грязного чб конвертиронного и другого формата
    "sample_size": 5000,
}
# ================================================================

MONO_QUANTIZE_CHOICES = ["LA", "LIBIMAGEQUANT", "FASTOCTREE", "MEDIANCUT"]

# Проверяем доступность libimagequant в Pillow
_HAS_LIBIMAGEQUANT = hasattr(Image.Quantize, 'LIBIMAGEQUANT')

# Рабочая конфигурация: CONFIG + переопределения из командной строки
CFG = dict(CONFIG)


def _get_quantize_method(preferred, has_alpha=False):
    """
    Возвращает метод квантования с учётом доступности библиотек.
    Для прозрачных изображений MEDIANCUT недоступен.
    """
    if preferred == "LA":
        return None, "LA"  # Особый режим, не квантование

    # Для прозрачных: MEDIANCUT не работает
    if has_alpha and preferred == "MEDIANCUT":
        if _HAS_LIBIMAGEQUANT:
            return Image.Quantize.LIBIMAGEQUANT, "LIBIMAGEQUANT (MEDIANCUT недоступен для RGBA)"
        return Image.Quantize.FASTOCTREE, "FASTOCTREE (MEDIANCUT недоступен для RGBA)"

    # Пробуем LIBIMAGEQUANT если запрошена
    if preferred == "LIBIMAGEQUANT":
        if _HAS_LIBIMAGEQUANT:
            return Image.Quantize.LIBIMAGEQUANT, "LIBIMAGEQUANT"
        else:
            return Image.Quantize.FASTOCTREE, "FASTOCTREE (libimagequant не найдена)"

    # Остальные методы
    method = getattr(Image.Quantize, preferred, Image.Quantize.FASTOCTREE)
    return method, preferred


def is_color_image(img, tolerance=None, sample_size=None):
    """Определяет, является ли изображение цветным или монохромным."""
    if tolerance is None:
        tolerance = CFG["tolerance"]
    if sample_size is None:
        sample_size = CFG["sample_size"]

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


def _save_jpeg(img, quality):
    """Сохраняет изображение в JPEG с заданным качеством."""
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=quality, optimize=True)
    return buf.getvalue(), "image/jpeg"


def _save_png8(img, colors, method=Image.Quantize.FASTOCTREE):
    """Сохраняет изображение в PNG с палитрой (PNG-8)."""
    if img.mode != 'P':
        img = img.quantize(colors=colors, method=method)
    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue(), "image/png"


def _save_png_la(img):
    """Сохраняет изображение как grayscale+alpha PNG (без квантования)."""
    if img.mode == 'RGBA':
        img = img.convert('LA')
    elif img.mode != 'LA':
        img = img.convert('LA')
    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue(), "image/png"


def compress_image(image_data, colors=None, quality=None):
    """
    Сжимает изображение: масштабирует, обрабатывает прозрачность,
    конвертирует в JPEG или PNG с заданной палитрой.

    Если colors указан (число) — генерирует JPEG и PNG-N, выбирает меньший.
    Если colors is None — только JPEG.
    """
    max_size = CFG["max_size"]
    min_size = CFG["min_size"]
    if quality is None:
        quality = CFG["quality"]

    with Image.open(BytesIO(image_data)) as img:
        print(f"Исходный размер изображения: {img.size}")

        # Определяем наличие альфа-канала
        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
        width, height = img.size

        # --- Масштабирование ---
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

        # --- Обработка прозрачности ---
        if has_alpha:
            print("Обнаружена прозрачность.")
            if is_color_image(img):
                print("Изображение цветное. Конвертируем в RGB с белым фоном.")
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'RGBA':
                    background.paste(img, mask=img.split()[3])
                else:
                    img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[3])
                img = background
            else:
                print("Изображение монохромное.")
                mono_mode = CFG.get("mono_quantize", "FASTOCTREE")
                mono_colors = CFG.get("mono_colors", 256)

                if mono_mode == "LA":
                    print("Сохраняем как grayscale+alpha PNG (LA).")
                    return _save_png_la(img)
                else:
                    method, method_name = _get_quantize_method(mono_mode, has_alpha=True)
                    if method_name != mono_mode:
                        print(f"{method_name}.")
                    print(f"Сохраняем как PNG-{mono_colors} ({method_name.split()[0]}).")
                    if img.mode != 'RGBA':
                        img = img.convert('RGBA')
                    return _save_png8(img, colors=mono_colors, method=method)
        else:
            print("Прозрачности нет.")

        if img.mode != 'RGB':
            print(f"Конвертируем в RGB")
            img = img.convert('RGB')

        # --- Сохранение ---
        jpeg_data, jpeg_type = _save_jpeg(img, quality)
        print(f"JPEG q={quality}: {len(jpeg_data)/1024:.1f} КБ")

        # Если указан colors: сравниваем JPEG vs PNG-N, выбираем меньший
        if colors is not None:
            png_data, png_type = _save_png8(img, colors=colors)
            print(f"PNG-{colors}: {len(png_data)/1024:.1f} КБ")
            if len(png_data) < len(jpeg_data):
                print(f"Выбран: PNG-{colors} ({len(png_data)/1024:.1f} КБ)")
                return png_data, png_type
            else:
                print(f"Выбран: JPEG q={quality} ({len(jpeg_data)/1024:.1f} КБ)")

        return jpeg_data, jpeg_type


def process_fb2(file_path, output_path=None, colors=None, quality=None):
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

    print(f"Найдено <binary>: {len(binaries)}, <image>: {len(images)}")

    id_mapping = {}
    total_saved = 0
    total_orig = 0

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

            orig_size = len(image_data)
            total_orig += orig_size

            try:
                compressed_data, new_type = compress_image(image_data, colors=colors, quality=quality)
            except Exception as e:
                print(f"Ошибка сжатия: {e}")
                continue

            new_size = len(compressed_data)
            saved = orig_size - new_size
            total_saved += saved
            print(f"Экономия: {saved/1024:+.1f} КБ ({saved/orig_size*100:+.1f}%)")

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

        print(f"\n{'='*60}")
        print(f"Итого исходный вес изображений: {total_orig/1024/1024:.1f} МБ")
        print(f"Итого новый вес изображений:    {(total_orig-total_saved)/1024/1024:.1f} МБ")
        print(f"Общая экономия:                 {total_saved/1024/1024:+.1f} МБ ({total_saved/total_orig*100:+.1f}%)")
        print(f"{'='*60}")
        return True
    except Exception as e:
        print(f"Ошибка сохранения: {e}")
        return False


def process_directory(root_dir, colors=None, quality=None):
    """Рекурсивно обрабатывает все .fb2 в каталоге с ЗАМЕНОЙ оригинала."""
    success = 0
    errors = 0
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.lower().endswith(".fb2"):
                file_path = os.path.join(dirpath, filename)
                print(f"\n{'='*60}\nОбработка (замена): {file_path}\n{'='*60}")
                if process_fb2(file_path, output_path=None, colors=colors, quality=quality):
                    success += 1
                else:
                    errors += 1
    print(f"\nСтатистика: успешно {success}, ошибок {errors}")


EXAMPLES = """\
примеры:
  # Базовая обработка одного файла (результат — compress_<имя>.fb2)
  python compress_img_fb2.py book.fb2

  # Серые линии/градиенты определяются как цветные (шум JPEG) —
  # поднимаем порог цветности:
  python compress_img_fb2.py book.fb2 --tolerance 15

  # Ч/б иллюстрации с градациями серого — максимальное качество:
  python compress_img_fb2.py book.fb2 --mono-quantize LA

  # ...или компактнее, с незаметным бандингом:
  python compress_img_fb2.py book.fb2 --mono-quantize FASTOCTREE --mono-colors 128

  # Цветные картинки: авто-выбор JPEG q85 vs PNG-128
  python compress_img_fb2.py book.fb2 -c 128

  # Только JPEG, качество 60:
  python compress_img_fb2.py book.fb2 -q 60 -c 0

  # Каталог целиком (оригиналы будут заменены!):
  python compress_img_fb2.py ./library --tolerance 15 --mono-quantize LA

  # Полный набор: размер, качество, все цветовые параметры
  python compress_img_fb2.py book.fb2 --max-size 1024 --min-size 800 \
      -q 80 -c 256 --tolerance 12 --mono-quantize FASTOCTREE --mono-colors 256

замечания:
  -c 0 отключает авто-выбор PNG (только JPEG) для цветных изображений.
  --mono-colors имеет смысл только если --mono-quantize не LA.
  Все параметры, не указанные в командной строке, берутся из CONFIG
  в начале скрипта. Подробности: COLOR_SETTINGS.md
"""


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="compress_img_fb2.py",
        description="Сжатие и масштабирование изображений в FB2 файлах.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", nargs="?", help="Путь до .fb2 файла или каталога")
    p.add_argument("-q", "--quality", type=int, default=None, metavar="N",
                   help="качество JPEG 1-100 (по умолчанию: %(default)s из CONFIG)")
    p.add_argument("-c", "--colors", type=int, default=None, metavar="N",
                   help="цветов для PNG авто-выбора 1-256, 0 = только JPEG (по умолчанию из CONFIG)")
    p.add_argument("--mono-quantize", choices=MONO_QUANTIZE_CHOICES, default=None,
                   metavar="M",
                   help="метод для монохромных с прозрачностью: LA, LIBIMAGEQUANT, "
                        "FASTOCTREE, MEDIANCUT (по умолчанию из CONFIG)")
    p.add_argument("--mono-colors", type=int, default=None, metavar="N",
                   help="цветов при квантовании монохромных 2-256, "
                        "значимо только если --mono-quantize не LA (по умолчанию из CONFIG)")
    p.add_argument("--max-size", type=int, default=None, metavar="N",
                   help="максимум по длинной стороне, px (по умолчанию из CONFIG)")
    p.add_argument("--min-size", type=int, default=None, metavar="N",
                   help="минимум по короткой стороне, px (по умолчанию из CONFIG)")
    p.add_argument("--tolerance", type=int, default=None, metavar="N",
                   help="порог определения цветности: разница R/G/B в тонах 0-255. "
                        "Поднимите (12-20), если серая картинка считается цветной (по умолчанию из CONFIG)")
    p.add_argument("--sample-size", type=int, default=None, metavar="N",
                   help="сколько пикселей проверять при определении цветности (по умолчанию из CONFIG)")
    return p


def apply_overrides(args):
    """Накладывает значения из командной строки на CONFIG."""
    if args.quality is not None:
        if not (1 <= args.quality <= 100):
            print("Ошибка: -q / --quality должен быть от 1 до 100"); sys.exit(1)
        CFG["quality"] = args.quality
    if args.colors is not None:
        if args.colors == 0:
            CFG["colors"] = None
        elif 1 <= args.colors <= 256:
            CFG["colors"] = args.colors
        else:
            print("Ошибка: -c / --colors должен быть от 1 до 256 (или 0 = выкл)"); sys.exit(1)
    if args.mono_quantize is not None:
        CFG["mono_quantize"] = args.mono_quantize
    if args.mono_colors is not None:
        if not (2 <= args.mono_colors <= 256):
            print("Ошибка: --mono-colors должен быть от 2 до 256"); sys.exit(1)
        CFG["mono_colors"] = args.mono_colors
    if args.max_size is not None:
        if args.max_size < 1:
            print("Ошибка: --max-size должен быть >= 1"); sys.exit(1)
        CFG["max_size"] = args.max_size
    if args.min_size is not None:
        if args.min_size < 1:
            print("Ошибка: --min-size должен быть >= 1"); sys.exit(1)
        CFG["min_size"] = args.min_size
    if args.tolerance is not None:
        if not (0 <= args.tolerance <= 255):
            print("Ошибка: --tolerance должен быть от 0 до 255"); sys.exit(1)
        CFG["tolerance"] = args.tolerance
    if args.sample_size is not None:
        if args.sample_size < 1:
            print("Ошибка: --sample-size должен быть >= 1"); sys.exit(1)
        CFG["sample_size"] = args.sample_size


def print_effective_config():
    print("Эффективные настройки:")
    print(f"  max_size      = {CFG['max_size']} px")
    print(f"  min_size      = {CFG['min_size']} px")
    print(f"  quality       = {CFG['quality']} (JPEG)")
    print(f"  colors        = {CFG['colors']} (PNG авто-выбор, None = только JPEG)")
    print(f"  mono_quantize = {CFG['mono_quantize']}")
    print(f"  mono_colors   = {CFG['mono_colors']} (значимо, если mono_quantize != LA)")
    print(f"  tolerance     = {CFG['tolerance']} (порог цветности)")
    print(f"  sample_size   = {CFG['sample_size']} пикселей")
    print(f"  libimagequant = {'доступна' if _HAS_LIBIMAGEQUANT else 'НЕ найдена'}")


if __name__ == "__main__":
    args = build_arg_parser().parse_args()

    if not args.path:
        build_arg_parser().print_help()
        sys.exit(1)

    apply_overrides(args)

    path = args.path
    colors = CFG["colors"]
    quality = CFG["quality"]

    if os.path.isfile(path) and path.lower().endswith(".fb2"):
        directory, filename = os.path.split(path)
        output_path = os.path.join(directory, f"compress_{filename}")
        print(f"Обработка одного файла: {path}")
        print(f"Результат будет сохранён в: {output_path}")
        print_effective_config()
        mode = f"JPEG q={quality}"
        if colors is not None:
            mode += f" vs PNG-{colors} (авто-выбор)"
        print(f"Режим (цветные): {mode}")
        process_fb2(path, output_path=output_path, colors=colors, quality=quality)

    elif os.path.isdir(path):
        print(f"Рекурсивная обработка каталога с заменой оригиналов: {path}")
        print_effective_config()
        mode = f"JPEG q={quality}"
        if colors is not None:
            mode += f" vs PNG-{colors} (авто-выбор)"
        print(f"Режим (цветные): {mode}")
        process_directory(path, colors=colors, quality=quality)

    else:
        print(f"Ошибка: '{path}' не является .fb2 файлом или каталогом.")
        sys.exit(1)
