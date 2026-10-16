# HFDL 航空数据链解码器（OpenHFDL + 全球地图航迹）

用 **OpenHFDL (dumphfdl)** 这一成熟解码器解调 HFDL（High Frequency Data Link）
信号，解析航班号 / 位置 / 航向 / 高度 / 速度，在**全球矢量地图**上绘制飞机位置
与历史航迹，并把所有报文存入本地 SQLite 供检索。

> 与前一版不同：**解调不再由本项目自研**，而是直接集成 OpenHFDL。
> 本项目负责「参数配置 → 启动解码器 → 解析输出 → 可视化 / 存储 / 检索」。

## 项目简介

HFDL（高频数据链）解码与航班可视化工具。基于成熟的 **OpenHFDL (dumphfdl)** 解调航空
HFDL 报文，解析航班号、位置、航向，在**全球矢量地图**实时绘制飞机与历史航迹
（最多 16 小时），全部报文写入本地 SQLite，可按时间 / 航班号检索。

**输入**：SDR 的 I/Q 录制文件（含 SDRuno 的 WAV）｜声卡 / SDR IQ OUT 实时桥接｜
远端 dumphfdl 的 TCP JSON｜内置仿真（无需硬件）。

**环境**：Python 3.10+（实测 3.14），需 numpy、scipy、matplotlib、sounddevice；
OpenHFDL (dumphfdl 1.7.0) + libacars 2.2.1，Linux / WSL 下用
`build_openhfdl_wsl.sh` 一键构建；可选 SDR 接收机或短波接收机声卡输出。

**界面**：Tkinter + matplotlib，无需浏览器。

---

## 1. 架构

```
                    ┌──────────────────────────────────────────┐
   SDR 硬件 ────────▶│  OpenHFDL (dumphfdl 1.7.0)               │
   或 I/Q 文件 ─────▶│  · SoapySDR 直采 / --iq-file 回放         │
                    │  · 1800 baud 解调 (BPSK/QPSK/8PSK 自适应)│
                    │  · 去交织 + R-S 纠错 + LPDU/SPDU/HFNPDU   │
                    │  · libacars 解析 ACARS                   │
                    └───────────────┬──────────────────────────┘
                                    │ JSON / Basestation
                        stdout ◀────┴────▶ TCP / UDP
                                    │
                    ┌───────────────▼──────────────────────────┐
                    │  openhfdl_client.py  (本项目的集成层)     │
                    │  · 定位 dumphfdl（本机 / WSL）            │
                    │  · 依据「常用默认配置」拼装命令行          │
                    │  · 容错解析 JSON / Basestation → HfdlFrame│
                    └───────────────┬──────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
        worldmap.py           store.py              main.py (GUI)
      矢量地图/航迹         SQLite 持久化          参数面板/日志/查询
```

**为什么这样集成**：OpenHFDL 是经过大量实收验证的 C/C++ 解码器，其物理层
（定时恢复、载波环、R-S 译码、去交织）与协议栈远比自研实现可靠。
本项目的价值在于把它的能力接到可配置、可视化的桌面应用上。

---

## 2. HFDL 常用默认配置

以下参数取自 OpenHFDL 源码（`src/hfdl.h`、`src/hfdl.c`）与官方系统表
（`etc/systable.conf`），即实际运行的权威默认值。

### 2.1 调制 / 突发类型

| 速率 | 调制 | 编码率 | 时隙 | 说明 |
|------|------|--------|------|------|
| 300 bps  | BPSK | 1/4 | 单/双 | 最稳健，用于恶劣传播 |
| 600 bps  | BPSK | 1/2 | 单/双 | |
| 1200 bps | QPSK | 1/2 | 单/双 | |
| 1800 bps | 8PSK | 1/2 | 单/双 | 速率最高，需最好信噪比 |

- **符号率固定 1800 baud**（`HFDL_SYMBOL_RATE = 1800`，内部 SPS = 3）
- 具体速率由 dumphfdl 从 MPDU 头自动识别，**无需人工指定**

### 2.2 标准波段与频道（16 个地面站，共 105 个频率）

| 波段 | 频道数 | 频率范围 (kHz) | 推荐采样率 |
|------|--------|----------------|-----------|
| 2.9 MHz  | 7  | 2941–3016 | 192 kSps |
| 3.4 MHz  | 2  | 3455–3497 | 96 kSps |
| 3.9 MHz  | 1  | 3900 | 96 kSps |
| 4.6 MHz  | 4  | 4654–4687 | 96 kSps |
| 5.5 MHz  | 14 | 5451–5720 | 500 kSps |
| 6.5 MHz  | 13 | 6529–6712 | 250 kSps |
| **8.9 MHz** | **15** | **8825–8977** | **250 kSps** |
| 10.0 MHz | 9  | 10027–10093 | 192 kSps |
| 11.1 MHz | 1  | 11184 | 96 kSps |
| 11.3 MHz | 9  | 11306–11387 | 192 kSps |
| 13.2 MHz | 10 | 13264–13351 | 192 kSps |
| 15.0 MHz | 1  | 15025 | 96 kSps |
| 17.9 MHz | 10 | 17901–17985 | 192 kSps |
| 21.9 MHz | 9  | 21928–21997 | 192 kSps |

> **默认预设 = 8.9 MHz 波段（15 频道，250 kSps）**，中纬度昼夜通用。
> 夜间优先 5.5–11.3 MHz，白天优先 13.2–21.9 MHz。

**重要**：dumphfdl 要求所有频道都落在 SDR 采样带宽内。因此预设以「单波段」为
单位；跨波段需多路 SDR 或分时运行。GUI 的「频道预设」下拉会自动填好频道列表
与推荐采样率。

### 2.3 SDR / I-Q 参数

| 项目 | 常用值 |
|------|--------|
| 采样率 | 96 k / 192 k / 250 k / 500 k / 1 M / 2 M Sps |
| I/Q 格式 | `CS16`（最常用）/ `CS8` / `CU8` / `CF32` |
| 中心频率 | 留空由 dumphfdl 自动取频道中心；I/Q 回放时须填录制时的调谐频率 |
| 系统表 | `systable.conf`（16 地面站，用于把 GS ID 换成站名） |

### 2.4 解码器开关

| 开关 | 本项目默认 | 说明 |
|------|-----------|------|
| `--output decoded:json:file:path=-` | 启用 | JSON 输出到 stdout |
| `--utc` | 开 | 日志用 UTC |
| `--output-mpdus` | 关 | 输出媒体 PDU（调试用） |
| `--milliseconds` | 关 | 毫秒级时间戳 |
| `--station-id` | 空 | 本站标识（写入 JSON 的 `station` 字段） |

---

## 3. 快速开始

### 3.1 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3.2 构建 OpenHFDL（必需，一次性）

