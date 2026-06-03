# Hope Health RSS

自动把 `health_exports/*.json` 生成健康日报/周报 RSS。

## RSS 订阅地址

- https://cdn.jsdelivr.net/gh/Hope328/hope-health-rss@main/public/health-report.xml

## 自动触发规则

- 每天 00:30（Australia/Brisbane）自动生成日报
- 每周日 09:00（Australia/Brisbane）自动生成周报
- 当你上传新的 `health_exports/*.json` 到 `main` 分支时，也会自动触发
- 手机端可以在 GitHub Actions 里手动运行 `Publish health RSS`，并选择 `daily` 或 `weekly`

## 数据来源目录

- `health_exports/`

把 AutoExportHealth JSON 放进这个目录即可（例如 `HealthAutoExport-2026-22.json`）。
