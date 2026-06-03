"""
Verify all critical dependencies are properly installed
Run with: python verify_installation.py
"""
import sys
import importlib
from pathlib import Path

required_modules = {
    'pandas': 'Data manipulation',
    'openpyxl': 'Modern Excel files (.xlsx)',
    'xlrd': 'Legacy Excel files (.xls)',
    'pyarrow': 'Parquet file support',
    'yaml': 'YAML configuration (PyYAML)',
    'watchdog': 'File system monitoring',
    'numpy': 'Numerical operations'
}

optional_modules = {
    'redis': 'Redis state tracking',
    'sqlalchemy': 'Database DLQ support'
}

def verify_module(module_name, description, required=True):
    try:
        mod = importlib.import_module(module_name)
        version = getattr(mod, '__version__', 'unknown')
        status = "✓"
        print(f"{status} {module_name:15} {version:10} - {description}")
        return True
    except ImportError as e:
        status = "✗" if required else "○"
        print(f"{status} {module_name:15} {'MISSING':10} - {description}")
        return False

print("=" * 70)
print("VERIFYING REQUIRED DEPENDENCIES")
print("=" * 70)
all_ok = True
for module, desc in required_modules.items():
    if not verify_module(module, desc, required=True):
        all_ok = False

print("\n" + "=" * 70)
print("VERIFYING OPTIONAL DEPENDENCIES")
print("=" * 70)
for module, desc in optional_modules.items():
    verify_module(module, desc, required=False)

print("\n" + "=" * 70)
if all_ok:
    print("✓ All required dependencies installed successfully!")
else:
    print("✗ Some required dependencies are missing. Check errors above.")
print("=" * 70)

# Check project structure
required_dirs = ['etl/extract', 'tests/fixtures', 'config', 'data']
print("\nChecking project structure...")
for dir_path in required_dirs:
    path = Path(dir_path)
    if path.exists():
        print(f"✓ {dir_path}")
    else:
        print(f"✗ {dir_path} - MISSING (will be created on first run)")
