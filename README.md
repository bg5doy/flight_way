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

**选中高亮**

在右下角「当前飞机」列表中**单击某航班**，地图上立刻高亮该目标：

| 元素 | 普通 | 选中 |
|------|------|------|
| 飞机图标 | 青色，基准尺寸 | **金琥珀色，放大 1.45 倍** |
| 高亮光环 | 无 | **琥珀色虚线圆环** |
| 航迹 | 细线、按时间渐变 | **加粗 2.3 倍、更亮、压在最上层** |
| 标签 | 青色底板 | **琥珀边框 + 粗体**，且**必定显示**（不受缩放策略限制）|
| 其它飞机 | 正常 | 适当压暗，突出高亮目标 |

- **双击**某航班 → 地图居中并放大到约 20° 视野，便于看清航迹
- **在地图上单击**（非拖拽）→ **取消高亮**；拖拽平移不会误触发
- 选中项**过期擦除后自动取消高亮**，状态栏也会显示当前高亮目标
- 列表每秒刷新时**只更新数值列、不重建行**，因此选中状态与滚动位置都保持不变

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

#### 航班号显示：HFDL 的 flight_id ≠ 商业航班号

HFDL 性能报文里的 `flight_id` **不是统一格式** —— 它是机组在航空电子设备里
自行配置的标识。实测本机解出的数据里**同时存在三类**：

| 类型 | 样例 | 说明 |
|------|------|------|
| **ICAO 呼号**（3 字母航司码） | `AAL100` `ANA107` `ACA025` `AIC357` `APJ571` | 美/日/欧航司常用；需 `ICAO→IATA` 转换才是商业航班号 |
| **IATA 商业航班号**（2 字符航司码） | `CA1721` `MU5104` `3U3192` `BX0111` | 中/韩/东南亚航司常用；**4 位号码只在这种形式下才完整** |
| **注册号 / 特殊编码** | `B-1234` `N12345` `D00031` | 公务机/货机/测试编码，原样显示 |

对应关系（实测样例）：

```
AAL100  →  AA100      （查航司码表）
SIA322  →  SQ322      （查航司码表）
ACA025  →  AC25       （去零填充）
CA0433  →  CA433      （去零填充）
MU5104  →  MU5104     （本来就是商业航班号，原样保留）
3U3192  →  3U3192     （同上）
```

##### ⚠ HFDL 的 6 字符硬上限（决定了号码位数）

HFDL Performance Data PDU 固定 47 字节，其中 `flight_id` **只占 6 个字节**
（OpenHFDL `src/hfnpdu.c`：`PERFORMANCE_DATA_HFNPDU_LEN 47`、
`memcpy(result->flight_id, buf + 2, 6)`）。因此：

| 形式 | 结构 | 号码最多 | 例 |
|------|------|---------|-----|
| ICAO 呼号 | 3 字母 + N 位 | **3 位**（3+3=6） | `CCA981` → CA981 ✓ |
| IATA 航班号 | 2 字符 + N 位 | **4 位**（2+4=6） | `CA1704` → CA1704 ✓ |

所以 **`CCA1704` 这 7 个字符物理上装不进报文**：国内 4 位航班号（CA1704、
MU5104、MF3558）只有在机组配置成 **IATA 形式**时才能完整出现；若配成 ICAO
三字码形式，第 4 位数字在报文里根本不存在，**无法还原**（补位等于编造）。

实测数据印证：**548 个 flight_id 无一超过 6 字符**；446 个 ICAO 形式呼号
**全部 ≤3 位数字**；102 个 IATA 形式里有 **97 个 4 位号码，且均正确显示 4 位**
（`CA1704` `MU5104` `CZ3502` `3U3053` `FM9134` `KN2918` `ZH8980`…）。

**同航司两类形式并存，直接证明 3 位是 4 位的前 3 位**（末位被 6 字节字段吃掉）：

| 航司 | ICAO 形式（全部 3 位） | IATA 形式（全部 4 位） |
|------|----------------------|----------------------|
| 四川航空 | `CSC312` `CSC315` `CSC316` …（57 个） | `3U3053` `3U3183` `3U3192` …（24 个）|
| 中国国航 | `CCA110` `CCA123` `CCA433` …（69 个） | `CA1704` `CA1721` `CA4062` …（14 个）|
| 东方航空 | `CES530` `CES531` `CES567` …（135 个） | `MU2211` `MU5104` `MU5336` …（20 个）|
| 上海航空 | `CSH816` `CSH898` `CSH920` | `FM9120` `FM9134` `FM9402` … |

