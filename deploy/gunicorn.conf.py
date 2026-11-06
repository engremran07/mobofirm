"""Gunicorn settings for the production container (override through environment)."""

import multiprocessing
import os

# Gunicorn reads lower-case module globals; they are published from one mapping so
# the names stay valid settings while the module keeps constant-style naming.
_SETTINGS = {
    "bind": os.getenv("GUNICORN_BIND", "0.0.0.0:8000"),
    "workers": int(
        os.getenv("GUNICORN_WORKERS", str(min(multiprocessing.cpu_count() * 2 + 1, 9)))
    ),
    "threads": int(os.getenv("GUNICORN_THREADS", "2")),
    "worker_class": "gthread",
    "timeout": int(os.getenv("GUNICORN_TIMEOUT", "60")),
    "graceful_timeout": 30,
    "keepalive": 5,
    "max_requests": 1000,
    "max_requests_jitter": 100,
    "worker_tmp_dir": "/dev/shm",
    # Logs go to stdout/stderr so the container runtime can collect and rotate them.
    "accesslog": "-",
    "errorlog": "-",
    "loglevel": os.getenv("GUNICORN_LOG_LEVEL", "info"),
    # The app is only reachable through the nginx container on the private network,
    # which sets X-Forwarded-Proto/For. Narrow this if the app is ever published
    # directly.
    "forwarded_allow_ips": os.getenv("GUNICORN_FORWARDED_ALLOW_IPS", "*"),
}
globals().update(_SETTINGS)
