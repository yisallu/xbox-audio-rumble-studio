# Xbox Audio Rumble Studio

一个 Windows 桌面小工具，可以把电脑正在播放的系统声音实时转换成 Xbox 手柄震动。

它会监听默认的 WASAPI 回环音频设备，实时分析音量和频段，再通过 XInput 控制兼容 Xbox 手柄的左右马达。适合音乐、ASMR、视频、游戏，以及任何电脑上正在播放的声音。

## 功能

- 通过 WASAPI loopback 捕获 Windows 系统声音
- 通过 XInput 输出 Xbox 兼容手柄震动
- 自动识别音乐、ASMR、安静片段并调整震动策略
- 内置人声清晰、低频沉浸、轻柔细腻、全频强劲等预设
- 可调整体强度、节奏力度、细腻麻感、阈值、启动速度、释放速度和曲线响应
- 支持双马达模式和人声聚焦模式
- 实时音量/震动监控和运行日志
- 设置会保存到 `xbox6.settings.json`
- 打包版默认开启震动开关

## 系统要求

- Windows
- Python 3.10 或更高版本
- Xbox 兼容 XInput 手柄
- Windows 中有正在播放的音频

## 安装源码版

克隆仓库：

```powershell
git clone https://github.com/yisallu/xbox-audio-rumble-studio.git
cd xbox-audio-rumble-studio
```

安装 Python 依赖：

```powershell
pip install numpy pyaudiowpatch
```

启动：

```powershell
python .\xbox6.py
```

## 下载编译版

可以直接下载 Windows x64 打包版：

[XboxAudioRumbleStudio-windows-x64-v1.0.1.zip](https://github.com/yisallu/xbox-audio-rumble-studio/releases/download/v1.0.1/XboxAudioRumbleStudio-windows-x64-v1.0.1.zip)

解压后运行：

```text
XboxAudioRumbleStudio.exe
```

压缩包里包含默认设置文件 `XboxAudioRumbleStudio.settings.json`。如果你修改了设置，程序会把新设置保存到 exe 旁边。

## 使用方式

1. 连接 Xbox 兼容手柄。
2. 在 Windows 中播放音乐、视频或其他声音。
3. 启动程序。
4. 选择一个预设，或者保持“全自动模式”开启。
5. 根据感觉调节整体强度、节奏力度、细腻麻感和响应参数。

## 注意事项

- 本项目依赖 Windows XInput 和 WASAPI，因此只支持 Windows。
- 如果程序没有检测到手柄，可以重新连接手柄，或检查其他手柄工具是否占用了设备。
- 长时间使用时建议保持适中的震动强度。
- 关闭程序时会自动停止震动。

## 项目状态

这是一个小型独立 Python 桌面程序。当前代码有意保持在单文件结构中，方便直接运行、查看和修改。

## 许可证

目前还没有添加许可证文件。在添加明确许可证之前，默认保留所有权利。
