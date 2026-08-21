"""WebUI 本地图片素材识别（参考 SenluoFlow 的路径识别方式）。

用户粘贴一个目录路径后，自动识别其中的产品目录，并按固定优先级收集图片，
作为视频生成的本地素材。支持批量：一个产品目录 = 一条视频。
"""

import os

# 与 app.models.const.FILE_TYPE_IMAGES 保持一致并补充 webp / 大写形式由调用方统一。
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# 产品目录内素材子目录的优先级（按目录名前缀匹配，input 优先）。
_PRIORITY_DIR_PREFIXES = (
    "input",
    "output_size_image",
    "output_package_image",
    "output_sku",
    "output_userguide",
)

# 集合根目录下应跳过的非产品目录。
_SKIP_DIR_NAMES = {".deepseek", "output_video", "moban", "__pycache__", ".git"}
# 集合根目录下按素材子目录命名规则出现、但并非产品的目录前缀。
_SKIP_DIR_PREFIXES = ("input", "output_", "output-")


def is_image_file(path: str) -> bool:
    """按扩展名判断是否为支持的图片文件。"""
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def _list_image_files(directory: str) -> list[str]:
    """返回 directory 下（仅一层、不递归）按文件名排序的图片路径。"""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names if is_image_file(name)]


def collect_product_images(
    product_dir: str,
    include_userguide: bool = False,
    max_images: int = 0,
) -> list[str]:
    """按展示优先级收集一个产品目录的图片：

    1. input/（主图）
    2. output_size_image/（尺寸图）
    3. output_package_image/（包装图）
    4. output_sku*/（各 SKU 图）
    5. output_userguide/（说明书图，默认排除）
    6. 产品目录根目录的图片
    7. 其它子目录中的图片（排除 output_video/ 与隐藏目录）

    max_images=0 表示不限制数量。
    """
    images: list[str] = []
    seen: set[str] = set()

    def _append(paths) -> None:
        for path in paths:
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                images.append(path)

    try:
        entries = sorted(os.listdir(product_dir))
    except OSError:
        return []

    dirs: list[tuple[str, str]] = []
    root_images: list[str] = []
    for name in entries:
        full = os.path.join(product_dir, name)
        if os.path.isdir(full):
            dirs.append((name, full))
        elif is_image_file(full):
            root_images.append(full)

    def _is_priority(name: str) -> bool:
        return any(name == prefix or name.startswith(prefix) for prefix in _PRIORITY_DIR_PREFIXES)

    # 固定优先级子目录。
    for prefix in _PRIORITY_DIR_PREFIXES:
        for name, full in dirs:
            if prefix == "output_userguide" and not include_userguide:
                continue
            if name == prefix or name.startswith(prefix):
                _append(_list_image_files(full))

    # 根目录图片。
    _append(root_images)

    # 其它子目录（如 output_sku1_ar_1-1 等派生目录），输出视频与隐藏目录除外。
    for name, full in dirs:
        if name.startswith(".") or name.startswith("output_video"):
            continue
        if _is_priority(name):
            continue
        _append(_list_image_files(full))

    if max_images and len(images) > max_images:
        images = images[:max_images]
    return images


def recognize_products(
    base_path: str,
    include_userguide: bool = False,
    max_images: int = 0,
) -> list[dict]:
    """识别 base_path 下的产品目录（参考 SenluoFlow 的 _collect_work_dirs 语义）。

    规则：
    - base_path 必须存在且为目录。
    - 若 base_path 有子目录且其中至少一个含图片 → 每个这样的子目录是一个产品。
    - 否则若 base_path 自身含图片 → base_path 本身是一个产品。
    - 否则返回空列表。

    返回形如 [{"name", "path", "image_count", "images"}] 的列表。
    """
    if not base_path or not os.path.isdir(base_path):
        return []

    try:
        children = sorted(os.listdir(base_path))
    except OSError:
        return []

    product_dirs = []
    for name in children:
        full = os.path.join(base_path, name)
        if not os.path.isdir(full):
            continue
        if name in _SKIP_DIR_NAMES or name.startswith("."):
            continue
        if name.startswith(_SKIP_DIR_PREFIXES):
            # input/output_* 是素材子目录的命名规则，出现在合集根目录时是
            # 历史遗留或整理产物，不应被当作产品目录。
            continue
        images = collect_product_images(
            full,
            include_userguide=include_userguide,
            max_images=max_images,
        )
        if images:
            product_dirs.append(
                {
                    "name": name,
                    "path": full,
                    "image_count": len(images),
                    "images": images,
                }
            )

    if product_dirs:
        return product_dirs

    images = collect_product_images(
        base_path,
        include_userguide=include_userguide,
        max_images=max_images,
    )
    if images:
        base_name = os.path.basename(base_path.rstrip("\\/")) or base_path
        return [
            {
                "name": base_name,
                "path": base_path,
                "image_count": len(images),
                "images": images,
            }
        ]
    return []
