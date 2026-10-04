#!/bin/sh
set -eu
mkdir -p /data/profile /data/workspace
exec chromium --no-sandbox --disable-dev-shm-usage --no-first-run \
  --disable-session-crashed-bubble --disable-infobars \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
  --user-data-dir=/data/profile --window-size=1280,760 --window-position=0,0 \
  about:blank
