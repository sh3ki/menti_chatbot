#!/usr/bin/env bash
set -euo pipefail

echo "== Render build: upgrade pip and install requirements =="
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

echo "\n== Project setup tasks (best-effort) =="
# Run resource image setup when present
if [ -f setup_resource_images.py ]; then
  echo "Running setup_resource_images.py"
  python setup_resource_images.py || true
fi

# Database migration steps (best-effort detection)
if [ -f alembic.ini ]; then
  echo "Detected alembic.ini — running Alembic migrations"
  alembic upgrade head || true
elif [ -d migrations ]; then
  if command -v flask >/dev/null 2>&1; then
    echo "Detected migrations directory and flask — running 'flask db upgrade'"
    flask db upgrade || true
  else
    echo "Migrations directory found but 'flask' CLI not available — skipping"
  fi
elif [ -f manage.py ]; then
  echo "Detected manage.py — running Django-style migrations"
  python manage.py migrate || true
else
  echo "No DB migration tool detected — skipping migration step"
fi

echo "\n== Build complete =="
