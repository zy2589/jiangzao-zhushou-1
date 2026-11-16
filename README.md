# 降噪助手

Windows 本地音频与视频降噪工具。所有素材都在本机处理，不连接 DaVinci Resolve。

## 启动

双击 `start_assistant.vbs` 或桌面/开始菜单中的“降噪助手”快捷方式。程序会使用隐藏窗口启动，不会额外弹出命令行。重复打开时会唤回已运行的助手，不会再启动第二个实例。

## 本地降噪

把音频或视频素材拖入列表，也可以点击“添加素材”。处理完成后，降噪结果保存在每个素材原来的文件夹中：

- 音频素材输出为降噪 WAV。
- 视频素材保留画面，并把降噪后的声音替换进视频。

窗口默认置顶。可以在“引擎设置”里指定 FFmpeg 路径；降噪使用项目附带的 DeepFilterNet。强度滑块控制原声与降噪声的混合比例。

## 开源组件

- 降噪模型：[Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)，项目附带官方 v0.5.6 Windows x64 命令行程序，许可为 Apache-2.0 或 MIT。
- Windows 文件拖放：[Eliav2/tkinterdnd2](https://github.com/Eliav2/tkinterdnd2)，MIT。
- FFmpeg：用于读取和封装音视频，由用户自行安装。程序会先查找 PATH 中的 `ffmpeg.exe`；若未找到，可在“引擎设置”中指定路径。

详细许可信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。设置保存在 `%APPDATA%\DenoiseAssistant\settings.json`。