OpenHFDL 是 C/C++ 程序，需要先构建。**Windows 下推荐用 WSL**：

```bash
# 在 WSL (Ubuntu) 中执行：自动装依赖、编译 libacars 与 dumphfdl 并安装
wsl -d Ubuntu-20.04 -u root -- bash /mnt/d/<你的路径>/aihfdl/build_openhfdl_wsl.sh
```

完成后 `/usr/local/bin/dumphfdl` 即就绪，本项目会自动检测到（kind=`wsl`）。

Linux 可直接执行同一脚本；macOS 用 `brew install liquid-dsp fftw soapysdr
libconfig` 后 `cmake .. && make`。

验证：

```bash
dumphfdl --version          # 期望: dumphfdl 1.7.0 (libacars 2.2.1)
dumphfdl --output help      # 期望列出 text/basestation/json 与 file/tcp/udp
```

### 3.3 运行

```bash
python main.py
```

- **▶ 开始解码**：按所选后端启动解码（GUI 只提供**可实际执行**的后端）
  - `OpenHFDL + I/Q 文件（离线解码）` —— 回放 SDR 录制的 I/Q（含 SDRuno 的 WAV）
  - `OpenHFDL + 声卡桥接（声卡 / SDR IQ OUT）` —— **让 OpenHFDL 也能用声卡/SDR IQ OUT**：
    信号 → CS16 I/Q → `dumphfdl --iq-file -`（详见第 6、7 节）
  - `OpenHFDL 远程解码 (TCP JSON)` —— 连接远端 dumphfdl 的 `--output ...:tcp:...` JSON 流

> **已移除的后端**（无法执行，故从界面删除）：
> - `内置教学解调` —— 自研解调与真实 HFDL **非位级兼容**，接真实信号解不出任何报文，
>   只会让人误以为"有信号却解不出"。相关模块（`demod.py` / `hfdl_frame.py`）保留
>   供教学与环回自测（`test_loopback.py`），但不再作为解码后端。
> - `SDR 驱动`（SoapySDR 直连）—— 本机 SDRuno 独占 SDRplay 硬件，且 dumphfdl 运行在
>   WSL 内无法访问 USB 设备，该路径无法执行。

> GUI 会**按后端自动激活对应面板**（标题前显示 `▶`），其余面板变灰，避免选错参数区。

#### 声卡直连速查（只插声卡时怎么选）

| 你的信号源 | 解码后端选 | 关键参数 |
|---|---|---|
| **SDRuno IQ OUT / 声卡 SDR**（L/R = I/Q） | `OpenHFDL + 声卡桥接` | 信号来源 = **SDR I/Q**；采样率与 IQ OUT 一致；填 **VFO 频率** |
| **SDR 录制的 I/Q 文件**（含 SDRuno 的 .wav） | `OpenHFDL + I/Q 文件` | 选文件后自动识别采样率/中心频率（`.wav`）|
| **短波接收机的 SSB 音频**（实信号） | `OpenHFDL + 声卡桥接` | 信号来源 = **声卡/接收机音频**；音频中心 ≈ 1370 |
| **只有麦克风 / 环境声**（无 HFDL 信号） | 任意后端都解不出帧 | 用 **▶ 仿真示例** 看界面效果 |

- **▶ 仿真示例**：无需任何硬件，合成 dumphfdl 风格 JSON 走完整
  「解析 → 地图 → 入库」链路，用于演示与回归测试
- **🔍 查询**：按航班 / 注册号 / 时间检索历史报文
- **📖 系统表**：查看 16 个地面站及其频率
- **⛶ 世界全景**：恢复全球视图（滚轮缩放、**左键拖拽平移**）

#### 界面布局

```
┌─ 工具栏：开始/停止 · 仿真 · 查询 · 世界全景 · 系统表 ─────────────┐
├──────────────┬──────────────────────────────────────────────────┤
│ 参数面板      │ 全球航迹图                                        │
│ （可滚动）    │   滚轮缩放 / 左键拖拽平移 / 「世界全景」复位        │
│              ├───────────────────────┬──────────────────────────┤
│              │ 解析日志（完整宽度）    │ 当前飞机（6 列）          │
└──────────────┴───────────────────────┴──────────────────────────┘
```

#### 地图说明

**投影与操作**

- **等比例投影**：`ax.set_aspect("equal")`，1°经度 = 1°纬度，**不会拉伸变形**
- **左键拖拽平移**：按像素增量换算位移（用拖拽起点的范围快照，避免抖动）
- **滚轮缩放**：以光标为中心，x/y 用同一系数保持比例；纬度限制在 ±90°
- 地图区按 2:1 左右设计（世界地图本身约 2:1），因此几乎无留白

**飞机图标：民航客机**

- **多部件俯视轮廓**：机体（钝圆机头 + 后掠翼 + **翼梢小翼** + 常规平尾，
  17 点镜像成 32 点）+ **左右两台发动机短舱**，共 3 个绘制块
- 按**面积形心**居中，左右对称误差 = 0（钝圆机头，非战斗机尖角）
- **按航向旋转**：0°=正北、90°=正东、180°=正南、270°=正西、135°=东南
- **缩放优化**：图标尺寸以**屏幕像素**计（不随缩放/纬度拉伸），并随视野缩小
  适度放大 —— 以全球视图为基准，视野每缩小一半约放大 30%，上限 40 px
- 越久未更新的飞机颜色越暗（按 `stale_minutes` 淡出到 35%）

**航向从哪来（重要）**

HFDL 的性能报文**本身不含航向**（也不含速度/高度）——OpenHFDL 的
`struct hfnpdu_perf_data`（`src/hfnpdu.c:37`）只有 `flight_id` + `location(lat/lon)`
+ UTC 时间，JSON 输出同样没有航向字段。因此：

- 若数据源直接给出航向（`track`/`heading`/`hdg`），**优先采用**，标记来源 `msg`
- 否则由**航迹相邻定位点推算真方位角**（大圆初始方位），标记来源 `track`
  - 两点相距须 ≥ **2 km** 才更新，避免静止/微动导致机头乱摆
  - 在**单位圆上做指数平滑**（α=0.35），抑制定位抖动带来的摆动
- **航向未知时画中空圆环 + 圆心**，而不是默认朝北的飞机图标
  —— 避免"所有飞机都朝北"的误读

> 因此飞机刚收到**第 1 个**定位点时显示为圆环，收到**第 2 个**后即显示为
> 指向实际航向的客机图标。实测：只喂位置点（不给航向）时，推算误差 ≤ 0.1°。

**航班标签**

- 内容：`航班号` + 第二行 `航向` 与/或 `FL高度`（如 `247°` / `247° FL350`），
  带半透明底板。航向仅在与航迹推算一致时显示（即来源 `track`）
