<div align="center">

# 🎯 Lizzieyzy 工具箱 · 新规先行版

**非官方个人维护包：Lizzieyzy 2.5.3 + KataGo 引擎 —— 修复形势判断，新增截图摆棋 / 联机对弈 / 实时盯盘同步，可选 transformer 引擎；2027 中国规则先行支持**

> 本项目与 Lizzieyzy 官方无关；核心对局与分析功能均由 Lizzieyzy 提供（未修改其源码），本仓库维护的是修复与扩展。

[![Release](https://img.shields.io/badge/release-v1.2-blue.svg)](../../releases)
[![Fork of Coderchen-001/AI_GO](https://img.shields.io/badge/fork%20of-AI_GO%20v1.1-blue.svg)](https://github.com/Coderchen-001/AI_GO)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](./LICENSE.txt)

**中文** | [English](./README_EN.md) | [日本語](./README_JA.md) | [한국어](./README_KO.md)

</div>

---

一个解压即用的围棋 AI 整合包：**Lizzieyzy** 图形界面提供核心对局与分析
功能（本项目未修改其源码），**KataGo** 提供引擎，并自带 Java 运行环境。
本仓库的贡献 = **修复**（形势判断引擎配置）+ **扩展**（截图摆棋 / 联机对弈 /
实时盯盘同步三件自研工具）+ **集成**（transformer 引擎 T18/T28 选项）。

---

## ✨ 核心特性

- **四引擎配置**：内置 KataGo **18b / 28b** 经典权重，以及新一代
  transformer 系列 **T18 (tf2-b10c384)** 与 **T28 (tf3-b10c512)**
  （需 KataGo 1.18.1，已内置），软件内一键切换
- **一键性能调优**：双击 `点我首次运行调优.bat` 自动完成（v1.1）
- **真正便携**：自带 OpenJDK 17，解压即用，可放 U 盘
- **跨显卡兼容**：OpenCL 通用版，支持 NVIDIA / AMD / Intel 主流显卡
- 🆕 **截图摆棋**（v1.2）：截图 → 自动识别棋盘 → 一键在 Lizzieyzy 中摆出局面
- 🆕 **联机对弈**（v1.2）：与好友局域网/互联网对弈，可选 AI 实时胜率面板，支持 2027 中国规则判定（平局/先停手规程），
   支持 2027 中国规则判定（平局/先停手规程）
- 🆕 **实时盯盘**（v1.2）：选定任意窗口，每隔几秒自动识别其中的棋盘并实时摆盘、
  实时 KataGo 分析，随窗口中棋局进展动态更新
- 🆕 **形势判断修复**（v1.2）：修复了 v1.1 中形势判断引擎无法加载的问题

---

## 🚀 快速开始

1. 解压到任意位置（**建议路径不含中文和空格**）
2. 双击 `Lizzieyzy-2.5.3-win64.exe` 启动
3. 引擎下拉框切换 **18b / 28b / T18 / T28** 四个引擎，开始下棋或导入 SGF 分析
   （T18/T28 为新一代 transformer 引擎：同档位每访问棋力更高，T18 参数量更小；
   Intel 核显上 OpenCL 优化未成熟、原始速度略慢，NVIDIA 卡上会更快）
4. ⭐ 强烈推荐：双击 `点我首次运行调优.bat` 完成性能调优（约 2~6 分钟）

> 首次启动会自动进行 OpenCL 调优（3~5 分钟），属正常现象，请耐心等待。

---

## 🆕 v1.2 新功能

### 1️⃣ 形势判断（已修复）

v1.1 中该功能因缺少专用估计引擎配置而不可用，v1.2 已修复并经真实环境
端到端验证。

- 在 Lizzieyzy 中按 **`/` 键**触发形势判断，再按一次取消
- 引擎常驻后台，首次约 3 秒，之后瞬时刷新
- 详细用法见 Lizzieyzy 自带说明文档

### 2️⃣ 截图摆棋（Screenshot2Board）

看到任何棋局截图/直播画面，想让 KataGo 直接分析？双击根目录
**`截图摆棋.bat`**：

1. `Win + Shift + S` 截图 → 点「**从剪贴板粘贴**」（或「选择图片」）
2. 自动识别棋盘与黑白子（19/13/9 路自动判断）
3. 点「**在 Lizzieyzy 中打开**」——局面即刻摆好，开始分析

识别错误时：点击预览棋盘上的交点即可手动纠正（空→黑→白循环）；
斜拍/复杂图片可用「四角校准」模式。本地 OpenCV 识别，测试语料准确率
**100%**，详见 [`screenshot2board/README.md`](./screenshot2board/README.md)。

### 3️⃣ 联机对弈（Netplay）

和好友在线下棋，双方都用本工具即可，无需任何服务器：

1. 双击 **`联机对弈.bat`**，主机方选「创建对局」（界面会显示 IP 和端口）
2. 对方选「加入对局」，输入主机 IP 即可连线
3. 内置完整规则引擎（提子/禁自杀/劫），支持悔棋请求、认输、聊天；
   **2027 中国规则判定**（黑贴 3.5 子、黑 184 子平局、平局可按规程
   判白胜或先停手方胜，终局可点击标注死子后重算，结果写入 SGF RE）
4. 可选开启 **AI 面板**：每手棋后显示胜率与势力归属热图
5. 对局结束保存 SGF，一键在 Lizzieyzy 中复盘

> 同一局域网直接连接（首次弹出的防火墙提示请点「允许」）；跨公网需
> 主机端做端口映射（默认端口 19864）或使用内网穿透工具。详见
> [`netplay/README.md`](./netplay/README.md)。

### 4️⃣ 实时盯盘（BoardWatch）

想让 KataGo 跟着任意窗口里的对局实时分析？双击根目录 **`实时盯盘.bat`**：

1. 在下拉框选择要盯的窗口（对弈软件、网页直播、回放窗口均可；窗口列表带进程名，
   微信小程序等枚举不到的窗口可点「**点选窗口**」后直接单击目标），
   设置间隔（默认 5 秒），点「**开始盯盘**」
2. 工具每隔几秒自动截取该窗口画面、识别其中的棋盘（复用截图摆棋的
   识别核心，19/13/9 路自动），增量推演出着法并在本工具界面实时摆盘。
   **窗口在后台被完全遮挡也能正常截取**（与 ok-ww/ok-nte 同为
   PrintWindow 路线）；勾选「最小化时自动还原窗口」后，最小化的窗口
   也会被自动还原以继续盯盘
3. 勾选「**AI 面板**」后，棋盘每有变化即调用 KataGo：黑方胜率、目数差、
   势力归属热图随棋局进展动态更新
4. 右侧缩略图会用绿框标出识别到的棋盘四角，便于确认对准；
   随时「保存 SGF」或一键转入 Lizzieyzy 深度复盘

增量追踪以画面为唯一真值：正常落子/提子按着法推演，画面突变（如一次
出现多子）自动重新同步摆盘，保证棋面始终与窗口一致。识别、追踪、分析
三层各自独立，识别失败只会跳过该帧不影响盯盘。65 项自动化测试全过
（含真实窗口截屏识别与真实引擎分析）。

**同步到 Lizzieyzy**：勾选「同步到 Lizzieyzy」后，识别到的局面实时推进
Lizzieyzy 复盘界面——胜率图、候选点、形势判断照常工作，无需手动导入。
推荐在 Lizzieyzy 中按一次 **Alt+O** 启用增量协议通道（逐手摆盘、不抢
焦点）；未启用时自动走剪贴板通道（自动粘贴导入，Lizzieyzy 会被短暂
置前）。**轮次自动对齐**：按交替落子原则推断该谁走，不符时自动 pass
翻正，形势判断不踩轮次错；**采样间隔盯盘中实时可调**（快棋/慢棋）。
详见 [`boardwatch/README.md`](./boardwatch/README.md)。

---

## ⚠️ 关于在线观战功能

Lizzieyzy 自带的「弈客直播/大厅」「野狐链接跟盘」等**在线观战**功能，
依赖弈客（rtgame.yikeweiqi.com）与野狐（hlws.huanle.qq.com）的实时推送
服务。经测试（2026-10），**这两处第三方服务器均已停止服务**，属上游
服务下线，客户端无法修复；两平台现行的对局数据接口均为私有协议
（弈客需登录令牌、野狐直播走登录态推送），不适合第三方工具依赖。
在线打开普通 SGF 链接的功能仍可使用。如需联机下棋，请使用本包新增的
联机对弈功能。

---

## 📖 2027 中国规则支持

《围棋竞赛规则（2027 试行版）》将于 2027-01-01 起试行，核心变化：
**黑贴 3.5 子（=贴 7 点/目）**（旧为 3¾ 子/7.5 点），黑总得 **184 子即为平局**
——围棋正式比赛首次引入平局。平局需分胜负时，规程提供两种方案：
**① 判白方胜；② 判最先停手（先虚着）一方胜**。

本整合包的工具层已先行支持：

| 工具 | 支持内容 |
| :--- | :--- |
| **联机对弈** | 双虚着收束后自动按新规数子判定（黑贴 3.5 子、黑 184 平局）；平局时弹窗按规程二选一（判白胜 / 判先停手方胜 / 保持平局）；**终局点击棋子标注死子后实时重算**；记录并显示先停手方；结果写入 SGF RE（平局 RE[0]） |
| **实时盯盘** | 被盯对局收束时显示 2027 数子近似判定与先停手方（按全部棋子为活子近似） |
| **KataGo 引擎** | 贴 7 点/任意贴目为引擎原生能力，和棋局面胜率正确（50%） |

**局限说明**：数子判定需要终局死子信息——联机对弈中由玩家点击标注
（中国数子法下己方地域内己方死子不影响得分，通常只需标注嵌入对方地域
的死子）；实时盯盘按"全部棋子为活子"近似，无法从画面判断死子。
KataGo 引擎不涉及"先停手判胜"（属赛事终局判定，由本工具在终局执行）。

## 💻 硬件兼容性与性能

本整合包已在以下平台完成兼容性测试：

| 品牌 | 测试型号 |
| :--- | :--- |
| **NVIDIA** | RTX 50 系列、GTX 10 系列、GTX1660Super（图灵架构） |
| **AMD** | RX 6000 系列 |
| **Intel** | Xe 核显（Tiger Lake / Alder Lake / UHD 770 等） |

2012 年后支持 OpenCL 1.2+ 的主流显卡基本均可运行；运行一键调优可自动
获得最佳配置。详细性能数据见 [PERFORMANCE.md](./PERFORMANCE.md)。

---

## ❓ 常见问题

- **双击 bat 无反应/报错**：右键「以管理员身份运行」；确认路径不含中文和空格
- **`CL_PLATFORM_NOT_FOUND_KHR`**：更新显卡驱动（需支持 OpenCL 1.2+）
- **AMD 显卡报 `Got nonfinite for policy sum`**：在两个引擎的
  `default_gtp.cfg` 末尾添加一行 `openclUseFP16 = false`
- **形势判断没反应**：确认按的是主键盘 `/` 键且 Lizzieyzy 主窗口处于焦点
- **联机连不上**：检查防火墙是否放行（首次运行要点「允许」）；跨公网需
  端口映射或内网穿透
- **截图识别失败**：用「四角校准识别」手动点四个角；或检查截图是否完整
  包含整个棋盘
- **T18 / T28 是什么？该选哪个？**：KataGo 新一代 transformer 网络
  （T18=tf2-b10c384、T28=tf3-b10c512，需内置的 KataGo 1.18.1）。每访问
  棋力高于同档 18b/28b；本机 Intel 核显 OpenCL 实测原始速度略慢于同档
  CNN（见 PERFORMANCE.md），NVIDIA 卡则会更快。T18 内存占用仅 18b 的
  40%，建议自行切换对比体验；18b/28b 保持默认

---

## 📦 文件结构（v1.2 新增部分加粗）

```text
LizzieyzyToolbox_v1.2/
├── Lizzieyzy-2.5.3-win64.exe    # 主程序
├── 点我首次运行调优.bat           # 一键性能调优 (v1.1)
├── **截图摆棋.bat**              # 截图摆棋启动器 (v1.2)
├── **联机对弈.bat**              # 联机对弈启动器 (v1.2)
├── **实时盯盘.bat**              # 实时盯盘启动器 (v1.2)
├── engine_18b/  engine_28b/     # KataGo 经典双引擎（含估计配置 estimate.cfg）
├── engine_t18/  engine_t28/     # KataGo 1.18.1 + transformer T18/T28 权重
├── **screenshot2board/**        # 截图摆棋工具（识别核心+GUI+测试）(v1.2)
├── **netplay/**                 # 联机对弈工具（规则引擎+GUI+测试）(v1.2)
├── **boardwatch/**              # 实时盯盘工具（窗口捕获+增量追踪+实时分析）(v1.2)
├── jre/                         # 自带 OpenJDK 17
├── config.txt                   # 全局配置（含形势判断引擎配置）
├── README.md / README_EN.md / README_JA.md / README_KO.md
├── PERFORMANCE.md / CHANGELOG.md
└── LICENSE.txt                  # GPL-3.0
```

---

## 🧪 测试与质量

v1.2 新增功能均附带自动化回归测试，可在本机复验：

```text
python screenshot2board\test_s2b.py      # 截图识别：10 主题语料 + SGF 往返
python netplay\test_netplay.py           # 联机：规则 + 协议回环 + 界面集成
python boardwatch\test_boardwatch.py     # 盯盘：追踪 + 窗口捕获 + 实时引擎分析
python boardwatch\e2e_lizzie_sync.py     # 盯盘→Lizzieyzy 同步端到端（需桌面空闲）
```

（需要本机安装 Python 3 与 opencv-python；测试不影响正常使用。）

---

## 🙏 致谢与许可声明

本整合包是以下优秀开源项目的集成与二次开发，向所有作者致以诚挚感谢：

| 组件 | 作者 / 组织 | 许可证 | 来源 |
| :--- | :--- | :--- | :--- |
| **Lizzieyzy** | yzyray | GPL-3.0 | [github.com/yzyray/lizzieyzy](https://github.com/yzyray/lizzieyzy) |
| **KataGo** | lightvector | MIT | [github.com/lightvector/KataGo](https://github.com/lightvector/KataGo) |
| **KataGo 权重 (kata1)** | KataGo Distributed Training | 见[网络许可证](https://katagotraining.org/network_license/) | [katagotraining.org](https://katagotraining.org/networks/) |
| **AI_GO v1.1 整合包** | Coderchen-001 | GPL-3.0（本包 v1.2 的直接上游基础） | [github.com/Coderchen-001/AI_GO](https://github.com/Coderchen-001/AI_GO) |
| **OpenJDK 17** | Oracle / OpenJDK 社区 | GPL-2.0 + CE | [openjdk.org](https://openjdk.org/) |
| **image2sgf / gbr** | noword / (Go Board Recognition) | 开源参考实现 | 仅用于识别算法调研与测试语料 |

- Lizzieyzy、KataGo、OpenJDK 等 GPL/MIT 组件**均未修改**原二进制，
  对应完整源代码见上表官方仓库；
- v1.2 新增的 `screenshot2board/`、`netplay/` 工具及配置修复为本项目
  原创，同样以 **GPL-3.0** 授权（与整包一致），源代码即随包分发；
- KataGo 神经网络权重按 katagotraining.org 网络许可证再分发（保留署名）。

本整合包仅供学习交流使用，不得用于商业目的。

---

## 📞 获取帮助

- **Lizzieyzy 官方仓库**：<https://github.com/yzyray/lizzieyzy>
- **KataGo 官方仓库**：<https://github.com/lightvector/KataGo>
- **讨论 / 反馈**：[本项目 Discussions](../../discussions) · [提交 Issue](../../issues)

**版本信息**：工具箱 v1.2（新规先行版 · 个人维护）| Lizzieyzy v2.5.3 | KataGo v1.16.4 / v1.18.1 | OpenJDK 17

祝你下棋愉快！ 🎋
