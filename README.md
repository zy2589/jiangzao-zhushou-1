# 降噪助手

Windows 桌面降噪工具，包含两个独立模式，音视频都在本机处理：

1. **Resolve 模式**：连接当前 DaVinci Resolve 时间线，让 Resolve 将指定音轨导出为 WAV；助手在本机降噪，再按时间线位置追加到新音轨，保留原轨。
2. **本地素材模式**：导入本地音频或视频，导出降噪文件；视频保留画面并替换音轨。

## 启动和连接 Resolve

双击 `start_assistant.vbs` 可从本地启动小窗口“降噪助手”，不会弹出命令行窗口。它优先使用 `%LOCALAPPDATA%\DenoiseAssistant\toolchains\python3146`（Python 3.14.6 / Tcl 8.6）中的独立 Python；该工具链不存在或无法启动时再使用系统 Python。首次打开时会把 `降噪助手.lua` 放进当前 Windows 用户的 Resolve `工作区 → 脚本 → Utility` 菜单；之后也可以直接从该菜单启动。两种入口共用同一个程序实例，重复点击会唤回现有窗口，不会再开第二个助手窗口或 Python 控制台。

助手会通过 F6 控制台启动本机桥接代码，提交后自动收起 F6 控制台并回到助手。若 Resolve 尚未打开，先打开项目；连接状态会自动重试，也可以点“刷新达芬奇”。

助手运行时会启动一个无界面的菜单监听进程，以便 Resolve 的隔离脚本环境发出启动信号时唤起主窗口。监听器不会弹出终端，不会写入 Windows 启动项，也不打包或安装程序；它在当前登录会话运行，并在 Windows 注销或重启时退出。Resolve 的工作区 Lua 入口也会直接尝试启动本地 VBS 启动器。

F6 控制台桥接文件和认证配置保存在用户配置目录，通过 `127.0.0.1` 回环连接；媒体不会上传。

## Resolve 时间线模式

1. 确认 Resolve 已打开项目并载入时间线。
2. 启动助手并等待显示“已连接”；需要时点“刷新达芬奇”。
3. 选择源音轨（例如 A1），点“生成降噪并添加到达芬奇”。
4. 助手会暂时隔离所选音轨，调用 Resolve 导出该轨的纯音频 WAV；完成后恢复音轨状态。
5. DeepFilterNet 在本机处理导出的 WAV，再由助手导入 Media Pool，按原时间线起点放到新建音轨（选择 A1 时放到 A2），保留原音轨。

进度区域会显示 Resolve 导出百分比、DeepFilterNet 降噪状态和回填阶段。Resolve 时间线音轨上的效果会随 WAV 导出一起处理；不再从原始媒体逐个裁切片段。

## 本地素材模式

本地素材区默认显示。可以把一个或多个音频/视频文件直接拖入素材列表，也可以点“添加素材”；处理结果保存在各自素材所在的文件夹。音频输出为降噪 WAV；视频复制原画面流，只替换为降噪音轨。助手窗口默认置顶。

Resolve 时间线导出时，先临时写入 Resolve 的 Utility 脚本目录，再自动移入助手程序目录的 `ResolveCache` 文件夹。原始 WAV 与降噪 WAV 都保存在 `ResolveCache`；助手中的“删除七天前的缓存”按钮只清除超过七天的缓存文件。

## 引擎

- **DeepFilterNet**：离线人声降噪模型，项目附带官方 v0.5.6 Windows x64 命令行程序。
- **FFmpeg**：读取、裁切和封装音视频。程序会先查找 PATH 中的 `ffmpeg.exe`；若未找到，在“引擎设置”中指定其路径。
- DeepFilterNet 输入会转换为 48 kHz 双声道 PCM WAV。强度滑杆控制原声与降噪声的混合比例。

设置保存在 `%APPDATA%\DenoiseAssistant\settings.json`。

## 开源组件

Resolve F6 桥接使用 [ET-sec/resolve-console-bridge](https://github.com/ET-sec/resolve-console-bridge)（MIT）。Windows 文件拖放由 [Eliav2/tkinterdnd2](https://github.com/Eliav2/tkinterdnd2)（MIT）提供。降噪模型为 [Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)（Apache-2.0 或 MIT）。FFmpeg 由用户自行安装。