- **碰撞避让**：标签外框重叠时自动跳过，保证不叠字
- 三种模式（界面可选）：`按缩放自动`（默认：飞机 ≤14 架或经度跨度 <60° 时显示）
  / `始终显示` / `不显示`

**航迹**

- **按时间渐变**：越旧的航段越透明（alpha 0.85 → 0.10），一眼看出飞行方向
- **跨 180° 经线自动分段**，不会横穿整幅地图
- 起点画小圆点（半径 2.5 px）标示航迹开始
- **最多保留 16 小时（960 分钟）**，界面可调但不超过此上限

#### 应用 / 地图显示选项

| 选项 | 默认 | 说明 |
|------|------|------|
| 过期擦除 (分) | 60 | 超过此时间未更新的飞机从地图擦除 |
| **航迹保留 (分)** | 120 | 历史航迹时长，**上限 960 分 = 16 小时** |
| **在地图上显示历史航迹** | 开 | 关闭后只显示飞机图标与标签 |
| **航班标签** | 按缩放自动 | `按缩放自动` / `始终显示` / `不显示` |
| **图标大小 (px)** | 15 | 全球视图下的图标基准像素尺寸（6–40）|

改完点「应用参数」即时生效，日志会回显当前设置。

#### 内置解调参数（仅自研解调链，非解码后端）

| 参数 | 默认 | 说明 |
|------|------|------|
| 音频中心频率 | 1370 Hz | SSB 输出中 HFDL 信号的位置 |
| 带宽 | 默认 1800 Hz | 信号占用带宽 |
| V.29 解扰 / 带通预滤波 | 开关 | 对应 `descrambler` / `bandpass` |

常用组合对照：

| 波特率 | 调制 | 原始速率 | 对应 HFDL 档位 |
|--------|------|----------|----------------|
| 300  | BPSK | 300 bps  | HFDL 300 bps（编码率 1/4） |
| 600  | BPSK | 600 bps  | HFDL 600 bps（编码率 1/2） |
| 1200 | QPSK | 2400 bps | HFDL 1200 bps（编码率 1/2） |
| 1800 | 8PSK | 5400 bps | HFDL 1800 bps（编码率 1/2）★ 标准 |

> 全部组合均已通过环回自测（见第 5 节 `test_loopback.py`，覆盖
> BPSK/QPSK/8PSK × 300/600/1200/1800/2400 baud，10/10 通过）。

### 3.4 典型命令（GUI 会自动生成，也可手工运行）

```bash
# 实时 SDR（RTL-SDR），监听 8.9 MHz 波段
dumphfdl --soapysdr driver=rtlsdr --sample-rate 250000 \
         --system-table systable.conf \
         --output decoded:json:file:path=- --utc \
         8825 8834 8843 8885 8894 8912 8921 8927 8936 8939 8942 8948 8957 8977

# 回放 I/Q 录制（CS16）
dumphfdl --iq-file rec.cs16 --sample-format CS16 --sample-rate 250000 \
         --centerfreq 8901 --system-table systable.conf \
         --output decoded:json:file:path=- --utc 8825 8834 8843 8885

# 供本程序「远程模式」消费
dumphfdl --soapysdr driver=airspyhf --sample-rate 192000 \
         --output decoded:json:tcp:address=127.0.0.1,port=5555 --utc 8825 8927
```

---

## 4. 文件说明

| 文件 | 说明 |
|------|------|
| `main.py` | GUI 主程序（Tkinter + matplotlib 内嵌地图） |
| `openhfdl_client.py` | **OpenHFDL 集成层**：定位/启动 dumphfdl、拼装命令行、解析输出 |
| `soundcard_bridge.py` | **信号→I/Q 桥接**：支持声卡音频（希尔伯特）与**真正的 SDR I/Q**（SDRuno IQ OUT）|
| `list_all_devices.py` | 列出全部音频设备，识别虚拟声卡/环回（IQ OUT 中转需要）|
| `check_audio.py` | **声卡检查工具**：设备/采样率/电平/频谱/HFDL 信号判定（IC-7300 调机用） |
| `analyze_hfdl.py` | **信号存在性分析**：分块统计 CV/平顶宽度，判断有无 HFDL 信号 |
| `diag_dumphfdl.py` | **dumphfdl 诊断**：用 Debug 版取 A1/A2/M1 相关值，判断信号是否被看到 |
| `test_hilbert_accuracy.py` | 检验希尔伯特解析变换在各频点的镜像抑制/幅度起伏 |
| `build_debug_dumphfdl.sh` | 构建 Debug 版 dumphfdl（诊断所需） |
| `check_stereo.py` | 判断声卡左右声道是否相同（是否真 I/Q） |
| `hfdl_presets.py` | **常用默认配置**：8 种突发类型、14 波段、频道表、推荐采样率、预设 |
| `systable.conf` | OpenHFDL 官方地面站系统表（16 站 / 105 频率） |
| `config.py` | 配置项（OpenHFDL 参数 + 声卡桥接参数 + 应用参数） |
| `worldmap.py` | 全球矢量地图与飞机/航迹绘制 |
| `store.py` | SQLite 持久化与检索 |
| `world_ne110m.geojson` | Natural Earth 110m 国家边界 |
| `build_openhfdl_wsl.sh` | 一键构建 OpenHFDL + libacars（WSL/Linux） |
| `test_openhfdl.py` | OpenHFDL 集成端到端自测 |
| `test_soundcard_bridge.py` | 声卡桥接自测（希尔伯特/CS16/管道灌入 dumphfdl） |
| `test_map.py` | 地图拖拽平移 / 等比投影 / 缩放比例校验 |
| `test_map_display.py` | 飞机图标朝向/缩放、标签避让、航迹（16h/渐变/日界线）|
| `test_loopback.py` | 内置教学解调环回自测（10/10 用例） |
| `gui_smoke.py` | GUI 启动 + 仿真联动冒烟测试 |
| `test_gui_openhfdl.py` | GUI 面板启动真实 OpenHFDL |
| `test_gui_bridge.py` | GUI 声卡桥接后端端到端校验 |
| `openhfdl/` | OpenHFDL 与 libacars 源码（vendored，用于构建） |
| `demod.py` `hfdl_frame.py` | 教学用自研解调链（仅环回自测，**非解码后端**） |

---

## 5. 自测

```bash
python test_openhfdl.py       # OpenHFDL 集成：定位/启动/解析   → PASS
python test_soundcard_bridge.py  # 声卡桥接：希尔伯特/CS16/管道 → PASS
python test_loopback.py       # 内置解调环回  → 10/10
                              #   BPSK/QPSK/8PSK × 300/600/1200/1800/2400 baud
python hfdl_presets.py        # 打印默认配置与波段
python gui_smoke.py           # GUI + 仿真联动
python test_gui_openhfdl.py   # GUI 面板启动真实 OpenHFDL
python test_gui_bridge.py     # GUI 声卡桥接后端
```

