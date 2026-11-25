# Xbox Audio Rumble Studio

一个简洁好用的 Windows 桌面小工具，可以把电脑正在播放的系统声音实时转换成 Xbox 手柄震动反馈。

它会监听默认的 WASAPI 回环音频设备，实时分析音量与频段，再通过 XInput 驱动兼容 Xbox 手柄的左右马达（左侧重型低频马达负责鼓点低音，右侧轻型马达负责人声与高频细节）。适合音乐、ASMR、视频、游戏，以及任何电脑上正在播放的声音。

## 功能亮点

- **极简无废话深色工作室 UI**：去除冗余标题干扰，顶栏配备 `4x LANCZOS` 抗锯齿等尺寸对称双拨杆（`动效` ｜ `震动`）与实时设备胶囊，支持高分屏自适应清晰缩放
- **ProMotion 自适应高刷三联律动舞台**：独立渲染线程锁定 Windows `DwmFlush()` 硬件垂直同步，自动适配任意屏幕刷新率（60Hz / 120Hz / 130Hz / 144Hz 满帧丝滑，130FPS 满载仅约 `2%` CPU，音乐暂停或关闭动效自动降至 `0 FPS` 休眠）
- **手柄 `LB + RB` 组合键随时开关震动**：任何时候同时按下手柄 **LB + RB** 即可一键开启/关闭震动（独立 60Hz 轮询，最小化在后台同样毫秒级响应并带轻巧确认震动）
- **蓝牙耳机热切换 + 低音量自适应增益 + 毫秒级急刹**：直连 Windows CoreAudio 实时追踪默认输出端点，切换蓝牙耳机自动无缝重连；低音量听歌依然澎湃有层次，暂停音乐 `< 55ms` 立即停转不拖尾
- **智能自动 + 五大声学精调模式**：四频段瞬态分离，支持「智能自动」「人声清晰」「低频沉浸」「轻柔细腻（ASMR）」「全频强劲」一键切换与三项手感推子调节
- **环境底噪校准与自动记忆**：一键采样 3 秒环境底噪设定最佳门限，所有开关与参数自动持久化保存

## 系统要求

- Windows 10 / 11
- Python 3.10 或更高版本
- Xbox 兼容 XInput 手柄
- Windows 中有可用的音频播放设备

## 安装与运行（源码版）

克隆仓库：

```powershell
git clone https://github.com/yisallu/xbox-audio-rumble-studio.git
cd xbox-audio-rumble-studio
```

安装依赖：

```powershell
pip install numpy pyaudiowpatch pillow
```

启动程序：

```powershell
python .\xbox6.py
```

## 下载编译版（推荐）

可以直接下载独立单文件 `.exe`（无需安装 Python，无需解压，下载后双击即可运行）：

- [直接下载 XboxAudioRumbleStudio.exe（最新版）](https://github.com/yisallu/xbox-audio-rumble-studio/releases/latest/download/XboxAudioRumbleStudio.exe)

运行后如果调整了设置，程序会自动在 `.exe` 同目录下保存 `XboxAudioRumbleStudio.settings.json`。

## 使用说明

1. 连接 Xbox 兼容手柄（支持 1~4 号槽位切换）。
2. 在 Windows 中播放音乐、视频、ASMR 或其他声音。
3. 启动程序，默认开启「智能自动」与震动输出。
4. 根据喜好拖动「震动强度」「节奏冲击」「细节微颤」滑块，或点击卡片切换预设场景。
5. 使用过程中，随时**同时按下手柄 LB + RB 键**（或点击右上角主开关 / 在窗口内按空格键）即可开启或关闭震动。

## 注意事项

- 本项目依赖 Windows XInput 和 WASAPI，仅支持 Windows 系统。
- 如果未检测到手柄，请重新连接手柄或检查是否有其他手柄映射软件独占了设备。
- 暂停震动或关闭程序时会自动清零马达输出。

## 许可证

目前尚未添加许可证文件。在添加明确许可证之前，默认保留所有权利。
