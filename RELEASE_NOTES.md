# Timekeeper 0.1.0 Beta 1

首个公开测试版，发布日期：2026-09-25。

## 下载与平台

| 文件 | 目标平台 | 大小 |
| --- | --- | ---: |
| `timekeeper_0.1.0~beta1_amd64.deb` | Ubuntu 24.04 amd64 | 约 14 MiB |
| `timekeeper-0.1.0-0.beta1.fc44.x86_64.rpm` | Fedora 44 x86_64 | 约 12 MiB |
| `Timekeeper-0.1.0-beta.1-x86_64.AppImage` | Ubuntu 24.04 amd64 及兼容环境 | 约 17 MiB |

`.deb` 使用 `sudo apt install ./文件名` 安装，`.rpm` 使用 `sudo dnf install ./文件名` 安装。AppImage 先用 `chmod +x 文件名` 赋予执行权限，再直接运行。AppImage 内置应用和 Python 第三方依赖，但**仍要求宿主机安装 Python 3.12、PyGObject、GTK 4 与 GStreamer**；它不是完全自包含包。详细命令见 [README](README.md)。

## 功能

- QQ 扫码登录，读取「我喜欢」及自建歌单，搜索歌曲并在线播放；歌单列表接近底部自动加载。
- 播放历史最多保存 500 首不同歌曲；底栏支持播放控制、进度跳转、随机播放、单曲循环和音量调节。
- 播放页展示封面与同步歌词，支持 QRC 逐字高光、LRC 逐行回退、触摸板滚动及点击歌词跳转。
- 设置页支持自动或指定音质、深浅色模式、主题色、歌词字号与高光颜色，以及账号切换和注销。
- 提供 MPRIS 媒体控制和桌面环境支持时的托盘操作。

## 本轮验证

- Ubuntu 24.04 amd64：源码的 30 项自动测试通过；`.deb` 重新构建，包内容、桌面入口、图标和第三方许可证文件核对通过。AppImage 在隔离的 X11 桌面会话中启动并显示窗口。
- Fedora 44 x86_64：RPM 在干净容器中安装成功，30 项自动测试通过；GTK、GStreamer 的 `playbin` 加载成功，应用在虚拟显示器中启动并持续运行 10 秒。Fedora 下尚未完成实际 QQ 登录与音频输出的人工验收。
- 三个发布包均低于项目设定的 250 MB 安装包目标。此前 Ubuntu 原生 Wayland 会话中，使用现有账号凭证读取歌单及「我喜欢」并播放歌曲成功；启动时一次采样约 93 MiB RSS、66 MiB PSS。内存值随曲库、封面与歌词加载变化。

## 已知边界

本项目使用非官方 QQ 音乐接口，登录或播放可能受接口变化、会员权限和地区版权限制影响。没有歌词数据的歌曲无法显示歌词，也不提供歌曲下载。强制 X11 会话中的触摸板歌词滚动尚未完成可靠性验收。Ubuntu 24.04 之外的 Debian 系发行版、Fedora 44 之外的 RPM 系发行版和其他架构暂不标为已验证。

本项目与腾讯音乐没有隶属或授权关系，请遵守 [腾讯音乐服务协议](https://www.tencentmusic.com/zh-cn/protocol.html)。项目以 [GPL-3.0-or-later](LICENSE) 发布；QQMusicApi 等第三方组件保留各自许可证。问题反馈请使用 [GitHub Issues](https://github.com/Timekeeperxxx/Timekeeper/issues)。