`test_openhfdl.py` 会真实启动 WSL 中的 dumphfdl，校验它加载系统表、
打开 JSON 输出，并把解析结果转换为地图所需的记录结构。

---

## 6. 声卡与 OpenHFDL（常见问题）

**Q：OpenHFDL 能直接从声卡输入解调吗？**

**不能。** 已核对 OpenHFDL 源码，结论明确：

- 输入只有两种：`--soapysdr <设备串>`（SDR 硬件）与 `--iq-file <文件|->`
  （I/Q 文件或**标准输入**）。见 `src/main.c` 的 `usage()` 与 option 表。
- 全部源码中**没有任何** audio / ALSA / PulseAudio / PortAudio / WASAPI
  相关代码。
- 采样格式只有 `CU8` / `CS16` / `CF32`，**全是复数 I/Q**，不接受实数音频。
- 采样率硬性要求 `≥ HFDL_SYMBOL_RATE × SPS = 1800 × 3 = 5400 sps`。

**Q：那能不能让声卡数据喂给 OpenHFDL？**

**可以，通过桥接** —— 因为 dumphfdl 支持 `--iq-file -` 从 stdin 读 I/Q。
本项目实现了 `soundcard_bridge.py`：

```
声卡 ──▶ SoundcardBridge ──▶ CS16 交织 I/Q ──▶ dumphfdl --iq-file - ──▶ JSON
```

三种输入模式（GUI 的「声卡桥接」面板可选）：

| 模式 | 适用场景 | 说明 |
|------|----------|------|
| `stereo-iq` | **声卡 SDR**（SoftRock / FiFi-SDR 等） | 左右声道本身就是 I/Q，**无损、最理想** |
| `analytic` | 仅有 SSB 接收机音频输出 | 实音频经希尔伯特变换成解析信号，实测镜像抑制 **−61 dB**（推荐） |
| `real-i` | 最简场景 | I=音频、Q=0，靠信道滤波抑制镜像，约 −3 dB |

**频率约定**：dumphfdl 侧用 `--centerfreq 0`，频道频率填**音频中心频率(kHz)**。
例如 HFDL 音频中心 1370 Hz → 频道写 `1.37`，信道化器会把它搬到 0 Hz：

```bash
# 手工等价命令（桥接器内部就是这样拼的）
dumphfdl --iq-file - --sample-format CS16 --sample-rate 48000 \
         --centerfreq 0 --system-table systable.conf \
         --output decoded:json:file:path=- --utc 1.37
```

**命令行用法**：

```bash
python soundcard_bridge.py --list                 # 列出输入设备
python soundcard_bridge.py --device 1 --rate 48000 \
       --mode analytic --audio-center 1370         # 开始桥接解码
```

**Q：没有 SDR 硬件能用吗？**
可以。用 `--iq-file` 回放 I/Q 录制、用声卡桥接（需声卡 SDR 或 SSB 音频）、
或点「仿真示例」演示完整界面。

**Q：GUI 显示"未找到 dumphfdl"？**
先运行 `build_openhfdl_wsl.sh` 构建；或在面板手动指定二进制路径；
或改选「远程模式」连接已运行 dumphfdl 的机器。

**Q：一个 SDR 能同时听多个波段吗？**
不能。dumphfdl 要求所有频道落在采样带宽内，请按波段分组运行。

**Q：位置来自哪里？**
dumphfdl 从 HFNPDU 性能数据 / 频率数据、CPDLC、ADS-C、ACARS 位置报中提取，
经 libacars 解析后写入 JSON。本程序把结果映射为
航班/注册/位置/航向/高度/速度。

> **注意**：声卡桥接受限于声卡带宽与动态范围，且需要把 HFDL 音频准确落在
> 所设定的音频中心附近；实际解码成功率通常低于直接使用 SDR。若追求效果，
> 优先用 SoapySDR 直采。

---

## 7. 用 IC-7300（或其它带 USB 声卡的电台）解 HFDL

**结论：可行。** 原理与当年 PC-HFDL 的做法一致 —— 电台音频 → 声卡 → 解码。

### 7.1 为什么可行

- IC-7300 的 USB 声卡输出的是**解调后的音频（实信号）**，不是 I/Q。
- HFDL 是抑制载波 PSK；**SSB(USB) 解调是线性频率搬移，相位信息保留**。
- 把实音频经**希尔伯特变换**变成解析信号 = 零中频 I/Q，交给 dumphfdl 即可。
- 音频中心用 HFDL 经典值 **1440 Hz**（PC-HFDL 约定）。

### 7.2 本机实测（IC-7300，USB 连接）

| 项目 | 实测值 |
|------|--------|
| 设备 | `[1] 麦克风 (4- USB Audio CODEC)` + `[6] 扬声器 (4- USB Audio CODEC)` |
| 左右声道 | **完全相同**（corr = 1.000000，RMS(L−R) ≈ 0）→ **单声道复制，不是 I/Q** |
| 可用采样率 | 48000 Hz（电台原生，建议就用这个，避免 Windows 重采样） |
| 音频电平 | 约 −30 dBFS RMS / −11 dBFS 峰值（不削顶） |
| 通带 | 250–3000 Hz（≈2.75 kHz，SSB 滤波器，正好覆盖 HFDL 占用带宽） |
| 灌入 dumphfdl | 480 万采样、运行正常 |

> ⚠ 因为是**单声道复制**，桥接模式**必须选 `analytic`**；选 `stereo-iq` 会失败
> （I 与 Q 相同 → 无正交信息）。

### 7.3 电台设置（关键，做错就解不出）

| 项目 | 设置 |
|------|------|
| 模式 | **USB**（或 USB-D DATA），**不要** LSB / AM / FM |
| IF 滤波器 | **最宽**（FIL1，3.0 kHz） |
| 降噪 NR | **关** |
| 抑噪 NB | **关** |
| Notch / APF | **关** |
| 语音压缩 COMP | **关** |
| AGC | **Fast 或 OFF**，用手动 RF 增益控制电平（AGC 抽吸会破坏 PSK 包络） |
| 输出电平 | 峰值约 −20 ~ −10 dBFS |

### 7.4 调谐公式（USB 上边带）

```
表盘频率 = HFDL 频率 − 音频中心
```

例：HFDL 频道 **8927.0 kHz**，想让信号落在音频 **1441 Hz**：

```
表盘 = 8927.0 − 1.441 = 8925.559 kHz
```

> 频道频率可从 GUI 的「📖 系统表」里查。夜间优先 5.5–11.3 MHz，
> 白天优先 13.2–21.9 MHz。

### 7.5 GUI 设置

