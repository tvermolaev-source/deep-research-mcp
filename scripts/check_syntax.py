"""Проверка синтаксиса всех .py файлов через ast.parse (без импорта зависимостей)."""
import ast
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent.parent
errors = 0
files = 0
for p in root.rglob("*.py"):
    if ".venv" in p.parts or "__pycache__" in p.parts:
        continue
    files += 1
    try:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        print(f"OK   {p.relative_to(root)}")
    except SyntaxError as e:
        errors += 1
        print(f"FAIL {p.relative_to(root)}: {e}")

print(f"\n{files} files checked, {errors} syntax errors")
sys.exit(1 if errors else 0)
