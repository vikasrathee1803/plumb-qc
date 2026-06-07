# CI runner image. The CI gate is the plumb exit code:
# 0 passing, 1 REVIEW, 2 BLOCKED, 3 tool or connection error.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY plumb ./plumb
COPY rules ./rules

RUN pip install --no-cache-dir .

# Auth in CI is key-pair: mount the key and set the connection profile,
# never bake either into the image.
ENTRYPOINT ["plumb"]
CMD ["--help"]