1. 解码后端 → **`OpenHFDL + 声卡桥接 (声卡→I/Q→dumphfdl)`**
2. 桥接模式 → **`单声道实音频 → 解析信号（希尔伯特）`**
3. 声卡设备 → **`[1] 麦克风 (4- USB Audio CODEC)`**
4. 声卡采样率 → **48000**
5. 音频中心 → **1441**（或实测能量重心）
6. 点 **▶ 开始解码**

### 7.6 先确认"真的对准了 HFDL"

```bash
python check_audio.py --capture 1 --seconds 6
```

看两项指标：

| 指标 | HFDL 信号 | 噪声 |
|------|-----------|------|
| **包络变异系数 CV** | < 0.30（PSK 恒定包络） | > 0.4（瑞利分布） |
| 通带内谱起伏 | < 4 dB（接近平顶） | 较大 |

实测示例（当时未对准信号）：

```
包络变异系数 CV = 1.317   通带内谱起伏 = 5.0 dB
❌ 包络起伏大（瑞利分布特征）→ 更像带限噪声，此频率上当前没有可解调的 HFDL 信号
```

CV 判断为噪声时，**换频率/换时段再试**，否则 dumphfdl 会长时间 0 帧。

### 7.7 局限

- **IC-7300 没有 I/Q 输出**（IC-7610 / IC-9700 才有），只能走音频路径。
- 音频路径性能低于 SDR 直采：SSB 滤波器的幅度/群延迟起伏会引入 ISI，
  AGC 与声卡附加滤波也会劣化；可解帧数通常少于 SDR。
- 若长期 0 帧，建议加一个入门 SDR（RTL-SDR / Airspy HF+）走 `--soapysdr`
  —— 那是 OpenHFDL 的原生用法，效果最好。

### 7.8 A/B 排查：为什么 PC-HFDL 能解、我的桥接不能

**先确认音频里到底有没有 HFDL 信号**——这是最常见的误判来源。HFDL 是恒包络
PSK：有信号时**包络变异系数 CV ≈ 0.1~0.25**，且频谱是**约 2.4 kHz 宽的平顶**；
噪声的 CV > 0.4。

```bash
# 30 秒分块分析（推荐先跑这个）
python analyze_hfdl.py 1 30
```

输出示例（实测：当时音频里没有 HFDL）：

```
  块 RMS(dBFS)    CV包络    平顶宽(Hz)    中心(Hz)       判定
  0     -35.6   0.829          0      1430    噪声/其它
  1     -21.3   1.703         12      1447    噪声/其它
  2     -32.6   0.837       1207      1529    噪声/其它
  ...
```

CV 全在 0.5~1.7、平顶宽时有时无 → **当前频率上没有可解调的 HFDL 信号**，
此时任何解码器（包括 PC-HFDL）都解不出。先换到**真正有信号**的频率。

**决定性 A/B 测试**（同一段音频分别喂两套解码器）：

1. 让 **PC-HFDL 正常解码**的同时，录制那段音频为 WAV
   （可用 `check_audio.py --capture 1 --seconds 60 --save-wav hfdl.wav`）
2. 用本桥接回放**同一个 WAV**（不占用声卡）：

```bash
python soundcard_bridge.py --wav hfdl.wav --mode analytic
```

- 若解出帧 → 桥接正确，此前 0 帧只是因为当时无信号
- 若仍 0 帧 → 说明音频路径/参数有真实差异，可据此继续排查

### 7.9 带内自动搜索（对齐 PC-HFDL / KG-HFDL 的行为）

PC-HFDL 与 KG-HFDL 都是**音频解码器**，会自己在音频通带内搜索并锁定 HFDL
信号，不需要你精确报出信号在音频里的位置。而 dumphfdl 需要**显式给出频道**。

因此桥接默认开启**带内自动搜索**：在 400–2900 Hz 内每 200 Hz 生成一个频道
（约 13 个）交给 dumphfdl 并行解码，命中者即可解出，并自动去重。GUI 的
「自动搜索音频带内信号（推荐）」勾选框控制此行为；关闭则只用「音频中心」
单一频道（需要你精确对准）。

### 7.10 关于 Windows 原生版（不用 WSL）

结论：**官方没有预编译的 Windows exe/dll**。

- v1.7.0 的 GitHub Release **没有任何 assets**（已核实 API）。
- 源码以 Linux/macOS 为主；构建系统有部分 MinGW 适配
  （`if(UNIX OR MINGW)`、`pthread_barrier.c` 兜底），但**硬性依赖 `libm` 与
  `pthread` 库**：`find_library(LIBM m REQUIRED)`、`find_library(LIBPTHREAD
  pthread REQUIRED)`。
- libacars **支持 Windows**（2.2.1 变更日志明确修了 Windows 编译）。
- 本机**未安装任何编译工具链**（无 gcc / cmake / MSVC / MSYS2）。

要得到原生 `dumphfdl.exe`，路径是 **MSYS2 + MinGW-w64**：用 pacman 装
`fftw3 / glib2 / libconfig`，再从源码构建 **liquid-dsp** 与 **libacars**，最后
构建 dumphfdl。工程量不小但可行。

> 注意：即使有了原生 exe，**声卡场景仍然需要桥接**——因为 dumphfdl 不认识
> 声卡，必须由本项目的 `soundcard_bridge.py` 把音频转成 I/Q 再从 stdin 灌入。
> 原生 exe 只是省掉 WSL 这一跳（降低延迟、简化部署）。

另一个现实选择：**WSL 里已经编译好的 dumphfdl 可以直接用**，也无需改动。

### 7.8 声卡枚举去重（Windows 必读）

Windows 上 PortAudio 会**按主机 API 重复枚举同一物理设备**。本机实测：

| 物理设备 | MME | DirectSound | WASAPI | WDM-KS |
|---|---|---|---|---|
| icspring camera | [2] | [11] | [22] | [28] |
| K66 | [3] | [12] | — | [25] |
| **IC-7300 (USB Audio CODEC)** | [1] | [10] | [23] | [31] |

共 14 条，但**只有 3 台真实输入设备**；另外 MME/DirectSound 还各报一个
**假聚合设备**（`Microsoft Sound Mapper - Input`、`主声音捕获驱动程序`）。

本项目已处理（`audio.py`）：

- **按归一化设备名去重** —— 去掉 `麦克风 (...)` 用途包装与 `4- ` 实例前缀，
  `麦克风 (4- USB Audio CODEC )` 与 `USB Audio CODEC` 归为同一台设备
- **分组键不含通道数** —— 同一设备在不同 API 下会报不同通道数
  （IC-7300：WASAPI 1ch / MME 2ch），计入会导致去重失效
- **过滤假聚合设备**
- **优先 WASAPI**（其次 WDM-KS → DirectSound → MME）：WASAPI 可避开 Windows
  音频增强，对 PSK 数据解调更有利
- **主机 API 可选** —— GUI 的「声卡枚举」面板可切换
  `自动 / Windows WASAPI / WDM-KS / DirectSound / MME`，切换即刷新设备列表
