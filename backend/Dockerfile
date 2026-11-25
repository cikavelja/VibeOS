FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.10
COPY pyproject.toml uv.lock README.md .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY app ./app
RUN mkdir /data && chown -R 10001:10001 /data /app
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH"
ENV DATABASE_URL=sqlite:////data/vibeos.db
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
