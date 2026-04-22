FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# 1. 安装系统依赖
# 包含 JDK 17 (SWE-bench Java 主流版本), git, maven, 以及构建 Python 库所需的依赖
RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    openjdk-17-jdk \
    maven \
    git curl unzip zip build-essential \
    && rm -rf /var/lib/apt/lists/*

# 2. 手动安装 Gradle 8.5 (适配大多数现代 Java 项目)
RUN curl -L https://services.gradle.org/distributions/gradle-8.5-bin.zip -o gradle.zip && \
    unzip gradle.zip -d /opt && \
    ln -s /opt/gradle-8.5/bin/gradle /usr/bin/gradle && \
    rm gradle.zip

WORKDIR /app

# 3. 安装 Python 依赖 (增加了 datasets 支持)
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# 4. 复制代码
COPY . .

# 5. 创建目录
RUN mkdir -p repos results

CMD ["python3", "main.py"]