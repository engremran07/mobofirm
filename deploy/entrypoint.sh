#!/bin/sh
# Container entrypoint. Schema changes and static collection are opt-in so that
# starting a container never modifies a database unless the operator asked for it.
set -eu

if [ "${RUN_COLLECTSTATIC:-false}" = "true" ]; then
    python manage.py collectstatic --noinput
fi

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
