# Apple Health 中文健康日报

这是一个面向 Windows 本地环境的 Python 项目，用来读取 Health Auto Export 导出的 Apple Health JSON 文件，生成中文健康日报/周报，并通过 Server酱推送到微信。

现在项目支持两种触发方式：

- 定时自动发送：适合 Windows Task Scheduler
- 手机一键触发：适合 iPhone 快捷指令调用本地 webhook

## 文件结构

```text
.
├── main.py
├── trigger_server.py
├── report_workflow.py
├── config.py
├── health_parser.py
├── report_generator.py
├── notifier.py
├── utils.py
├── requirements.txt
├── .env.example
├── start_trigger_server.bat
└── README.md
```

## 环境要求

- Windows
- Python 3.10+

## 安装

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

## 配置 .env

按你的实际环境修改 `.env`：

```env
HEALTH_EXPORT_DIR=C:\Users\Hope\Google Drive\HealthExport
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-5.5
SERVERCHAN_SENDKEY=SCTxxxxxx
REPORT_DAYS=7
TRIGGER_HOST=0.0.0.0
TRIGGER_PORT=8787
TRIGGER_TOKEN=change-this-token
```

配置说明：

- `HEALTH_EXPORT_DIR`：Health Auto Export 同步到电脑后的 JSON 目录
- `OPENAI_API_KEY`：可选，不填时自动使用本地规则版日报
- `OPENAI_MODEL`：可选，默认 `gpt-5.5`
- `SERVERCHAN_SENDKEY`：可选，不填时只打印不推送
- `REPORT_DAYS`：趋势窗口，默认 `7`
- `TRIGGER_HOST`：手机一键触发服务监听地址
  - 如果只想本机测试，可用 `127.0.0.1`
  - 如果想让 iPhone 通过同一 Wi-Fi 访问，建议设为 `0.0.0.0`
- `TRIGGER_PORT`：触发服务端口，默认 `8787`
- `TRIGGER_TOKEN`：手机触发用的简单口令，建议一定设置

## 手动运行

生成昨日正式日报：

```powershell
python main.py
```

生成本周周报：

```powershell
python main.py --weekly
```

生成今天的进度报告：

```powershell
python main.py --today-partial
```

## 手机上一键触发

### 1. 先启动触发服务

推荐直接双击：

```text
start_trigger_server.bat
```

或者手动运行：

```powershell
python trigger_server.py
```

启动后服务会监听：

```text
http://TRIGGER_HOST:TRIGGER_PORT
```

例如你电脑当前局域网 IP 如果是 `192.168.1.105`，端口是 `8787`，那手机请求地址就是：

```text
http://192.168.1.105:8787
```

### 2. 可用接口

- 日报：`/report/daily`
- 周报：`/report/weekly`
- 今日进度报告：`/report/today-partial`
- 健康检查：`/health`

如果你配置了 `TRIGGER_TOKEN`，请求时需要带上：

- Header：`X-Trigger-Token: 你的口令`

也支持放在 URL 查询参数里：

```text
http://192.168.1.105:8787/report/weekly?token=你的口令
```

### 3. iPhone 快捷指令设置

在 iPhone 的“快捷指令”里新建一个快捷指令，例如叫“要周报”。

添加动作：

1. `获取 URL 内容`
2. URL 填：

```text
http://192.168.1.105:8787/report/weekly
```

3. 方法选 `POST`
4. Header 加一项：

```text
X-Trigger-Token: 你的TRIGGER_TOKEN
```

5. 可选再加一个 `显示结果`

如果你想做日报快捷指令，把 URL 换成：

```text
http://192.168.1.105:8787/report/daily
```

如果你想看今天的未完成进度报告，把 URL 换成：

```text
http://192.168.1.105:8787/report/today-partial
```

### 4. 触发后的效果

快捷指令触发后，Windows 上的服务会：

1. 读取最新健康 JSON
2. 生成日报或周报
3. 调用 Server酱推送到你的微信
4. 同时把报告正文作为 JSON 响应返回给快捷指令

## Tailscale 模式

