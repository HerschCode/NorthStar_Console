FROM python:3.12-slim

WORKDIR /app

# Install deps first, separate from copying source -- lets Docker cache this layer
# across rebuilds when only application code changes, not dependencies. --timeout=100
# (pip's default is 15s) and --retries=5 found necessary by actually running this
# build for the first time: mlflow's large dependency chain (matplotlib, grpcio, etc.)
# hit pip's default read timeout against files.pythonhosted.org on a real network,
# failing the build outright rather than retrying -- no local test could have caught
# this since it only manifests against a real, imperfect network connection.
# --retries alone wasn't enough -- pip's own retry only covers a fresh HTTP request,
# not a connection that dies mid-stream (this build's actual failure mode: SSLError
# "record layer failure" partway through a large wheel download, a real Docker
# Desktop/WSL2 networking quirk on this host, confirmed by it happening at a
# different random point on every attempt, sometimes past the 10-minute mark).
# Wrapping the whole install in a shell retry loop covers that case too -- pip skips
# re-downloading packages already successfully installed in an earlier iteration.
#
# The first version of this loop had a real bug, caught by actually running the
# build and testing the resulting image (`docker run ... python -c "import mlflow"`
# failed with ModuleNotFoundError even though `docker build` reported success): a
# bare `for ... do CMD && break; sleep 5; done` loop's own exit status is whatever
# its LAST executed command returned, not CMD's -- if every attempt failed, the
# loop's last action was `sleep 5` (exit 0), so Docker considered the RUN step
# successful and produced an image silently missing every package that hadn't
# finished installing yet. Fixed with an explicit success flag and a final check
# that actually fails the build if every attempt failed, instead of shipping a
# broken image that merely LOOKED like it built correctly.
COPY requirements.txt .
RUN success=0; \
    for i in 1 2 3 4 5 6 7 8; do \
      if pip install --no-cache-dir --default-timeout=100 --retries=5 -r requirements.txt; then \
        success=1; break; \
      fi; \
      echo "pip install attempt $i failed, retrying..."; sleep 5; \
    done; \
    if [ "$success" -ne 1 ]; then echo "pip install failed after all attempts"; exit 1; fi

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Mirrors operations-assistant's Dockerfile pattern (same author, same reasoning) --
# a genuine health check against /health (src/api/routes.py's `check_connection()`),
# not just "is the process up". Reads $PORT (falling back to 8000 locally) since
# Render/Cloud Run assign the listen port dynamically via that env var.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, httpx; httpx.get(f'http://localhost:{os.environ.get(\"PORT\", 8000)}/health', timeout=3).raise_for_status()" || exit 1

# Shell form (not exec-form JSON array) so $PORT is expanded at container start --
# an exec-form CMD would pass the literal string "$PORT" to uvicorn instead of its
# value. Falls back to 8000 (this project's local default) when PORT isn't set.
# Docker warns shell-form CMD isn't JSON-args-recommended (OS signal handling) --
# accepted deliberately here since $PORT expansion needs the shell either way; sh -c
# is the explicit, self-documenting version of the same trade-off exec form can't avoid.
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
