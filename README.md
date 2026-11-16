# 降噪助手

Windows 本地音频与视频降噪工具，不连接 DaVinci Resolve。

## 单文件版

双击 `dist\降噪助手.exe` 即可启动。EXE 内含 Python 运行环境、DeepFilterNet、FFmpeg、网页下载器和网页解析运行时，不要求另外安装这些依赖。首次启动会解压到 Windows 临时目录，启动时间稍长。

在 Windows x64 构建环境中运行：

```powershell
python -m pip install pyinstaller
python build_onefile.py
```

构建需要 PATH 中有 `ffmpeg.exe` 和网络连接。构建脚本会从 yt-dlp 和 Deno 官方 GitHub Release 下载固定版本，并校验 SHA-256 后一并打入 EXE。该 EXE 会较大；当前 FFmpeg 构建采用 GPLv3 许可，分发时须同时提供适用的许可证和源码材料。详见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。

## 本地素材降噪

把音频或视频拖入列表，也可以点击“添加素材”。处理结果放在每个素材原来的文件夹中：

- 音频素材输出为 `_降噪.wav`。
- 视频素材保留画面，并替换为降噪后的声音。

强度默认 75%。这个数值表示输出里降噪音频的混入比例；数值越高，降噪结果占比越大。若人声细节受影响，可降低强度。

## 链接下载并降噪

支持直接音视频文件链接，以及 yt-dlp 支持的公开视频页面链接。下载完成后会自动进入同一套 DeepFilterNet 降噪流程。下载素材和降噪结果放在：

```text
%USERPROFILE%\Downloads\降噪助手下载
```

网页链接需要联网；登录、地区限制、网站改动或平台保护可能导致下载失败。直接文件链接不需要网页解析器。

## 源码版

可以双击 `start_assistant.vbs` 启动源码版。需要 Python 3.13、Tkinter 和 FFmpeg；文件链接下载可以直接用，平台网页链接需要 `yt-dlp.exe` 与 `deno.exe`（单文件构建会自动下载并打包）。

设置保存在 `%APPDATA%\DenoiseAssistant\settings.json`。
