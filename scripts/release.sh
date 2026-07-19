#!/usr/bin/env bash
# Создание нового релиза: bump version → tag → push → GitHub Actions зальёт образ.
#
# Использование:
#   ./scripts/release.sh patch   # 0.1.0 → 0.1.1
#   ./scripts/release.sh minor   # 0.1.0 → 0.2.0
#   ./scripts/release.sh major   # 0.1.0 → 1.0.0

set -euo pipefail

BUMP="${1:-patch}"

# Берём текущую версию из pyproject.toml
CURRENT=$(grep -oE 'version = "[0-9]+\.[0-9]+\.[0-9]+"' pyproject.toml | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
if [[ -z "$CURRENT" ]]; then
  echo "❌ Could not read current version from pyproject.toml" >&2
  exit 1
fi

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
case "$BUMP" in
  patch) PATCH=$((PATCH + 1)) ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  *) echo "Usage: $0 {patch|minor|major}"; exit 1 ;;
esac

NEW="$MAJOR.$MINOR.$PATCH"
echo "📦 Bumping $CURRENT → $NEW"

# Обновляем pyproject.toml
sed -i.bak "s/version = \"$CURRENT\"/version = \"$NEW\"/" pyproject.toml
rm -f pyproject.toml.bak

# Также обновляем __init__.py
sed -i.bak "s/__version__ = \"$CURRENT\"/__version__ = \"$NEW\"/" src/deep_research/__init__.py
rm -f src/deep_research/__init__.py.bak

git add pyproject.toml src/deep_research/__init__.py
git commit -m "chore: release v$NEW"
git tag -a "v$NEW" -m "Release v$NEW"

echo "✅ Tagged v$NEW. Run 'git push origin main --tags' to trigger the release workflow."
