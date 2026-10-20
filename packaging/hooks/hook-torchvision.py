"""Include torchvision's stable ABI libraries, loaded through torch.ops.load_library."""

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs(
    "torchvision", search_patterns=["*.so", "*.pyd", "*.dll", "*.dylib"]
)
module_collection_mode = "pyz+py"
