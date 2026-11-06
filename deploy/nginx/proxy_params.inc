proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
# Appends the address nginx saw; Django (TRUSTED_PROXY_COUNT=1) reads the last entry.
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_read_timeout 60s;
proxy_redirect off;
