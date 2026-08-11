#!/bin/sh
set -eu

mkdir -p "$(dirname "$NOT_CR_CONFIG_PATH")" "$NOT_CR_DOCS_DIR" "$NOT_CR_OUTPUT_DIR"

if [ ! -f "$NOT_CR_CONFIG_PATH" ]; then
    cp /app/config.example.json "$NOT_CR_CONFIG_PATH"
    printf '%s\n' "Initialized $NOT_CR_CONFIG_PATH from config.example.json. Edit it from /config or the mounted volume."
fi

exec "$@"
