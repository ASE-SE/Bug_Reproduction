FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# 1. 安装系统依赖
RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    git curl unzip zip build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. 安装 Python 依赖
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# 3. 复制代码
COPY . .

# 4. 创建目录
RUN mkdir -p repos results

CMD ["python3", "main.py"]