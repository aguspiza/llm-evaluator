import os
import re


def resolve_env_vars(obj):
    """Recursively resolve ${ENV_VAR} patterns in strings."""
    if isinstance(obj, str):
        def replacer(match):
            var_name = match.group(1)
            value = os.environ.get(var_name)
            if value is None:
                return match.group(0)  # leave ${VAR} as-is if not set
            return value
        return re.sub(r'\$\{(\w+)\}', replacer, obj)
    elif isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_env_vars(item) for item in obj]
    return obj


def load_file_content(path, base_dir=None):
    """Load text content from a file path, relative to base_dir if provided."""
    if base_dir and not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read().strip()
