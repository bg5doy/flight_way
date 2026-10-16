#!/bin/bash
# 构建 dumphfdl 的 DEBUG 版（用于诊断：--debug dsp,frame + A1/A2/M1 相关值汇总）
# wsl -d Ubuntu-20.04 -u root -- bash /mnt/d/Temp/aihfdl/build_debug_dumphfdl.sh
set -e
SRC=/mnt/d/Temp/aihfdl/openhfdl/szpajder-dumphfdl-bb7234b
cd "$SRC"
rm -rf build-debug && mkdir build-debug && cd build-debug
cmake .. -DCMAKE_BUILD_TYPE=Debug -DCMAKE_PREFIX_PATH=/usr/local > /tmp/dbg_cmake.log 2>&1 \
  || { tail -30 /tmp/dbg_cmake.log; exit 1; }
make -j"$(nproc)" > /tmp/dbg_make.log 2>&1 || { tail -40 /tmp/dbg_make.log; exit 1; }
echo "=== built ==="
ls -la src/dumphfdl
ln -sf "$SRC/build-debug/src/dumphfdl" /usr/local/bin/dumphfdl-debug
echo "=== debug classes available (verify DEBUG) ==="
/usr/local/bin/dumphfdl-debug --debug help 2>&1 | head -20
echo "DONE"
