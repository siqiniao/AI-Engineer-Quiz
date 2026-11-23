# 素材检索与 CSV 导出交付说明

## 本地运行

以下命令在仓库根目录执行。建议使用 Python 3.12 和 uv。SQLite 需要启用 FTS5 和 JSON 函数。

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'

demo_dir="$(mktemp -d)"
KEEPSTACK_DATA_DIR="$demo_dir" KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=false \
  uv run --no-sync python -m keepstack seed
KEEPSTACK_DATA_DIR="$demo_dir" KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=false \
  KEEPSTACK_HOST=127.0.0.1 uv run --no-sync python -m keepstack
```

打开 http://127.0.0.1:8000，用演示账号 `admin` / `admin` 登录。Seed 生成 24 个素材和 3 个集合。

## 复现与实测

```bash
env -u KEEPSTACK_OCR_ENABLED uv run --no-sync python -m pytest -q -rs
uv run --no-sync python -m pytest -q tests/test_asset_search.py
node --check web/app.js
```

- 全量测试 **27 通过、1 跳过**。跳过项是原有的本地视觉模型测试，当前环境未安装该模型。
- [自动化测试](tests/test_asset_search.py)在临时目录中生成图片，经登录、上传、集合和编辑 API 建立数据。同一创建时间的素材跨页分布，并加入不匹配的标签、集合、关键词、类型和状态。测试验证两种时间排序、列表与 CSV 的 UUID 顺序、表头和特殊字符、非法分页、未登录访问及编辑前后结果。
- Chrome 实测登录后按关键词、集合、多个包含标签、排除标签和媒体类型筛选。14 条结果分页为 12 + 2，切换排序回到第一页，从第二页下载得到完整且顺序一致的 14 行。验证了快速切换、空结果、请求或下载失败反馈、重试、清除筛选、窄屏控件，以及在详情页编辑标题、描述和新增标签后的查询与下载。
- 含斜杠标签的删除已补充回归测试。Chrome 中在详情页添加 `campaign/2026` 后点击旁边的 `×`，删除请求返回 `200`。随后按该标签筛选得到零条，实际下载的 CSV 只有表头。自动化测试同时验证 FTS 不再命中已删除的标签，素材及其他标签保留。
- 使用默认 seed 手工复现时，可在 `Environment & Survey` 集合中打开两条 `Coastal survey photo` 素材，在标题加上 `Harbor`，为两条分别添加 `approved` 和 `campaign`，并为其中一条添加 `internal`。回到 Library，选择该集合和媒体类型 `image`，搜索 `harbor`，勾选两个包含标签并排除 `internal`。页面应显示 1 条结果，CSV 应包含表头和对应的一行。

## 主要取舍与限制

- 列表和 CSV 共用关键词、集合、媒体类型、标签及排序的 SQL 构造。沿用 FTS5 字段、分词和词前缀匹配。空白关键词不加限制，纯标点返回零结果。标签按完整名称匹配，沿用去除首尾空白和小写化规则。
- 页面每页 12 条。时间排序使用资产 ID 升序处理时间相同的记录，保证数据稳定时跨页顺序确定。偏移分页在跨请求有数据写入时可能发生位移。列表请求可取消，较早的响应不会覆盖新条件。
- `GET /api/assets` 和 `GET /api/assets/export.csv` 都使用原有 Bearer 登录鉴权。多值标签通过重复的 `include_tags` 和 `exclude_tags` 查询参数传递。导出的 `tags` 列是按名称排序的 JSON 字符串数组，可在 CSV 解析后再解码。CSV 使用带 BOM 的 UTF-8，保留原始字段文本。用电子表格打开外部来源的数据时，可将字段按文本导入。
- 同步导出在内存中构建 CSV，空间占用随结果大小增长。前端隐藏了语义搜索选项。既有语义接口仍可调用，CSV 对应本题的关键词与结构化筛选。
- 标题、描述及标签的编辑与 FTS 重建同事务提交。修复了原有前端提交 JSON 时缺少默认 POST 的问题，以恢复登录和标签编辑这两个验收入口。服务端与浏览器端均沿用原有技术栈。
- 题目范围内的功能已完成。跳过的本地视觉模型测试属于语义功能。
