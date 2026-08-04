#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py seed_demo_user

exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --access-logfile - \
  --error-logfile -
