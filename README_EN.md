<div align="center">

# 🎯 Lizzieyzy Toolbox — 2027 Rules Early-Adoption Edition

**An unofficial, personally-maintained bundle of Lizzieyzy 2.5.3 + KataGo: score-estimation fix, plus screenshot-to-board / netplay / live-watch companion tools, optional transformer engines, and early support for the 2027 Chinese rules**

> Not affiliated with official Lizzieyzy. Core play & analysis features are provided by Lizzieyzy
> (its source is unmodified); this repository maintains fixes and add-on tools.

[![Release](https://img.shields.io/badge/release-v1.2-blue.svg)](../../releases)
[![Fork of Coderchen-001/AI_GO](https://img.shields.io/badge/fork%20of-AI_GO%20v1.1-blue.svg)](https://github.com/Coderchen-001/AI_GO)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](./LICENSE.txt)

**English** | [中文](./README.md) | [日本語](./README_JA.md) | [한국어](./README_KO.md)

</div>

---

A Go AI bundle that works straight after unzipping: the **Lizzieyzy** graphical interface provides the core
play & analysis features (unmodified), the **KataGo** engine does the thinking, and a bundled Java runtime needs
no installation. This repository's contribution = a **fix** (score-estimation engine config), **add-on tools**
(screenshot-to-board / netplay / live window watch & sync), and **integration** (transformer engine options T18/T28).

---

## ✨ Core Features

- **Four engines**: ships with KataGo **18b / 28b** classic weights plus the new
  transformer-series **T18 (tf2-b10c384)** and **T28 (tf3-b10c512)**
  (KataGo 1.18.1 bundled), switchable with one click in the app
- **One-click performance tuning**: just double-click `点我首次运行调优.bat` (click-me first-run tuning) and it completes automatically (v1.1)
- **Truly portable**: bundled OpenJDK 17, ready after unzipping, fits on a USB stick
- **Cross-GPU compatibility**: universal OpenCL build, supports mainstream NVIDIA / AMD / Intel GPUs
- 🆕 **Screenshot-to-Board** (v1.2): screenshot → automatic board recognition → set the position up in Lizzieyzy in one click
- 🆕 **Online Play** (v1.2): play Go with friends over LAN/Internet, with an optional real-time AI win-rate panel
  and 2027 Chinese rules judgment (draws / first-to-pass tiebreak)
- 🆕 **Live window watching** (v1.2): pick any window — the tool recognizes the Go board inside it every few seconds,
  keeps the position mirrored in real time and runs live KataGo analysis as the game progresses
- 🆕 **Score estimation fix** (v1.2): fixes the issue where the score estimation engine failed to load in v1.1

---

## 🚀 Quick Start

1. Unzip anywhere (**a path without Chinese characters or spaces is recommended**)
2. Double-click `Lizzieyzy-2.5.3-win64.exe` to launch
3. Switch between the 18b / 28b engines from the top menu, start playing or import an SGF for analysis
4. ⭐ Highly recommended: double-click `点我首次运行调优.bat` (click-me first-run tuning) to complete performance tuning (about 2–6 minutes)

> The first launch automatically performs OpenCL tuning (3–5 minutes). This is normal — please be patient.

---

## 🆕 What's New in v1.2

### 1️⃣ Score Estimation (Fixed)

In v1.1 this feature was unavailable due to a missing dedicated estimation engine configuration;
v1.2 fixes it, verified end-to-end in a real environment.

- Press **`/`** in Lizzieyzy to trigger score estimation; press again to cancel
- The engine stays resident in the background: about 3 seconds the first time, instant refresh afterwards
- See the documentation bundled with Lizzieyzy for detailed usage

### 2️⃣ Screenshot-to-Board (Screenshot2Board)

See a screenshot or livestream view of any game and want KataGo to analyze it right away? Double-click
**`截图摆棋.bat`** (Screenshot-to-Board) in the root directory:

1. `Win + Shift + S` to take a screenshot → click "**Paste from clipboard**" (or "Select image")
2. The board and the Black/White stones are recognized automatically (19/13/9-line boards detected automatically)
3. Click "**Open in Lizzieyzy**" — the position is set up instantly and analysis can begin

If recognition is wrong: click intersections on the preview board to correct them manually (cycles empty → Black → White);
for angled shots or complex images, use the "four-corner calibration" mode. Recognition runs locally with OpenCV and scores **100%** accuracy on the test corpus. See [`screenshot2board/README.md`](./screenshot2board/README.md) for details.

### 3️⃣ Online Play (Netplay)

Play Go online with a friend — both sides only need this toolkit, no server required:

1. Double-click **`联机对弈.bat`** (Online Play); the host picks "Create game" (the interface shows the IP and port)
2. The other player picks "Join game" and enters the host's IP to connect
3. Full built-in rules engine (captures / suicide prohibition / ko), with undo requests, resignation, and chat
   plus **2027 Chinese rules judgment** (black gives 3.5 zi = 7 points; black 184 stones is a draw; when a winner is required, the rules offer white-wins or first-to-pass-wins; mark dead stones by clicking and re-judge)
4. Optionally enable the **AI panel**: shows win rate and a territory-ownership heatmap after every move
5. When the game ends, an SGF is saved — review it in Lizzieyzy with one click

> Direct connection works on the same LAN (click "Allow" on the firewall prompt shown the first time); across the public
> Internet, the host needs port forwarding (default port 19864) or a NAT traversal tool. See
> [`netplay/README.md`](./netplay/README.md) for details.

### 4️⃣ Live Window Watching (BoardWatch)

Want KataGo to analyze a game as it unfolds in any window? Double-click **`实时盯盘.bat`** (Live Window Watch):

1. Pick the window to watch (a game client, a web stream, a replay window) — the list is labelled with process
   names, and windows that can't be enumerated (e.g. WeChat mini-programs) can be chosen with **"click-to-pick"**:
   press the button, then click the target window within 5 seconds. Set the interval (5 s by default) and click
   **"Start watching"**
2. Every few seconds the tool captures that window, recognizes the board inside it (reusing the Screenshot-to-Board
   core, auto 19/13/9), infers the moves incrementally and mirrors the position live in its own board panel.
   **A window fully covered by others is captured just fine in the background** (same PrintWindow route as
   ok-ww/ok-nte); with "auto-restore minimized window" ticked, a minimized window is restored automatically so
   watching continues
3. Tick **"AI panel"** and every change triggers KataGo: black win rate, score lead and an ownership heat map that
   updates as the game progresses
4. The thumbnail on the right marks the detected board corners in green so you can confirm alignment; save the SGF or
   send it into Lizzieyzy for deep review at any time

Tracking treats the picture as the single source of truth: normal moves/captures are replayed as moves, sudden
multi-stone changes trigger an automatic re-sync, so the mirrored board always matches the window. Recognition,
tracking and analysis are independent layers — a failed frame is skipped and watching continues. All 65 automated
tests pass (including real-window capture + recognition and real-engine analysis).

**Sync into Lizzieyzy**: tick "Sync to Lizzieyzy" and every recognized position is pushed straight into the Lizzieyzy
review UI — winrate graph, candidate moves and score estimation all keep working, no manual importing. Pressing
**Alt+O** once inside Lizzieyzy enables the incremental-protocol channel (move-by-move, no focus stealing); without
it the clipboard channel is used automatically (Lizzieyzy is briefly brought to the front on each update). **The
side to move is auto-aligned**: the tool infers it from alternating play and corrects Lizzieyzy with a pass when
needed, so score estimation never trips on a wrong turn; the **sampling interval is adjustable live** while watching
(fast vs slow games). See [`boardwatch/README.md`](./boardwatch/README.md) for details.

---

## ⚠️ About the Online Spectating Feature

The **online spectating** features bundled with Lizzieyzy, such as "Yike live / lobby" and "Yehu (Fox) linked-board tracking",
depend on the real-time push services of Yike (rtgame.yikeweiqi.com) and
Yehu (hlws.huanle.qq.com). As tested (2026-10), **both of these third-party servers have shut down**; this is an upstream
service discontinuation that cannot be fixed on the client side. Moreover, the platforms' current game-data APIs are
private (Yike requires login tokens; Fox live push needs a logged-in session), so third-party tools cannot reasonably
rely on them. Opening ordinary SGF links online still works. To play online, please use the Online Play feature.

---

## 📖 2027 Chinese Rules Support

The Chinese Weiqi Association's *Go Competition Rules (2027 Trial Edition)* takes effect
2027-01-01. Key changes: **black gives 3.5 zi (= 7 points)** (previously 3.75 zi / 7.5
points), and a total of **184 zi for black is a draw** — the first official introduction
of draws. When a winner is required, the regulations offer two options: **① white wins;
② the side that passed first wins**.

The tools in this bundle already support these rules:

| Tool | Support |
| :--- | :--- |
| **Online Play** | After a two-pass ending, scores automatically under the new rules (3.5-zi komi, 184-zi draw); on a draw, a dialog offers the two regulation tiebreaks (white wins / first-to-pass wins) or keeping the draw; **click stones to mark them dead and re-judge live**; records and displays who passed first; result written to SGF RE (draw = RE[0]) |
| **Live Window Watch** | When the watched game ends, shows an approximate 2027 score and who passed first (assuming all stones alive) |
| **KataGo engine** | Komi of 7 points / arbitrary komi is native, and draws analyze correctly (50%) |

**Limitations**: scoring requires dead-stone information — in Online Play you mark dead
stones by clicking; Live Watch approximates by treating all stones as alive, since dead
stones cannot be judged from a screenshot. "First-to-pass wins" is a game-end ruling by
the tools; the KataGo engine itself is not involved.

## 💻 Hardware Compatibility and Performance

This bundle has been compatibility-tested on the following platforms:

| Brand | Tested models |
| :--- | :--- |
| **NVIDIA** | RTX 50 series, GTX 10 series, GTX1660Super (Turing architecture) |
| **AMD** | RX 6000 series |
| **Intel** | Xe integrated GPUs (Tiger Lake / Alder Lake / UHD 770, etc.) |

Mainstream GPUs from 2012 onward with OpenCL 1.2+ support can generally run it; running the one-click
tuning automatically obtains the best configuration. See [PERFORMANCE.md](./PERFORMANCE.md) for detailed performance data.

---

## ❓ FAQ

- **Double-clicking a .bat file does nothing / gives an error**: right-click and choose "Run as administrator"; also make sure the path contains no Chinese characters or spaces
- **`CL_PLATFORM_NOT_FOUND_KHR`**: update your GPU driver (OpenCL 1.2+ support required)
- **AMD GPU reports `Got nonfinite for policy sum`**: add the line `openclUseFP16 = false` at the end of
  `default_gtp.cfg` for both engines
- **Score estimation does not respond**: make sure you pressed the `/` key on the main keyboard and that the Lizzieyzy main window has focus
- **Online play won't connect**: check that the firewall allows it (click "Allow" on first run); across the public
  Internet you need port forwarding or NAT traversal
- **Screenshot recognition fails**: use "four-corner calibration" and click the four corners manually; or check
  that the screenshot fully contains the whole board
- **What are T18 / T28? Which should I pick?** The new KataGo transformer networks
  (T18 = tf2-b10c384, T28 = tf3-b10c512; they require the bundled KataGo 1.18.1). Strength per visit is higher
  than the same-tier 18b/28b; on an Intel iGPU the OpenCL transformer kernels are still immature so raw speed is
  slightly lower than same-tier CNNs (see PERFORMANCE.md) — faster on NVIDIA cards. T18 uses only 40% of 18b's
  memory; try them yourself and compare — 18b/28b remain the defaults

---

## 📦 File Structure (v1.2 additions in bold)

```text
LizzieyzyToolbox_v1.2/
├── Lizzieyzy-2.5.3-win64.exe    # Main program
├── 点我首次运行调优.bat           # One-click performance tuning (v1.1)
├── **截图摆棋.bat**              # Screenshot-to-Board launcher (v1.2)
├── **联机对弈.bat**              # Online Play launcher (v1.2)
├── **实时盯盘.bat**              # Live Window Watch launcher (v1.2)
├── engine_18b/  engine_28b/     # KataGo classic engines (with dedicated estimation config estimate.cfg)
├── engine_t18/  engine_t28/     # KataGo 1.18.1 + transformer T18/T28 weights
├── **screenshot2board/**        # Screenshot-to-Board tool (recognition core + GUI + tests) (v1.2)
├── **netplay/**                 # Online Play tool (rules engine + GUI + tests) (v1.2)
├── **boardwatch/**              # Live Window Watch tool (window capture + incremental tracking + live analysis) (v1.2)
├── jre/                         # Bundled OpenJDK 17
├── config.txt                   # Global configuration (incl. score estimation engine config)
├── README.md / README_EN.md / README_JA.md / README_KO.md
├── PERFORMANCE.md / CHANGELOG.md
└── LICENSE.txt                  # GPL-3.0
```

---

## 🧪 Testing and Quality

All new v1.2 features come with automated regression tests you can re-run locally:

```text
python screenshot2board\test_s2b.py      # Screenshot recognition: 10-theme corpus + SGF round-trip
python netplay\test_netplay.py           # Online play: rules + protocol loopback + UI integration
python boardwatch\test_boardwatch.py     # Live watch: tracking + window capture + real-engine analysis
python boardwatch\e2e_lizzie_sync.py     # Watch->Lizzieyzy sync E2E (idle desktop needed)
```

(Requires Python 3 and opencv-python installed locally; the tests do not affect normal use.)

---

## 🙏 Acknowledgements and License

This bundle integrates and extends the following excellent open-source projects — sincere thanks to all the authors:

| Component | Author / Organization | License | Source |
| :--- | :--- | :--- | :--- |
| **Lizzieyzy** | yzyray | GPL-3.0 | [github.com/yzyray/lizzieyzy](https://github.com/yzyray/lizzieyzy) |
| **KataGo** | lightvector | MIT | [github.com/lightvector/KataGo](https://github.com/lightvector/KataGo) |
| **KataGo weights (kata1)** | KataGo Distributed Training | See the [network license](https://katagotraining.org/network_license/) | [katagotraining.org](https://katagotraining.org/networks/) |
| **AI_GO v1.1 bundle** | Coderchen-001 | GPL-3.0 (direct upstream base of this v1.2 build) | [github.com/Coderchen-001/AI_GO](https://github.com/Coderchen-001/AI_GO) |
| **OpenJDK 17** | Oracle / OpenJDK community | GPL-2.0 + CE | [openjdk.org](https://openjdk.org/) |
| **image2sgf / gbr** | noword / (Go Board Recognition) | Open-source reference implementation | Used only for recognition-algorithm research and test corpora |

- The GPL/MIT components such as Lizzieyzy, KataGo, and OpenJDK are shipped as **unmodified** original binaries;
  the corresponding full source code is in the official repositories listed above;
- The `screenshot2board/` and `netplay/` tools added in v1.2, along with the configuration fixes, are original
  work of this project, likewise licensed under **GPL-3.0** (same as the whole package), with source code distributed in the package;
- The KataGo neural network weights are redistributed under the katagotraining.org network license (attribution retained).

This bundle is for learning and exchange purposes only and must not be used commercially.

---

## 📞 Getting Help

- **Lizzieyzy official repository**: <https://github.com/yzyray/lizzieyzy>
- **KataGo official repository**: <https://github.com/lightvector/KataGo>
- **Discussion / feedback**: [This project's Discussions](../../discussions) · [File an issue](../../issues)

**Version info**: Toolbox v1.2 (personal patched & enhanced build) | Lizzieyzy v2.5.3 | KataGo v1.16.4 / v1.18.1 | OpenJDK 17

Enjoy your games of Go! 🎋
