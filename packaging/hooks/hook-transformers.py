# Adapted from PyInstaller hooks-contrib's Transformers hook (GPL-2.0-or-later).
# https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/_pyinstaller_hooks_contrib/stdhooks/hook-transformers.py
from PyInstaller.utils.hooks import (
    copy_metadata,
    get_module_attribute,
    is_module_satisfies,
    logger,
)

datas = []

# Preserve the upstream hook's metadata collection for installed optional dependencies.
try:
    dependencies = get_module_attribute(
        "transformers.dependency_versions_table",
        "deps",
    )
except Exception:
    logger.warning(
        "hook-transformers: failed to query dependency_versions_table.deps",
        exc_info=True,
    )
    dependencies = {}

for dependency_name, dependency_req in dependencies.items():
    if not is_module_satisfies(dependency_req):
        continue
    try:
        datas += copy_metadata(dependency_name)
    except Exception:
        pass

# Transformers 5.18's auto_docstring indexes absolute source paths at import time.
# Loading real .py files also preserves its lazy model discovery and source inspection.
module_collection_mode = "py"