如果你希望人在外面时也能点一下 iPhone 快捷指令触发日报/周报，推荐使用 Tailscale。

为什么推荐：

- 不需要路由器端口映射
- 不需要把服务直接暴露到公网
- iPhone 和 Windows 只要都登录同一个 Tailscale 账号，就能互相访问

官方文档：

- Windows 安装：[Install Tailscale on Windows](https://tailscale.com/docs/install/windows)
- iPhone 安装：[Install Tailscale on iOS](https://tailscale.com/docs/install/ios)
- 本地服务共享：[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- CLI 参考：[tailscale serve command](https://tailscale.com/docs/reference/tailscale-cli/serve)

### 逐步照抄版

#### 1. 在 Windows 安装 Tailscale

根据官方 Windows 文档（Last validated: November 21, 2025）：

1. 打开 [Tailscale Windows 安装页](https://tailscale.com/docs/install/windows)
2. 下载最新 `.exe`
3. 双击安装
4. 安装完成后，右下角托盘会出现 Tailscale 图标
5. 右键托盘图标，点击 `Log in`
6. 浏览器里完成登录

#### 2. 在 iPhone 安装 Tailscale

根据官方 iOS 文档（Last validated: February 21, 2025）：

1. 在 App Store 搜索 `Tailscale`
2. 安装并打开
3. 点击 `Get Started`
4. 允许安装 VPN 配置
5. 用和 Windows 同一个账号登录

#### 3. 把项目切到 Tailscale 更稳的本地监听方式

Tailscale Serve 反代本地服务时，官方 CLI 说明里写的是代理到 `http://127.0.0.1` 这类本地地址，因此建议在 `.env` 里使用：

```env
TRIGGER_HOST=127.0.0.1
TRIGGER_PORT=8787
TRIGGER_TOKEN=你的口令
```

如果你之前为了同 Wi-Fi 直连写成了 `0.0.0.0`，Tailscale 模式下建议改回 `127.0.0.1`。

#### 4. 启动本地触发服务

```powershell
start_trigger_server.bat
```

或者：

```powershell
python trigger_server.py
```

#### 5. 在 Windows 上开启 Tailscale Serve

安装 Tailscale 后，打开 PowerShell，先确认命令可用：

```powershell
tailscale version
```

然后运行：

```powershell
tailscale serve --bg http://127.0.0.1:8787
```

说明：

- `--bg` 表示在后台保存 Serve 配置
- `http://127.0.0.1:8787` 是把你本地的快捷指令触发服务共享到 Tailscale 网络

如果是第一次启用 Serve，Tailscale 可能会弹出浏览器页面让你确认启用 HTTPS 或 Serve，按页面提示同意就可以。

查看是否生效：

```powershell
tailscale serve status
```

#### 6. 找到你的 Tailscale 访问地址

启用后，常见访问形式会是：

```text
https://你的电脑名.你的tailnet.ts.net
```

或者你也可以在：

```powershell
tailscale status
```

里先确认这台 Windows 机器的名字。

#### 7. 在 iPhone 快捷指令里改成 Tailscale 地址

周报：

```text
https://你的电脑名.你的tailnet.ts.net/report/weekly?token=你的TRIGGER_TOKEN
```

日报：

```text
https://你的电脑名.你的tailnet.ts.net/report/daily?token=你的TRIGGER_TOKEN
```

今日进度：

```text
https://你的电脑名.你的tailnet.ts.net/report/today-partial?token=你的TRIGGER_TOKEN
```

快捷指令里最省事的写法：

1. 新建快捷指令
2. 添加动作 `获取 URL 内容`
3. 方法选 `GET`
4. 直接把 `token` 写进 URL 查询参数
5. 再加一个 `显示结果`

#### 8. 这样以后怎么用

- 在家、同 Wi-Fi：你可以继续用 `192.168.1.105:8787`
- 在外面：用 Tailscale 的 `https://你的电脑名....ts.net/...`
- 两种模式可以并存

### 常用命令

启动项目本地触发服务：

```powershell
python trigger_server.py
```

开启 Tailscale Serve：

```powershell
tailscale serve --bg http://127.0.0.1:8787
```

查看 Serve 状态：

```powershell
tailscale serve status
```

关闭 Serve：

```powershell
tailscale serve reset
```

## Windows Task Scheduler

### 定时发日报

- `Program/script`：你的 Python 路径，例如 `.venv\Scripts\python.exe`
- `Add arguments`：`main.py`
- `Start in`：项目目录

### 开机自动启动手机触发服务

如果你希望电脑开着时，手机随时都能点一下触发，可以再建一个“开机或登录时运行”的任务：

- `Program/script`：`cmd.exe`
- `Add arguments`：`/c start_trigger_server.bat`
- `Start in`：项目目录

## 常见问题

### 1. 手机点了快捷指令没反应

先检查：

- 电脑和 iPhone 是否在同一 Wi-Fi
- `trigger_server.py` 是否已经运行
- Windows 防火墙是否放行了对应端口
- `TRIGGER_HOST` 是否设成了 `0.0.0.0`
- 快捷指令里的 IP 是否写对

### 2. 微信没收到推送

先检查：

- `SERVERCHAN_SENDKEY` 是否正确
- Server酱服务是否正常
- Windows 控制台里是否出现发送失败日志

### 3. OpenAI 没配置会不会坏

不会。

没有 `OPENAI_API_KEY` 时，会自动回退到本地规则版日报/周报。

### 4. 为什么今天有时会自动发昨天日报

因为项目会判断“今天文件是否只是部分时间段数据”。

如果今天的文件只同步到凌晨或中午，不算完整日，就会自动切回昨天，保证正式日报不被半天数据带歪。

### 5. 为什么心率、站立小时、活动圆环经常没有

当前模式按 `iPhone + 小米手环` 设计。

Apple Watch 专属指标不是本项目的核心判断依据；心率如果没有同步到 Apple Health，只会提示“暂不分析心率”，不会当成异常。

## 说明

- 这个项目只做生活方式层面的趋势分析，不做医学诊断
- 如果心率数据偏高，报告只会给出温和提醒，不会做诊断性结论
- 项目优先保证“缺少部分数据也能跑通”，不会因为某个指标缺失就直接崩溃

## 推荐自动化配置

如果你想要“iPhone 点一下就收到微信推送的日报”，以及“每周固定自动推周报”，推荐按下面这套方式配置：

### 每天自动日报

1. iPhone / Health Auto Export 在每天 `00:05` 左右自动导出到云盘
2. 等云盘同步到 Windows
3. Windows Task Scheduler 在每天 `00:20` 运行：

```text
send_daily_report.bat
```

这样脚本会自动判断：

- 如果当天文件还是凌晨的半天数据，不会误发“今日日报”
- 会自动切回昨天的完整正式日报

### 每周自动周报

如果你希望“周日报告包含整个周日的数据”，更稳的时间其实是：

- `周日 23:30`
- 或 `周一 00:20`

运行：

```text
send_weekly_report.bat
```

如果你在 `周日 00:20` 就跑周报，那本质上统计到的还是周六结束前的完整数据。

### iPhone 一键触发

电脑上先运行：

```text
start_trigger_server.bat
```

如果 iPhone 和电脑在同一个 Wi-Fi，下发 URL 可以使用电脑局域网地址，例如：

- 日报：`http://192.168.1.105:8787/report/daily`
- 周报：`http://192.168.1.105:8787/report/weekly`
- 今日进度：`http://192.168.1.105:8787/report/today-partial`

快捷指令里记得加 Header：

```text
X-Trigger-Token: 你的 .env 里的 TRIGGER_TOKEN
```

### 本地窗口 / 按钮

如果你想在 Windows 上点按钮直接看报告，运行：

```text
start_dashboard.bat
```

会打开一个本地窗口，里面有：

- `看日报`
- `看周报`
- `看今日进度`
- `检查服务`

点一下就会：

1. 生成报告
2. 推送到微信
3. 同时在窗口里显示完整正文
