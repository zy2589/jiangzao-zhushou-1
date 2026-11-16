# 第三方组件

## DeepFilterNet

- 项目：Rikorose/DeepFilterNet
- 版本：v0.5.6，Windows x86_64 命令行程序
- 文件：`tools/deep-filter.exe`
- 来源：[官方 GitHub Release](https://github.com/Rikorose/DeepFilterNet/releases/tag/v0.5.6)
- 许可：项目采用 Apache-2.0 或 MIT 双许可，详见[上游许可证](https://github.com/Rikorose/DeepFilterNet/blob/main/LICENSE)。
- SHA-256：`75E11FA16445F560CB6B021521DDB89E89270D13B83089705D98776F58FD7915`

## FFmpeg

常规源码运行会调用系统中的 `ffmpeg.exe`。单文件 EXE 构建会把构建机上的 `ffmpeg.exe` 一并嵌入；此工作区当前构建机使用 FFmpeg 9.0.2 Gyan.dev full build，按其构建页面标注为 GPLv3。FFmpeg 项目源码版本为 [946fcce07b](https://github.com/FFmpeg/FFmpeg/commit/946fcce07b)。Windows 构建由 [FFmpeg 官方下载页](https://ffmpeg.org/download.html)链接的第三方构建提供；Gyan.dev 的构建页面列出 GPLv3 许可和构建变体说明。发布单文件 EXE 时应一并提供适用的对应源码与许可证材料。

## yt-dlp

- 项目： [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)
- 版本：2026.10.26，官方 Windows x64 独立程序 `yt-dlp.exe`。
- 来源：[官方 GitHub Release](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26)
- SHA-256：`f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d`
- 许可：Unlicense；该独立程序还包含其他第三方组件的许可文本。完整许可汇总随 EXE 放在 `licenses/yt-dlp-THIRD_PARTY_LICENSES.txt`。
- 上游文件：[LICENSE](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/LICENSE)、[第三方许可汇总](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/THIRD_PARTY_LICENSES.txt)。

## Deno

- 项目：[denoland/deno](https://github.com/denoland/deno)
- 版本：2.9.8，作为 yt-dlp 网页解析的 JavaScript 运行时。
- 来源：[官方 GitHub Release](https://github.com/denoland/deno/releases/tag/v2.9.8)，Windows x64 压缩包 SHA-256：`cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf`
- 许可：MIT，随 EXE 附带 `licenses/deno-LICENSE.txt`。

## TkinterDnD2

- 项目：[Eliav2/tkinterdnd2](https://github.com/Eliav2/tkinterdnd2)，基于 pmgagne/tkinterdnd2 的维护分支
- 版本：0.6.4
- 文件：`vendor_new/tkinterdnd2/`
- 用途：接收 Windows 文件资源管理器拖入的本地音频和视频文件。
- 许可：MIT，随附于 `vendor_new/tkinterdnd2-0.6.4.dist-info/licenses/LICENSE`。

## PyInstaller

单文件 EXE 使用 PyInstaller 打包。PyInstaller 使用 GPL-2.0-or-later 与 Bootloader Exception；详见[官方许可说明](https://pyinstaller.org/en/stable/license.html)。
