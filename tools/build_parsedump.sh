#!/bin/sh
# Build the token-stream oracle against a pinned MicroPython tree.
#
#   tools/build_parsedump.sh /path/to/micropython-v1.29.0 [out]
#
# Reuses the mpy-cross build's objects and generated headers, so the token
# numbering is that build's -- which is what we want to compare against.
set -e
TREE="$1"
OUT="${2:-$(dirname "$0")/../build/parsedump}"
if [ -z "$TREE" ]; then echo "usage: $0 MICROPYTHON_TREE [out]" >&2; exit 2; fi
B="$TREE/mpy-cross/build"
if [ ! -d "$B/py" ]; then
    echo "error: $B/py not found -- build mpy-cross first (make -C mpy-cross)" >&2
    exit 2
fi
mkdir -p "$(dirname "$OUT")"
# Every mpy-cross object except its main(), which we replace.
OBJS=$(find "$B" -name '*.o' ! -name 'main.o' | sort)
exec ${CC:-gcc} -O1 -std=gnu99 \
    -I "$TREE" -I "$B" -I "$TREE/mpy-cross" \
    -DNDEBUG \
    -o "$OUT" "$(dirname "$0")/parsedump.c" $OBJS -lm