- **自动回退** —— 若 WASAPI 打不开，自动尝试同一物理设备的其它主机 API
  （例如 `[23]WASAPI → [31]WDM-KS → [10]DS → [1]MME`）
- **WASAPI 独占模式** checkbox —— 绕过 Windows 音频引擎，最接近位精确采集

去重效果（14 → 3）：

```
=== 原始枚举：14 条 ===
[ 0]                  MME   Microsoft Sound Mapper - Input   <假设备>
[ 1]                  MME   麦克风 (4- USB Audio CODEC )
...
[31]       Windows WDM-KS   USB Audio CODEC

=== 去重后：3 条（优先 WASAPI）===
[22] icspring camera · WASAPI
[24] K66 · WASAPI
[23] USB Audio CODEC · WASAPI      ← IC-7300
```

诊断命令：

```bash
python audio.py            # 打印「原始枚举 vs 去重后」对比
python diag_devices.py     # 打印每个设备的主机 API，并按名称分组看重复
```

### 7.9 ★ 关键发现：dumphfdl 的 1440 Hz SSB 载波偏置（此前的致命错误）

**这是"有 HFDL 信号却一帧都解不出"的真正原因。**

OpenHFDL 源码里：

```c
src/hfdl.c:46   #define HFDL_SSB_CARRIER_OFFSET_HZ 1440
src/hfdl.c:476  float freq_shift = (float)(centerfreq - (frequency + HFDL_SSB_CARRIER_OFFSET_HZ))
                                     / (float)sample_rate;
```

即 **dumphfdl 认为 HFDL 信号中心在 `频道频率 + 1440 Hz`**（HFDL 的 SSB 约定：
指配频率是抑制载波，信号在其上方 1440 Hz）。

要让**绝对音频频率 A** 的信号落到 0 Hz，需满足
`centerfreq - freq = A - 1440`。取 **`centerfreq = 1.44 kHz`** 即得 `freq = A`
—— 频道频率直接填音频频率，且恒为正数（避免负频率被 dumphfdl 拒绝）。

**早期版本用 `--centerfreq 0` + 频道=音频频率，每个频道都偏了 1440 Hz**，
结果只能得到噪声级的前导相关值，永远解不出帧。修正前后对比（同一段 60 秒 WAV）：

| 指标 | `--centerfreq 0`（错） | `--centerfreq 1.44`（对） |
|------|----------------------|--------------------------|
| `A1_found` | 32 | **58** |
| `A2_found` | **0** | **21** |
| `M1_found` | **0** | **14** |
| `train_bits_bad` | 未进入训练阶段 | **7–15%**（已评估训练序列）|

修正后 dumphfdl 能完整捕获 A1→A2→M1 帧结构并评估训练序列 —— 说明频率映射终于正确。

> 代码中已修正：`soundcard_bridge.py` 的 `build_stdin_command()` 使用
> `HFDL_SSB_CARRIER_OFFSET_HZ = 1440`，命令行为 `--centerfreq 1.44`。

### 7.10 修正后仍未解出帧：信号质量不足

即使频率正确，本机这段录音仍解不出帧，原因是**信号质量处于门槛之下**：

| 指标 | 实测 | 正常需要 |
|------|------|----------|
| `A1_corr_avg` | 0.39 | ≈ 0.9 |
| `A2_corr_avg` | 0.37 | ≈ 0.9 |
| `M1_corr_avg` | 0.41 | ≈ 0.7 |
| `train_bits_bad` | 最好 **7.0%**，中位 14.7% | **< 1%** |
| `costas_dphi` | ±0.08~0.12 rad（符号间变号）| 稳定小值 |

前导相关只有 0.4（等效 SNR ≈ 0 dB）、训练误码 7–15%，FEC 无法纠正，故 CRC 不过。
已排除的因素：

- **不是解析变换精度**：FIR(n=511) 与理想 FFT 希尔伯特结果几乎相同
  （`python cmp_hilbert.py hfdl.wav`），train_bits_bad 中位 14.7% vs 14.7%
- **不是缺均衡器**：dumphfdl 有判决反馈 LMS 均衡
  （`src/hfdl.c:495-496` `eqlms_cccf_create_lowpass`，判决策略在 717-731 行）
- **不是频道没对准**：100 Hz 与 50 Hz 步进精细扫描结果一致
- **不是缓存**：见 7.8 节

**为什么 PC-HFDL 还能解？** PC-HFDL 手册明确写着它用
*Decision Feedback Equalisation*（第 100-108 行）来"undo the effects of the
ionosphere"，是为**传统接收机音频**这一场景专门调优的；而 dumphfdl 的均衡器
面向平坦的 SDR 通道。SSB 滤波器在 2.4 kHz 信号边沿造成的幅度/群延迟起伏，
PC-HFDL 的 DFE 能补偿，dumphfdl 则较难。

**提升信号质量的做法**（按收益排序）：

1. **IF 滤波器调到最宽**（FIL1 3.0 kHz）——KG-HFDL 说明书要求 ≥2.4 kHz/-3dB
2. **关闭 NR / NB / Notch / APF / COMP** —— NR 的谱减会严重破坏 PSK
3. **AGC 用 Fast 或 OFF** + 手动 RF 增益，避免 AGC 抽吸
4. **提高 USB AF 输出电平**：录音峰值目标 −12 ~ −6 dBFS（当前仅 −8.4 dBFS 峰值、
   −26 dBFS RMS，偏小）
5. 改善天线 / 换更强的地面站频率；**在 PC-HFDL 解码良好时录制**再回放验证
6. 若追求稳定解码，用 SDR 走 `--soapysdr`（OpenHFDL 的原生用法）

### 7.11 诊断工具汇总

| 命令 | 作用 |
|------|------|
| `python diag_dumphfdl.py --wav hfdl.wav` | 取 A1/A2/M1 相关值与 train_bits_bad |
| `python analyze_wav.py hfdl.wav` | 频谱/时间结构/包络统计 + 频谱图 |
| `python cmp_hilbert.py hfdl.wav` | 对比 FIR 与 FFT 解析变换 |
| `python test_hilbert_accuracy.py` | 希尔伯特各频点镜像抑制/幅度起伏 |
| `python test_wav_bridge.py hfdl.wav` | 用桥接回放 WAV（走完整链路）|

### 7.12 关于 `Aircraft cache TTL set to 3600 seconds`

**这条日志与能否解出帧无关，不要改它。** 从源码核实：

- `src/main.c:679` 打印该行；`src/ac_cache.c:62-63` 创建两个缓存
  `ac_fwd` / `ac_inv`，TTL 用 `Config.ac_cache_ttl`
- 选项说明（`src/main.c:298`）：
  `--aircraft-cache-ttl` —— "How long to keep **aircraft ID to ICAO mappings** in the cache"
