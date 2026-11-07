"""Select source files without mistaking image evidence sidecars for input."""

from pathlib import Path


EXT = {'xml': '.xml', 'xlsx': '.xlsx', 'html': '.html', 'pdf': '.pdf',
       'csv': '.csv', 'json': '.json', 'jsonl': '.jsonl'}
IMAGE_EXT = {
    'image': {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp', '.gif'},
    'img': {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp', '.gif'},
    'jpg': {'.jpg', '.jpeg'}, 'jpeg': {'.jpg', '.jpeg'},
    'tif': {'.tif', '.tiff'}, 'tiff': {'.tif', '.tiff'},
    'png': {'.png'}, 'bmp': {'.bmp'}, 'webp': {'.webp'}, 'gif': {'.gif'},
}
SKIP_PARTS = ('attachment', 'profile', 'image')


def find_source_file(downloads, project_root, list_name, config):
    downloads, project_root = Path(downloads), Path(project_root)
    kind = str(config.get('file_type', '')).strip().lower()
    image_extensions = IMAGE_EXT.get(kind)
    local = config.get('local_path')
    if local and (project_root / local).is_file():
        path = project_root / local
        if image_extensions and path.suffix.lower() not in image_extensions:
            raise ValueError('Configured image source local_path is not an image file.')
        return path
    extension = EXT.get(kind, '')
    source = config.get('source_name', '')
    candidates = []
    for base in (downloads / source / list_name, downloads / list_name):
        if base.is_dir():
            for path in base.rglob('*' if image_extensions else f'*{extension}'):
                if not path.is_file():
                    continue
                if image_extensions:
                    # Inspect only subfolders relative to this source root; a
                    # user/source folder named "images" is not an attachment.
                    subfolders = path.relative_to(base).parts[:-1]
                    if path.suffix.lower() not in image_extensions or any(
                        'attachment' in part.lower() or 'profile' in part.lower()
                        for part in subfolders
                    ):
                        continue
                elif any(part in str(path).lower() for part in SKIP_PARTS):
                    continue
                candidates.append(path)
    for pattern in (f'{list_name}*{extension}', f'*{list_name}*{extension}'):
        candidates.extend(path for path in downloads.glob(pattern) if path.is_file()
                          and (not image_extensions or path.suffix.lower() in image_extensions))
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
