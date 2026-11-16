# 第三方组件

## DeepFilterNet

- 项目：Rikorose/DeepFilterNet
- 版本：v0.5.6，Windows x86_64 命令行程序
- 文件：`tools/deep-filter.exe`
- 来源：[官方 GitHub Release](https://github.com/Rikorose/DeepFilterNet/releases/tag/v0.5.6)
- 许可：项目采用 Apache-2.0 或 MIT 双许可，详见[上游许可证](https://github.com/Rikorose/DeepFilterNet/blob/main/LICENSE)。
- SHA-256：`75E11FA16445F560CB6B021521DDB89E89270D13B83089705D98776F58FD7915`

## FFmpeg

本程序调用系统中安装的 `ffmpeg.exe` 完成音频采样率转换和视频封装，不在项目中分发 FFmpeg。FFmpeg 的源代码大部分使用 LGPL，部分可选组件使用 GPL；具体取决于所安装的构建及其启用组件。详见 [FFmpeg 官方许可证说明](https://ffmpeg.org/legal.html)。

## TkinterDnD2

- 项目：[Eliav2/tkinterdnd2](https://github.com/Eliav2/tkinterdnd2)，基于 pmgagne/tkinterdnd2 的维护分支
- 版本：0.6.4
- 文件：`vendor_new/tkinterdnd2/`
- 用途：接收 Windows 文件资源管理器拖入的本地音频和视频文件。
- 许可：MIT，随附于 `vendor_new/tkinterdnd2-0.6.4.dist-info/licenses/LICENSE`。
