# 小米手环健康报告改为 RSS 模式

目标：不再使用 Bark / Server 酱 / Tailscale / 本地按钮触发。脚本生成日报或周报后，把内容写入 RSS XML，RSSHub、RSS 阅读器或其他自动化工具直接订阅这个 feed。

## 现在新增的命令

生成日报并写入 RSS，不推送：

```powershell
python main.py --rss-only
```

生成周报并写入 RSS，不推送：

```powershell
python main.py --weekly --rss-only
```

默认 RSS 输出：

```text
public/health-report.xml
```

## 取消本地运行的关键

如果脚本不在本地电脑跑，云端必须能读取 Health Auto Export / AutoExportHealth 导出的 JSON。

推荐架构：

1. iPhone / 小米手环数据同步到 Apple Health。
2. AutoExportHealth 每天导出 JSON。
3. iPhone 快捷指令或云盘同步把 JSON 上传到一个云端位置。
4. GitHub Actions / 云服务器 / RSSHub 自托管服务器每天运行：

```bash
python main.py --rss-only
```

5. 生成的 `public/health-report.xml` 作为 RSS feed 发布。

## 最稳的云端方案

用 GitHub Actions + GitHub Pages：

1. 把每日 JSON 放到仓库的 `health_exports/` 目录，或让自动化上传到这个目录。
2. 在 GitHub Actions 里设置：

```text
HEALTH_EXPORT_DIR=health_exports
RSS_OUTPUT_PATH=public/health-report.xml
RSS_FEED_LINK=https://你的用户名.github.io/你的仓库名/health-report.xml
```

3. GitHub Pages 发布 `public/health-report.xml`。
4. RSS 阅读器订阅这个地址。

## 如果继续用 RSSHub

RSSHub 本身不是数据仓库，它需要能访问数据源。你有两个选择：

1. 让 RSSHub 直接订阅/代理已经生成好的 `health-report.xml`。
2. 自托管 RSSHub，并写自定义 route 去读取云端 JSON，然后调用同等分析逻辑。

更推荐第 1 个：Python 负责分析，RSSHub 或阅读器只负责订阅，稳定很多。

## 不再需要的东西

RSS 模式下可以不用：

- `trigger_server.py`
- Tailscale Serve
- iPhone 手动按钮
- `start_trigger_server.bat`
- Bark / Server 酱推送
- Windows Task Scheduler 本地定时

如果你还没把 JSON 上传到云端，暂时仍然需要本地或一台云端机器能访问 JSON 文件。