- 它的作用是把 HFDL 报文里的**短飞机 ID 映射到 24 位 ICAO 地址**，仅用于给报文
  **标注飞机身份/注册号**。它**不是**帧缓冲区，把 TTL 调到 0 也不会多解出一帧。

真正决定能否解码的是物理层是否锁上（见下节）。

### 7.9 诊断：dumphfdl 到底有没有"看到"信号

`debug_print` 在 Release 构建里被编译成 `nop()`（`src/util.h:83`），
`hfdl_print_summary()` 也被 `#ifdef DEBUG` 包住。**要诊断必须构建 Debug 版**：

```bash
wsl -d Ubuntu-20.04 -u root -- bash /mnt/d/<路径>/aihfdl/build_debug_dumphfdl.sh
# 产出 /usr/local/bin/dumphfdl-debug
```

然后：

```bash
# 直接采声卡并诊断（默认扫描 400–2900 Hz）
python diag_dumphfdl.py --device 23 --seconds 40

# 或对已有录音诊断（推荐：PC-HFDL 正在解码时录的那段）
python diag_dumphfdl.py --wav hfdl.wav
```

它读取 dumphfdl 退出时打印的关键计数：

| 指标 | 含义 | 真实信号 | 噪声 |
|------|------|----------|------|
| `A1_found` | 找到 A1 前导的次数 | > 0，且相关值高 | 偶发，相关值低 |
| `A1_corr_avg` | A1 相关均值（门限 **0.36**） | 接近 **0.9** | 0.36~0.45（勉强越限）|
| `A2_found` | 找到 A2 序列（门限 0.3） | > 0 | 0 |
| `M1_found` | 解出帧类型（门限 0.3） | > 0 | 0 |

门限取自 `src/hfdl.c:42-44`：`CORR_THRESHOLD_A1 0.36`、`A2 0.3`、`M1 0.3`。

**本机实测（IC-7300，device 23，40 秒，13 频道扫描）**：

```
A1_found:       21
A2_found:        0
M1_found:        0
A1_corr_avg:     0.378      ← 仅比门限 0.36 高一点
```

结论：A1 相关值停在噪声水平（真实前导应接近 0.9），且门限更低的 A2/M1 一个都没
找到 —— **当时音频里没有可用的 HFDL 信号**，那 21 次是噪声误触发
（按 127 长序列相关分布估算，40 秒 × 13 频道 ≈ 59 次/百万次测试，量级吻合）。

### 7.10 已修复：希尔伯特变换在低频段精度不足

音频在 48 kHz 采样时，HFDL 只占 400–2900 Hz（归一化频率极低）。
原来的 127 抽头希尔伯特 FIR 在该频段过渡带占比过大，实测：

| 频率 | n=127 镜像抑制 | n=127 幅度起伏 | n=511 镜像抑制 | n=511 起伏 |
|------|---------------|---------------|---------------|-----------|
| 300 Hz | **−16.0 dB** | **2.77 dB** | −62.7 dB | 0.013 dB |
| 400 Hz | **−22.9 dB** | **1.25 dB** | −61.5 dB | 0.015 dB |
| 600 Hz | −44.8 dB | 0.10 dB | −69.0 dB | 0.006 dB |
| 1441 Hz | −57.8 dB | 0.02 dB | −70.7 dB | 0.004 dB |

低频段的镜像泄漏与幅度起伏会破坏 PSK 星座。**默认抽头数已从 127 改为 511**，
全频段优于 −60 dB。可用以下命令自查：

```bash
python test_hilbert_accuracy.py
```

### 7.11 决定性验证步骤（强烈建议先做）

既然 PC-HFDL 能稳定解码，就用**同一段音频**做 A/B：

```bash
# 1) 让 PC-HFDL 正常解码，同时录制那段音频
python check_audio.py --capture 23 --seconds 60 --save-wav hfdl.wav

# 2) 对同一段音频做诊断（看 A1 相关值）
python diag_dumphfdl.py --wav hfdl.wav

# 3) 再走桥接解码
python soundcard_bridge.py --wav hfdl.wav --mode analytic
```

- `A1_corr_avg` 接近 0.9 且能解出帧 → 桥接正确，之前的 0 帧只是当时无信号
- `A1_corr_avg` 仍在 0.4 左右 → 说明**我采到的音频与 PC-HFDL 看到的不一致**
  （设备/主机 API/Windows 音效处理不同），据此继续排查

> Windows 上「麦克风」类端点可能被施加**音频增强**（AGC / 降噪 / 回声消除），
> 这会破坏 PSK 信号。请在「声音设置 → 录制 → 该设备属性 → 高级」里
> **关闭所有音频增强**，或在 GUI 里勾选 **WASAPI 独占模式**绕过音频引擎。

### 7.13 ★ 用 SDRuno 的 I/Q 输出作为 OpenHFDL 输入

**结论：可以，而且这是最好的一条路。** SDRuno 输出的是**真正的复基带 I/Q**，
正是 dumphfdl 的原生输入形态 —— 不需要希尔伯特变换、无损、采样率最高 192 kHz。

#### 7.13.1 IQ OUT 只能选虚拟声卡吗？—— **不是。任何输出设备都能选**

官方手册 **§3.5 "Selecting an output device"**（第 44 页）原文：

> Each VRX can have its own WME output device. More than one VRX can share the same
> WME device. **Output device can be selected in RX Control → Settings → Out Tab.**
> If no device is selected (default) the VRX will use the system default (sound mapper).
> SDRuno must have an output device.

**配置位置：`RX Control` → `SETT`（Settings）→ `Out` 选项卡 → 选 WME 输出设备**
（每路 VRX 各自设置；默认 = 系统默认 sound mapper）。
`Out` 分组也可在手册 §2.2.1 "RX Control Settings"（第 17 页）看到。

**那为什么手册推荐虚拟声卡？** 因为 IQ OUT 是把 I/Q 经**Windows 音频输出通道**
送出的（手册 §6，第 63 页）：

> the filtered I/Q signal selected by the VFO is piped directly to the **audio output**
> ... **I-channel → Left Audio channel, Q-channel → Right Audio channel**

物理输出设备只会把它送到喇叭，**别的程序抓不到**。所以要有"环回"路径：

| 环回方式 | 是否可用 |
|----------|----------|
| **虚拟声卡**（VB-CABLE / VAC / VoiceMeeter） | ✅ 手册推荐；本机**尚未安装** |
| Windows「立体声混音 / Stereo Mix」 | ❌ 你的声卡未暴露该设备（已枚举确认） |
| WASAPI Loopback | ❌ PortAudio/sounddevice 不直接支持 |

> 注意：手册中 "virtual audio" 一词**只出现在第 63 页**（IQ OUT 那页），
> 而 IQ OUT 本身只在该页与第 16 页被提及 —— 也就是说这是唯一的官方说明。

