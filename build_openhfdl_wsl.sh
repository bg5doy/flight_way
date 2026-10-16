#!/bin/bash
# =====================================================================
# 一键构建 OpenHFDL (dumphfdl) + libacars  (在 WSL / Linux 中运行)
#
#   wsl -d Ubuntu-20.04 -u root -- bash /mnt/d/Temp/aihfdl/build_openhfdl_wsl.sh
#
# 依赖: build-essential cmake pkg-config libliquid-dev libfftw3-dev
#       libconfig++-dev libglib2.0-dev zlib1g-dev libjansson-dev libxml2-dev
#       (可选) libsoapysdr-dev —— 用于直接驱动 SDR 硬件
# 产物: /usr/local/bin/dumphfdl  (以及 /usr/local/lib/libacars-2.so)
# =====================================================================
set -e
export DEBIAN_FRONTEND=noninteractive

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OH=$ROOT/openhfdl
DUMP_SRC=$OH/szpajder-dumphfdl-bb7234b
DUMP_BIN=$DUMP_SRC/build/src/dumphfdl

echo "=== 1/5 安装构建依赖 ==="
apt-get update -qq
apt-get install -y -qq \
  build-essential cmake pkg-config \
  libliquid-dev libfftw3-dev libconfig++-dev libglib2.0-dev \
  zlib1g-dev libjansson-dev libxml2-dev
apt-get install -y -qq libsoapysdr-dev || echo "[warn] 无 SoapySDR，仅支持 --iq-file"

echo "=== 2/5 构建 libacars (若未安装) ==="
if [ -e /usr/local/lib/libacars-2.so ]; then
  echo "libacars 已安装，跳过"
else
  cd "$OH"
  rm -rf libacars-src && mkdir -p libacars-src
  tar -xzf libacars-2.2.1.tar.gz -C libacars-src --strip-components=1
  cd libacars-src
  rm -rf build && mkdir build && cd build
  cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON > /tmp/libacars_cmake.log 2>&1
  make -j"$(nproc)" > /tmp/libacars_make.log 2>&1
  make install > /tmp/libacars_install.log 2>&1
  ldconfig
fi

echo "=== 3/5 构建 dumphfdl (OpenHFDL) ==="
if [ ! -d "$DUMP_SRC" ]; then
  echo "解压 dumphfdl 源码 (dumphfdl-1.7.0.tar.gz)..."
  cd "$OH"
  tar -xzf dumphfdl-1.7.0.tar.gz
fi
cd "$DUMP_SRC"
rm -rf build && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/usr/local > /tmp/dumphfdl_cmake.log 2>&1
make -j"$(nproc)" > /tmp/dumphfdl_make.log 2>&1

echo "=== 4/5 安装到 /usr/local/bin ==="
ln -sf "$DUMP_BIN" /usr/local/bin/dumphfdl
ldconfig

echo "=== 5/5 验证 ==="
dumphfdl --version
echo "--- 输出驱动 ---"
dumphfdl --output help 2>&1 | head -18
echo
echo "=== 构建完成：dumphfdl 已就绪 ==="
