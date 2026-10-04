FROM python:3.12-slim-bookworm AS desktop
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DISPLAY=:99 HOME=/home/bridge LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential linux-libc-dev chromium xvfb x11vnc openbox xclip novnc supervisor tini fonts-noto-cjk \
    fonts-dejavu-core xterm curl ca-certificates git ripgrep fd-find nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix \
    && useradd --create-home --uid 1000 bridge \
    && mkdir -p /data/workspace /data/state /data/profile /home/bridge/.config/openbox \
    && chown -R bridge:bridge /data /home/bridge
WORKDIR /app
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
ARG BRIDGE_PYTHON_EXTRAS=desktop
RUN pip install --no-cache-dir -c constraints.txt ".[${BRIDGE_PYTHON_EXTRAS}]"
COPY docker/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/start-browser.sh /usr/local/bin/start-browser
RUN chmod +x /usr/local/bin/start-browser
USER bridge
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=10s --timeout=5s --start-period=90s CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]

# Only the explicit Fly target bootstraps root-owned fresh volume directories.
# The launcher drops to bridge before supervisor or any application is executed.
FROM desktop AS fly
USER root
COPY docker/fly-entrypoint.py /usr/local/bin/fly-entrypoint.py
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/usr/local/bin/fly-entrypoint.py"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]

# Preserve the original non-root behavior for docker build / Compose by default.
FROM desktop AS local
