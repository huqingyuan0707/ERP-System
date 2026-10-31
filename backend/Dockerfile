FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# 阿里云服务器访问官方 PyPI 很慢，改用阿里云 PyPI 镜像
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ PIP_DEFAULT_TIMEOUT=100
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