也就是说 `CSC3126`（7 字符）被裁成 `CSC312`；而同一家公司另一架飞机若是
按 IATA 形式填的 `3U3126`（6 字符）就能完整送达。**第 4 位数字在报文里
不存在，任何"补全"都是编造**，因此本程序如实显示 3 位。

`parse_flight_id()` 用 `capped` 标记"号码已顶到 6 字符上限"的情况。

##### 界面怎么看出代码来源

「当前飞机」列表有两列并排显示，来源一目了然：

| 航班(IATA) | 报文原始代码 | 含义 |
|-----------|-------------|------|
| `CA1704` | `CA1704` | IATA 形式，4 位完整 |
| `MU530` | `CES530` | ICAO 形式，报文里只有 3 位 |
| `AA100` | `AAL100` | ICAO 形式转 IATA |
| `SQ322` | `SIA322` | ICAO 形式转 IATA |

##### 界面选项

`应用 / 地图显示` 面板 → **航班号显示**，默认即为 IATA：

| 选项 | 效果 |
|------|------|
| `商业航班号 (IATA)`**（默认）** | 报文代码转 IATA 码，如 `CCA981`→`CA981`、`CES510`→`MU510`；4 位号码原样保留 |
| `HFDL 原始 ID` | 显示报文原文（`CCA981` / `AAL100`），用于与原始报文核对 |
| `两者都显示` | 如 `CA981 (CCA981)`，可同时对照 |

切换后**地图标签、「当前飞机」列表、历史查询结果三处同步更新**（下拉框即时生效，
点「应用参数」才写入配置）。航司码表见 `flight_id.py`（190+ 家航司），
**未收录的航司原样回退显示、绝不猜测** —— 原始呼号是准确信息，编造的航班号是错的。

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
| `test_gui_highlight.py` | 列表选中→地图高亮：图标/光环/航迹/标签、选择保持、双击定位 |
| `test_flight_id.py` | HFDL flight_id 解析：ICAO 呼号/IATA 商业号/注册号识别与转换 |
| `test_sdr_rsp.py` | SDRplay 结构体布局（对照 C 头文件黄金值）+ API + 真机采集 |
| `test_rsp_band.py` | RSP 波段自动匹配：频道是否跟着中心频率走 |
| `test_rsp_tune.py` | RSP 参数优化：默认值、各波段带宽推荐、一键自动优化 |
| `test_rsp_decim.py` | RSP 硬件抽取：各波段抽取合法性校验 + 真机输出速率实测 |
| `test_map_throttle.py` | 地图重绘节流：无新帧时降频、有新帧立即重绘 |
| `test_last_frame.py` | 「最新帧 (UTC)」列：显示最后一帧接收时刻而非秒数 |
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

## 8. SDRplay RSP 直采（I/Q）

RSP 系列是 SDR，**直接输出复基带 I/Q**，比"声卡桥接"少一次模拟往返，是解 HFDL 最好的一路。

### 8.1 为什么不用 SoapySDR

