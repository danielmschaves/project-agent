FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Keep the venv outside /app so host volume mounts never shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --all-extras --no-install-project

COPY . .
RUN uv sync --frozen --all-extras

ENTRYPOINT ["uv", "run"]
CMD ["python", "-m", "pipeline", "--help"]