#### 7.13.2 ★ 免虚拟声卡的第三条路：SDRuno 自带 I/Q 录制

手册 **§2.6 "Scheduler & Recorder"**（第 24 页）原文：

> An event can be either to play the I/Q stream from an attached RSP, **record the
> I/Q stream** or operate the frequency scanner.
> **Record: Starts the manual recording of an IQ stream.**
> **RECORD FOLDER: Specifies the default user folder used to save recorded IQ files.**
> Playback ... operates as before with an **I/Q WAV file** being selected as an input source.

**所以不需要装任何东西就能用**：

1. SDRuno 调到 HFDL 频段，点 **SCHEDULER / RECORDER** 面板的 **Record**，
   或配置定时事件自动录制 → 得到 **I/Q WAV** 文件
2. 用桥接回放该 WAV：

```bash
python soundcard_bridge.py --wav <SDRuno录制的IQ.wav> --mode stereo-iq
```

或 GUI 里：解码后端选「OpenHFDL + 声卡桥接」→ 信号来源选 **SDR I/Q** →
在「I/Q 文件」里选录制的 WAV → 填 VFO 频率 → 开始解码。

桥接已支持 SDRuno 的 I/Q WAV（含 **float32** 格式，`read_wav_any()`；
Python 自带 `wave` 模块读不了 float WAV）。自测：

```bash
python test_wav_reader.py     # float32 立体声 I/Q / PCM16 单声道 / PCM16 立体声
```

#### 7.13.3 实时方案（IQ OUT + 虚拟声卡）设置步骤

**① 安装虚拟声卡**（本机尚无 VB-CABLE/VAC/VoiceMeeter，已枚举确认）

- **VB-CABLE**（免费）：<https://vb-audio.com/Cable/>
- 装好后出现 **CABLE Input**（播放设备）与 **CABLE Output**（录音设备）

**② SDRuno 侧**

1. `RX Control` → 模式选 **IQ OUT**
2. 子模式选 **WFM**（取高采样率；NFM 只能取低端）
3. 确认 `Final SR` ≥ 目标 IQ 率（IQ 率不能大于 Final SR）
4. **`RX Control` → `SETT` → `Out` 选项卡 → 输出设备选 `CABLE Input`**
5. 调谐到 HFDL 频段（VFO = 要听的频段中心）

**③ 本程序侧**

| GUI 项 | 取值 |
|--------|------|
| 解码后端 | `OpenHFDL + 声卡桥接 (声卡→I/Q→dumphfdl)` |
| **信号来源** | **`SDR I/Q（SDRuno IQ OUT 等）`** |
| 桥接模式 | 自动锁定 `立体声 I/Q`（I/Q 源不允许其它模式）|
| 采集设备 | **`CABLE Output`** |
| 采样率 | **192000**（与 SDRuno IQ OUT 率一致）|
| **VFO 频率 (kHz)** | SDRuno 当前调谐频率，如 `8901` |
| 频道列表 | HFDL **射频频率**（kHz），如 `8825 8927 8957 …` |

命令行等价：

```bash
dumphfdl --iq-file - --sample-format CS16 --sample-rate 192000 \
         --centerfreq 8901 --system-table systable.conf \
         --output decoded:json:file:path=- --utc \
         8825 8834 8843 8885 8894 8912 8921 8927 8936 8939 8942 8948 8957 8977
```

#### 7.13.4 两种来源的频率约定不同（**关键**）

程序已按「信号来源」自动切换：

| 信号来源 | `--centerfreq` | 频道频率 | 说明 |
|----------|----------------|----------|------|
| **SDR I/Q**（IQ OUT / I/Q WAV） | **VFO 频率**（如 8901 kHz） | **HFDL 射频频率**（如 8927 kHz）| 原生用法，1440 Hz 偏置由 dumphfdl 自理 |
| 声卡音频（实信号） | **1.44**（=1440Hz 偏置常量） | **音频频率**（如 1.441 kHz）| 见 7.9 节 |

用错了会整体偏 1440 Hz —— 这正是此前"有信号却解不出"的原因（见 7.9 节）。

#### 7.13.5 为什么 I/Q 明显优于声卡音频

| 项目 | SDRuno I/Q（IQ OUT / I/Q WAV） | 声卡音频（SSB 解调后） |
|------|-------------------------------|----------------------|
| 信号形态 | 复基带 I/Q（原生） | 实音频，需希尔伯特变换 |
| 带宽 | 最高 192 kHz，**可同时解多个 HFDL 频道** | 受 SSB 滤波器限制 ~2.4–3 kHz |
| 滤波器失真 | **无**（旁路解调） | 有（IF 幅/相起伏 → ISI）|
| AGC/音效 | **不受影响** | 受 AGC/NR/音效影响 |
| 多频道 | 一次可解多个 HFDL 频道 | 一次一个信号 |

> **I/Q 才是 OpenHFDL 该用的输入**。声卡音频路径（7.1–7.12 节）是为"只有接收机
> 音频"的场合准备的退路。

### 7.14 参考：PC-HFDL / KG-HFDL 的关键要求

从本机已安装的 PC-HFDL 手册与 KG-HFDL 说明书中提取：

| 项目 | PC-HFDL | KG-HFDL |
|------|---------|---------|
| 采样率 | **48000 Hz**（手册："Sample rate changed to 48000 to allow better soundcard compatibility"） | 声卡输入（`kghfdl.ini` 的 `[Wave] Input=1`） |
| 模式 | USB | **USB 模式**（说明书：「無線機をUSBモードに設定します」） |
| IF 滤波器 | — | **≥ 2.4 kHz / -3 dB**（说明书：「受信フィルターの帯域は2.4kHz/-3dB以上のもの」） |
| 频率校正范围 | — | 自动校正，但**仅 ±60 Hz**（说明书：「±60Hz程度しか補正を行いません」） |
| 信号定位 | 自动在通带内搜索 | 自动在通带内搜索 |

> 这解释了关键差异：两套工具都**自动搜索**音频带内信号，所以电台**不需要**
> 精确把 HFDL 放在某个音频频率上；而 dumphfdl 必须显式指定频道。本桥接的
> 「带内自动搜索」就是为对齐这一行为而加。

---

## 7. 许可与致谢

- **OpenHFDL / dumphfdl** —— Tomasz Lemiech (szpajder)，GPL-3.0
  <https://github.com/szpajder/dumphfdl>
- **libacars** —— 同作者，用于 ACARS/HFDL 报文解析
- **Natural Earth** —— 110m 国家边界数据（公有领域）
- 内置教学解调器为本项目自研，仅用于教学演示，非位级兼容实现；
  已从 GUI 后端列表移除（接真实信号解不出报文，见第 3 节）。