RSP **没有纯开源驱动**：硬件必须经 SDRplay 官方闭源服务
（Windows 下 `SDRplayAPIService` + `sdrplay_api.dll`）。开源方案
（[SoapySDRPlay3](https://github.com/pothosware/SoapySDRPlay3)、gr-sdrplay3）
只是这个闭源 API 的开源**封装**，还要额外装 SoapySDR 运行时。

本项目用 **ctypes 直接绑定 `sdrplay_api.dll`**（`sdr_rsp.py`）：

- 免装 SoapySDR，零额外依赖
- 直接拿 int16 I/Q → CS16 交织 → `dumphfdl --iq-file -`
- 运行中可调频率/增益（走 `sdrplay_api_Update`）

数据流：

```
RSP 硬件 → sdrplay_api 回调(int16 I/Q) → CS16 交织 → dumphfdl stdin
         → HFDL JSON → 地图 / SQLite
```

### 8.2 环境要求

| 项目 | 要求 |
|------|------|
| SDRplay API | **3.x**（3.15 实测）；服务 `SDRplayAPIService` 必须在运行 |
| DLL 路径 | 自动查找 `D:\HAM\SDRplay\API\x64\sdrplay_api.dll` 等常见位置，也可用环境变量 `SDRPLAY_API_DLL` 指定 |
| dumphfdl | 仍需 WSL/Linux 下的 dumphfdl 做 HFDL 解调 |

自检（无硬件也能跑，验证绑定与枚举）：

```bash
python sdr_rsp.py          # 打印 DLL/API 版本、设备列表、结构体大小
python test_sdr_rsp.py     # 完整测试：布局黄金值 + API + 真机采集
```

### 8.3 GUI 使用

1. 解码后端选 `SDRplay RSP 直采 I/Q（SDRplay API）`
2. 点「刷新」枚举设备（显示 `[序号] 型号 序列号`）
3. 设 **中心频率 / 采样率**，面板底部会实时显示"覆盖 xxx–xxx kHz（某波段），将监听 N 个频道"
4. 其余参数：

| 参数 | 说明 |
|------|------|
| 采样率 | 1.536 / 2 / 5 / 6 / 8 MSPS。2 MSPS 已足够覆盖单个 HFDL 段的全部频道 |
| **硬件抽取** | 1/2/4/8/16/32×。API 内部数字滤波后降采样，**数据率按倍数下降**，信号不损失。默认 8×（2 MSPS → 250 kSPS）|
| 中频模式 IF | **Zero-IF（HF 推荐）** / 450 / 1620 / 2048 kHz |
| 模拟带宽 | 200 / 300 / 600 / 1536 / 5000 / 6000 / 7000 / 8000 kHz |
| AGC 模式 | 关闭 / 100 / 50 / 5 Hz |
| 增益 gRdB | **增益衰减量**（越小增益越大），20 = 最大增益 |
| LNA 状态 | 依型号与频段而变。**RSP1 在 8.9 MHz 只有 0–3**，越界会自动降到 0 重试 |
| 天线口 | RSP2/RSPdx 有多个（A/B/C），RSP1 单口 |
| 偏置 T | 给有源天线供电 |

#### ★ 频道自动跟随波段（重要）

dumphfdl 必须**显式给出频道频率**。早期版本只把「频道预设」里那组频率拿去过滤，
于是你把中心频率调到 21.9 MHz 时，预设还是 8.9 MHz 段那 14 个频道，一个都落不进
采样窗口，就报"没有任何 HFDL 频道落在其中"——但该频率上其实有信号。

现在改为：**窗口内的频道 = 预设频道 ∪ 全波段系统表频道**（两者取并集），
预设作为必含项，系统表补齐预设漏收的频道（例如预设少了 `8886`）。

#### 各波段推荐中心频率（已避开 DC 尖峰）

程序可按 `hfdl_presets.recommend_center_khz()` 自动算，实测结果：

| 波段 | 推荐中心 | 频道数 | 备注 |
|------|---------|-------|------|
| 2.9 MHz | 2977 | 7 | |
| 3.4 MHz | 3476 | 2 | |
| 4.6 MHz | 4670 | 4 | |
| 5.5 MHz | 5574 | 14 | 跨度 269 kHz，2 MSPS 全含 |
| 6.5 MHz | 6610 | 13 | |
| **8.9 MHz** | **8903** | **15** | 默认值 |
| 10.0 MHz | 10051 | 9 | |
| 11.3 MHz | 11339 | 9 | |
| 13.2 MHz | 13294 | 10 | |
| 17.9 MHz | 17943 | 10 | 日间常用 |
| 21.9 MHz | 21964 | 9 | 日间强信号 |

> 把中心频率设成某个**频道本身**（如 21928）会撞上 DC 尖峰，界面会提示挪开。
> 用上表的推荐值即可。

#### 过载（overload）提示

日志里若出现 `overloads` 计数增长，说明 ADC 过载。**过载是解码数量偏低的首要原因**：
实测同一时段 21.9 MHz 段，关闭 AGC 用最大增益时 120 秒内过载 **238 次**，
突发对比度从 34 dB 崩到 2 dB；开启 AGC 后过载 **0 次**、对比度恢复 34 dB。

因此**默认开启 AGC（50 Hz）**。只有手动调增益时才关 AGC；此时 gRdB=20 适合
2–10 MHz 弱信号段，17/21 MHz 强信号段建议 gRdB 35–50。

#### ★ 参数优化：实测 A/B（同一时段各 120 秒，21.9 MHz 段）

| 配置 | 中心频率 | 带宽 | AGC | 过载 | **解码帧数** |
|------|---------|------|-----|------|------------|
| 旧默认 | 21928（= 频道本身）| 1536 kHz | 关，gRdB=20 | **238** | 12 帧 |
| **优化后** | **21964**（两频道之间）| **200 kHz** | **50 Hz** | **0** | **46 帧** |

**提升 +283%（12 → 46 帧）**。三项改动各自的作用：

| 改动 | 作用 |
|------|------|
| **AGC 开启** | 消除 ADC 过载（主因，238 → 0 次）|
| **中心避开频道** | 释放被 DC 尖峰淹没的 21928 频道 |
| **带宽 1536 → 200 kHz** | 少进噪声，突发对比度约 +1 dB |

> 参数扫描数据（2.5 s/组，突发对比度 = 各频道 200 ms 切片 max/median 的中位数）：
> AGC 开时 200/300/600/1536 kHz 分别为 34.3 / 34.2 / 32.6 / 33.1 dB；
> AGC 关 + gRdB=20 时全部只有 ~2 dB 且过载 6 次。

#### 一键自动优化

面板上的 **「⚡ 按当前波段自动优化」** 会一次配好三件事：

1. 中心频率 → 该波段两个频道之间、距最近频道 ≥ 9 kHz（避开 DC 尖峰）
2. 模拟带宽 → 覆盖全部频道的最小档（各波段推荐见上表）
3. AGC → 50 Hz

启动解码时，若检测到"中心与频道重合 / 带宽不足 / AGC 关闭"，日志里也会给出
对应的 ⚠ 提示。

### 8.4 ★ 关键坑：零中频 DC 尖峰

实测 RSP1 在本振频率处有一个**强 DC 尖峰：约 +30 dB、约 13 kHz 宽**。
若某个 HFDL 频道正好落在中心频率上，会被这个尖峰彻底淹没。

实测证据（同一台设备，改中心频率后尖峰跟着走）：

| 中心频率 | 最强峰位置 | 相对底噪 |
|---------|-----------|---------|
| 8927 kHz | **+0.0 kHz**（正好在中心） | +33.1 dB |
| 8900 kHz | **+0.0 kHz**（正好在中心） | +31.6 dB |

**结论**：无论调到哪个频率，最强峰都恒定在 **+0.0 kHz**，所以它是本振泄漏（DC 尖峰）
而非真实信号。它高于底噪约 30 dB，"高于底噪 6 dB"的宽度约 **13 kHz**。

**处置**：中心频率要设在**两个频道之间，且距最近频道 ≥ 9 kHz**（尖峰半宽约 6.5 kHz）。
本程序默认 **8903 kHz** —— 8.9 MHz 段 15 个频道里，它距 8894 和 8912 各 9 kHz，
频道偏移范围 −78…+74 kHz，最接近居中。若手动把中心设到与某频道重合（±8 kHz 内），
面板会给出 ⚠ 警告。

> 简单高通/DC 阻断去不掉它（尖峰比高通带宽宽得多），所以不要指望"加个 DC 阻断"就完事，
> 正确做法是让频道远离本振。

### 8.5 ★ 硬件抽取（decimation）—— 降负载最有效的一招

带宽只用 200 kHz，却按 2 MSPS 传输，**4 倍数据是白搬的**。开启 RSP 的硬件抽取后，
API 内部先做数字滤波再降采样，**USB 流量、管道写入、Python 交织负载都按倍数下降**。

实测（RSP1，中心 21964 kHz，BW 200 kHz，各 3 秒）：

| 抽取 | 实测输出 | 数据率 | 突发对比度 |
|------|---------|--------|-----------|
| 1× | 1718 kSPS | 6.87 MB/s | 35.8 dB |
| 2× | 865 kSPS | 3.46 MB/s | 34.3 dB |
| **4×** | 432 kSPS | 1.73 MB/s | 35.5 dB |
| **8×** | 215 kSPS | **0.86 MB/s** | 33.8 dB |
| 16× | 107 kSPS | 0.43 MB/s | 35.3 dB |
| 32× | 54 kSPS | 0.21 MB/s | 37.9 dB |

**抽取不损失解码能力**（对比度 33.8–37.9 dB，与不抽取相当），但数据率降到 1/8。

端到端实测（8×（250 kSPS）喂 dumphfdl，20 秒）：

```
dumphfdl --iq-file - --sample-format CS16 --sample-rate 250000 --centerfreq 21964 ...
RSP 采集: samples=4,983,552 / 20s = 249 kSPS   零丢块、零过载
解码 7 帧
```

**两条硬约束**（程序会自动算，也可手工判断）：

1. **抽取后采样率 ≥ 模拟带宽** —— 否则带外信号混叠进来
2. **抽取后采样率的一半 > 最远频道偏移** —— dumphfdl 要求频道落在半带宽内

各波段自动推荐结果（`hfdl_presets.recommend_decim`，全部满足上述两约束）：

| 波段 | 推荐抽取 | 输出 | 说明 |
|------|---------|------|------|
| 2.9 / 3.4 / 3.9 / 4.6 MHz | 8× | 250 kSPS | |
| 5.5 MHz | 2× | 1000 kSPS | 频道跨度 269 kHz，只能轻抽 |
| 6.5 MHz | 4× | 500 kSPS | |
| 8.9 / 10.0 / 11.1 / 11.3 / 13.2 / 15.0 / 17.9 / **21.9** MHz | **8×** | **250 kSPS** | 默认 |

> 想更省可以手工选 16×/32×（带宽 200 kHz 时输出 125/62.5 kSPS 已低于带宽，
> 会混叠 —— 界面会提示"抽取过头"，此时应同时把带宽调到 100 kHz 以下）。

### 8.6 性能：必须向量化交织

2 MSPS = 8 MB/s 的 I/Q。若在 API 回调里用 Python 逐样本交织，**会丢掉 97% 的数据**
（实测：采集 46045 块只有 1292 块送进 dumphfdl）。改用 numpy 向量化后：

```
RSP 采集 : {'blocks': 49440, 'samples': 49835520, 'dropped': 0}
写入     : {'written': 49440, 'dropped': 0}     ← 零丢弃
```

写入端也已合并成大块（≥64 KB）以减少系统调用。

#### ★ 队列溢出（"已丢 N 块"）的真正原因

日志里出现 `[rsp] 队列溢出，累计丢 N 块` 时，**原因不是 CPU 算不过来，而是写线程被
GIL 卡住了**。实测复现：让后台线程模仿 `matplotlib` 每秒重绘地图（纯 Python 忙循环
0.4 s，期间无法释放 GIL），写线程就完全停摆：

| 抽取 | 队列深度 | 模拟 GUI 占 GIL | 丢块 |
|------|---------|----------------|------|
| 8× | 128（旧默认）| 是 | **111** |
| 8× | 512 | 是 | 10 |
| **8×** | **2048（现默认）** | 是 | **0** |
| 1× | 128 | 是 | 0 |

**两项修复**：

1. **队列深度 128 → 2048 块**（≈8 MB，可吸收数秒停顿）
2. **队列满时丢最旧的块**，而不是丢新块 —— 直播解码时新数据更有价值，
   丢旧数据只造成一次时间空洞，不会让解码延迟越积越大
3. **地图无新帧时不重绘**：`_tick` 里只有收到新帧才立刻重绘，否则最多每 5 秒
   重绘一次（仅用于航迹淡出）。实测无新帧时 7 秒内重绘次数从 7 次降到 **1 次**

另外启动时会**等 dumphfdl 打印初始化完成**（约 1 s）再开始推流，避免启动瞬间
把管道灌满。若告警仍持续增长，说明确实处理不及，可提高抽取倍数或降低采样率
（告警文本会直接给出当前输出速率与建议）。

### 8.7 本机实测数据（RSP1, SN 0000000001）

| 项目 | 结果 |
|------|------|
| ctypes 结构与官方头文件一致性 | **29 个结构体 sizeof + 44 个字段 offset 全部一致**（用 WSL gcc 编译官方头文件做黄金值） |
| API | SDRplay API 3.15，服务运行中 |
| 2 MSPS 采集 | 49,440 块 / 49.8M 样本，零丢弃 |
| 跨波段信号 | HFDL 8.9M +13.8 dB、40m +15.0 dB、31m 广播 +22.0 dB、20m +23.5 dB、15m +27.3 dB |
| 结论 | 天线与链路正常 |

> 说明：**能否解出帧取决于当时有无 HFDL 突发与传播条件**。HFDL 是 TDMA 突发，
> 单频道每秒只有一个时隙，30 秒窗口内没有可解突发属正常现象；
> 需要长时间挂机（配合多频道同时监听）才能稳定出帧。

---

## 9. 许可与致谢

- **OpenHFDL / dumphfdl** —— Tomasz Lemiech (szpajder)，GPL-3.0
  <https://github.com/szpajder/dumphfdl>
- **libacars** —— 同作者，用于 ACARS/HFDL 报文解析
- **SDRplay API** —— 官方闭源运行时（免费），`sdr_rsp.py` 用 ctypes 直接调用，
  **不包含也不修改其二进制**
- **Natural Earth** —— 110m 国家边界数据（公有领域）
- 内置教学解调器为本项目自研，仅用于教学演示，非位级兼容实现；
  已从 GUI 后端列表移除（接真实信号解不出报文，见第 3 节）。
