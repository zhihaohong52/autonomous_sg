# Stage 1: build the React frontend (optional; skipped if frontend/package.json is absent)
FROM node:20-slim AS web
COPY . /src
RUN if [ -f /src/frontend/package.json ]; then \
      cd /src/frontend && if [ -f package-lock.json ]; then npm ci; else npm install; fi && npm run build && cp -r dist /dist; \
    else \
      mkdir -p /dist; \
    fi

# Stage 2: Python API + built frontend
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY atm/ ./atm/
COPY data/ ./data/
COPY eval/ ./eval/
COPY web/ ./web/
COPY --from=web /dist /app/frontend/dist
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user
ENV PORT=7860
EXPOSE 7860
CMD ["uvicorn", "atm.server:app", "--host", "0.0.0.0", "--port", "7860"]
