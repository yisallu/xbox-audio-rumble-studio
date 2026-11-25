# Xbox Audio Rumble Studio

一个简洁好用的 Windows 桌面小工具，可以把电脑正在播放的系统声音实时转换成 Xbox 手柄震动反馈。

它会监听默认的 WASAPI 回环音频设备，实时分析音量与频段，再通过 XInput 驱动兼容 Xbox 手柄的左右马达（左侧重型低频马达负责鼓点低音，右侧轻型马达负责人声与高频细节）。适合音乐、ASMR、视频、游戏，以及任何电脑上正在播放的声音。

## 功能亮点

- **简洁人性化的现代深色界面**：一目了然的圆角卡片布局，支持高 DPI 清晰缩放；主界面仅保留最常用的总开关、场景模式、三项百分比手感滑块与实时动态响应条，专业频段参数与日志收纳于可选的「高级与日志」窗口中
- **手柄 `LB + RB` 组合键随时开关震动**：任何时候同时按下手柄的 **LB 和 RB 键** 即可一键开启或关闭震动（独立高频轮询，即使暂停播放或最小化到后台也毫秒级响应，开启时附带轻巧触觉确认）
- **WASAPI 非阻塞音频捕获与自动跟随**：静音时平滑收住震动，切换耳机/扬声器等默认输出设备时自动重连（也可点击顶部音频徽章立即刷新）
- **智能自动 + 四大场景预设**：支持自动识别音乐、ASMR、安静待机并动态调整手感；也可一键切换「人声清晰」「低频沉浸」「轻柔细腻」「全频强劲」预设
- **直观手感调节与环境校准**：滑块支持点击直达、拖拽与滚轮微调；系统音量条直接标出黄色触发门限刻度，点击「环境校准」采样 3 秒底噪即可自动设定最佳触发门限
- **自动保存配置**：所有设置自动保存至 `xbox6.settings.json`

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
pip install numpy pyaudiowpatch
```

启动程序：

```powershell
python .\xbox6.py
```

## 下载编译版

也可以下载已打包的 Windows x64 版本：

[XboxAudioRumbleStudio-windows-x64-v1.0.1.zip](https://github.com/yisallu/xbox-audio-rumble-studio/releases/download/v1.0.1/XboxAudioRumbleStudio-windows-x64-v1.0.1.zip)

解压后直接运行 `XboxAudioRumbleStudio.exe`，设置会自动保存在程序同目录下。

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
