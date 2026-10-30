# 企业ERP系统 · Docker Compose 部署配置（V1.0）

服务清单：nginx / frontend / backend / celery-worker / celery-beat / postgres / redis / minio

## 1. docker-compose.yml
```yaml
version: "3.9"
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: erp
      POSTGRES_USER: erp
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U erp"]
      interval: 10s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data
    restart: unless-stopped

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
    volumes:
      - miniodata:/data
    ports:
      - "9000:9000"
      - "9001:9001"
    restart: unless-stopped

  backend:
    build: ./backend
    env_file: .env
    depends_on:
      - postgres
      - redis
    command: gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 -w 4
    restart: unless-stopped

  celery-worker:
    build: ./backend
    env_file: .env
    command: celery -A app.tasks worker -l info
    depends_on:
      - redis
      - postgres
    restart: unless-stopped

  celery-beat:
    build: ./backend
    env_file: .env
    command: celery -A app.tasks beat -l info
    depends_on:
      - redis
      - postgres
    restart: unless-stopped

  frontend:
    build: ./frontend
    restart: unless-stopped

  nginx:
    image: nginx:1.27
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./nginx/certs:/etc/nginx/certs:ro
    depends_on:
      - frontend
      - backend
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
  miniodata:
```

## 2. backend/Dockerfile
```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 3. frontend/Dockerfile
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
```

## 4. nginx/nginx.conf
```nginx
server {
    listen 80;
    server_name _;

    client_max_body_size 20m;

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 5. .env 示例
```ini
DB_PASSWORD=change_me
MINIO_USER=minioadmin
MINIO_PASSWORD=change_me
DATABASE_URL=postgresql+asyncpg://erp:change_me@postgres:5432/erp
REDIS_URL=redis://redis:6379/0
JWT_SECRET=change_me
```

## 6. 部署步骤
```bash
cp .env.example .env          # 修改密码与密钥
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

## 7. 生产建议
- PostgreSQL 独立数据卷 + 每日 `pg_dump` 备份；Redis 开启持久化
- MinIO 存储附件与导出文件
- Prometheus + Grafana 监控，Sentry 收集前后端异常
- Nginx 统一入口并配置 HTTPS
