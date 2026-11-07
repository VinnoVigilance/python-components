"""Resolve list evidence paths without collecting other captures in the day."""

from pathlib import Path
from string import Formatter


def list_attachment_paths(source_file_path, rule):
    directory = Path(source_file_path).resolve().parent
    if 'local_files' not in rule:
        # Preserve existing directory-based list attachment behavior.
        legacy_directory = directory / rule['local_directory']
        return sorted(p for p in legacy_directory.iterdir() if p.is_file()) if legacy_directory.is_dir() else []
    if 'local_directory' in rule:
        raise ValueError('Choose local_files or local_directory, not both.')
    templates = rule['local_files']
    if not isinstance(templates, list) or not templates:
        raise ValueError('local_files must be a nonempty list of relative paths.')
    required = rule.get('required', True)
    if type(required) is not bool:
        raise ValueError('Attachment required must be a boolean.')
    source = Path(source_file_path)
    values = {'source_stem': source.stem, 'source_filename': source.name}
    paths = []
    for template in templates:
        if not isinstance(template, str) or not template.strip():
            raise ValueError('Attachment path template must be a nonempty string.')
        for _, field, spec, conversion in Formatter().parse(template):
            if field is not None and (field not in values or spec or conversion):
                raise ValueError('Unsupported attachment path placeholder.')
        relative = Path(template.format(**values))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Attachment paths must stay inside the source directory.')
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory):
            raise ValueError('Attachment paths must stay inside the source directory.')
        if not path.is_file():
            if required:
                raise FileNotFoundError(f'Required source evidence is missing: {path}')
            continue
        if path not in paths:
            paths.append(path)
    return paths
