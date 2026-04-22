#!/usr/bin/env bash
set -euo pipefail

# prepare_build_cache.sh
# 用途：在单机上一次性准备 Gradle/Maven 发行包与依赖缓存，
# 使多仓库共享，避免每个仓库重复下载。

# --- 可配置部分 ---
GRADLE_VERSION="${GRADLE_VERSION:-8.14}"        # 你要的 gradle 版本
MAVEN_VERSION="${MAVEN_VERSION:-3.9.6}"         # 你要的 maven 版本
REPOS_ROOT="${REPOS_ROOT:-$PWD/repos}"         # 本地仓库目录（脚本会遍历该目录）
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/tmp/build_cache_downloads}"
# --- 结束配置 ---

mkdir -p "$DOWNLOAD_DIR"
mkdir -p "$HOME/.gradle/wrapper/dists"
mkdir -p "$HOME/.gradle/caches"
mkdir -p "$HOME/.m2"

echo "配置："
echo "  GRADLE_VERSION=$GRADLE_VERSION"
echo "  MAVEN_VERSION=$MAVEN_VERSION"
echo "  REPOS_ROOT=$REPOS_ROOT"
echo

# 1) 下载 gradle 分发 zip 并安装到 $HOME/gradle
GRADLE_ZIP="gradle-${GRADLE_VERSION}-all.zip"
GRADLE_URL="https://services.gradle.org/distributions/${GRADLE_ZIP}"
GRADLE_ZIP_LOCAL="$DOWNLOAD_DIR/${GRADLE_ZIP}"

if [ ! -f "$GRADLE_ZIP_LOCAL" ]; then
  echo "下载 $GRADLE_URL -> $GRADLE_ZIP_LOCAL"
  curl -L -o "$GRADLE_ZIP_LOCAL" "$GRADLE_URL"
else
  echo "已存在 $GRADLE_ZIP_LOCAL，跳过下载"
fi

# 安装到 $HOME/gradle
INSTALL_DIR="$HOME/gradle"
mkdir -p "$INSTALL_DIR"
unzip -o -q "$GRADLE_ZIP_LOCAL" -d "$INSTALL_DIR"
GRADLE_UNZIPPED_DIR=$(ls -d "$INSTALL_DIR"/gradle-* | head -n1)
echo "Gradle 解压到: $GRADLE_UNZIPPED_DIR"

echo "为了在 shell 中临时使用 gradle："
echo "  export PATH=\"$GRADLE_UNZIPPED_DIR/bin:\$PATH\""
echo "（要永久生效，请把上面那行写入 ~/.bashrc 或 ~/.profile）"
echo

# 2) 把 gradle zip 放入 wrapper dists（让所有 wrapper 复用）
DIST_DIR="$HOME/.gradle/wrapper/dists/gradle-${GRADLE_VERSION}-all"
SEED_DIR="$DIST_DIR/seed"
mkdir -p "$SEED_DIR"
cp -n "$GRADLE_ZIP_LOCAL" "$SEED_DIR/" || true
# 解压到一个子目录，wrapper 会检测解压内容
UNPACK_DIR="$DIST_DIR/seed/gradle-${GRADLE_VERSION}"
mkdir -p "$UNPACK_DIR"
unzip -o -q "$GRADLE_ZIP_LOCAL" -d "$UNPACK_DIR"

echo "已将 gradle 发行包放到 wrapper 缓存： $DIST_DIR"
echo

# 3) 下载并安装 Maven（可选）
MAVEN_TGZ="apache-maven-${MAVEN_VERSION}-bin.tar.gz"
MAVEN_URL="https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/${MAVEN_TGZ}"
MAVEN_LOCAL="$DOWNLOAD_DIR/${MAVEN_TGZ}"

if [ ! -f "$MAVEN_LOCAL" ]; then
  echo "下载 Maven: $MAVEN_URL -> $MAVEN_LOCAL"
  curl -L -o "$MAVEN_LOCAL" "$MAVEN_URL"
else
  echo "Maven 已在 $MAVEN_LOCAL，跳过下载"
fi

mkdir -p "$HOME/maven"
tar -xzf "$MAVEN_LOCAL" -C "$HOME/maven"
MAVEN_DIR=$(ls -d "$HOME/maven"/apache-maven-* | head -n1)
echo "Maven 解压到: $MAVEN_DIR"

echo "为了在 shell 中临时使用 mvn："
echo "  export PATH=\"$MAVEN_DIR/bin:\$PATH\""
echo "（要永久生效，请把上面那行写入 ~/.bashrc 或 ~/.profile）"
echo

# 4) 遍历 REPOS_ROOT 下的仓库，预取依赖到本机缓存
if [ ! -d "$REPOS_ROOT" ]; then
  echo "仓库目录 $REPOS_ROOT 不存在。请确认你已把仓库克隆到该目录，或通过 REPOS_ROOT 指定正确路径。"
  exit 1
fi

# 临时 PATH，使得脚本使用本地 gradle/mvn 版本（如果安装）
export PATH="$GRADLE_UNZIPPED_DIR/bin:$MAVEN_DIR/bin:$PATH"

echo "开始遍历仓库并预取依赖（这会联网下载并缓存到 ~/.gradle/caches 与 ~/.m2/repository）："
for d in "$REPOS_ROOT"/*; do
  [ -d "$d" ] || continue
  echo
  echo "=== 处理仓库： $d ==="
  cd "$d"
  # detect build system
  if [ -f "pom.xml" ]; then
    echo "  Detected Maven (pom.xml). 正在执行: mvn -T1C -DskipTests dependency:go-offline"
    mvn -T1C -DskipTests dependency:go-offline || echo "  Maven 离线预取失败（可手动重试）"
  elif [ -f "build.gradle" ] || [ -f "build.gradle.kts" ] || [ -f "gradlew" ]; then
    if [ -x "./gradlew" ]; then
      echo "  Detected Gradle (gradlew). 正在执行: ./gradlew --no-daemon dependencies --refresh-dependencies"
      ./gradlew --no-daemon dependencies --refresh-dependencies || echo "  Gradle 预取依赖失败（可手动重试）"
    else
      echo "  Detected Gradle (build.gradle), 使用系统 gradle: gradle --no-daemon dependencies --refresh-dependencies"
      gradle --no-daemon dependencies --refresh-dependencies || echo "  Gradle 预取依赖失败（可手动重试）"
    fi
  else
    echo "  未检测到 build system（跳过）"
  fi
done

echo
echo "完成：已尝试为每个仓库预取依赖。"
echo "现在所有仓库应可复用以下位置的缓存："
echo "  Gradle wrapper dists: $HOME/.gradle/wrapper/dists/"
echo "  Gradle caches:        $HOME/.gradle/caches/"
echo "  Maven repo:           $HOME/.m2/repository/"
echo
echo "提示：若目标机在公司网络环境中需要代理，请先导出 HTTP_PROXY/HTTPS_PROXY 或在 ~/.m2/settings.xml / ~/.gradle/gradle.properties 中配置镜像/代理，然后重试。"
