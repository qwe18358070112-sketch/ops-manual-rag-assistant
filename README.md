# 政企运维知识库问答助手

面向政企驻场运维场景的本地资料库与知识问答系统。当前版本已经从“本机演示版”推进到“可共享、可迁移、可部署版”，具备资料上传、自动入库、增量索引、全量重建、知识问答、历史案例检索、专题复盘、资料导出、迁移包导入导出、权限控制和访问日志能力。

## 当前已落地能力

- `docx/pdf/xlsx/txt` 资料导入
- 手动上传资料到资料库并保存到本地
- 资料元数据归档与分类管理
- 增量更新单个资料索引
- 全量重建所有资料索引
- SQLite FTS 关键词检索
- 轻量 TF-IDF 语义检索
- 手册问答 / 历史案例检索双模式
- 引用式答案生成与可选大模型重写
- 案例时间轴、专题视图、案例详情、手册章节详情
- 资料统计分析与 CSV/JSON 导出
- 资料库迁移包 ZIP 导入 / 导出
- 管理员登录入口、访问日志、部署脚本和环境变量模板
- 外置数据目录、Docker 部署和共享启动脚本

## 项目结构

```text
ops-manual-rag-assistant/
  app/
    api/               # 原有 JSON API 与兼容页面
    core/              # 配置、权限、访问日志中间件
    ingestion/         # 文档解析与切块
    models/            # 数据结构
    retrieval/         # 关键词/语义检索索引
    services/          # 问答、案例、资料库服务
    static/            # 新 UI 样式
    templates/         # 新 UI 模板
    web/               # 新版页面与资料库路由
  data/
    raw/uploads/       # 手动上传的资料
    extracted/         # 解析后的结构化结果
    chunks/            # 切块结果
    indexes/           # 检索索引与汇总
    library/           # 资料库元数据 SQLite
    logs/              # 访问日志
    exports/           # 导出文件
    manifests/         # 首批种子文档清单
  deploy/systemd/      # systemd 模板
  docs/                # 测试、验收、部署与项目说明
  scripts/             # 导入、索引、验收和运维脚本
  tests/               # 自动化回归测试
```

## 数据存储方式

当前使用的是“本地文件 + 本地 SQLite + 本地索引”的轻量方案，但已经支持把数据目录从项目目录挪到外部磁盘、服务器挂载目录或 Docker volume。

- 原始资料文件：`$OPS_ASSISTANT_DATA_DIR/raw/uploads/`
- 解析结果：`$OPS_ASSISTANT_DATA_DIR/extracted/`
- 切块结果：`$OPS_ASSISTANT_DATA_DIR/chunks/`
- 检索索引：`$OPS_ASSISTANT_DATA_DIR/indexes/`
- 资料库元数据库：`$OPS_ASSISTANT_DATA_DIR/library/metadata.sqlite3`
- 访问日志：`$OPS_ASSISTANT_DATA_DIR/logs/access.log`

如果未设置 `OPS_ASSISTANT_DATA_DIR`，默认仍然使用项目内 `data/`。

## 安装依赖

推荐使用 `uv`：

```bash
cd /home/lenovo/projects/ops-manual-rag-assistant
uv venv
uv pip install -p .venv/bin/python -e '.[dev]'
```

## 启动本地服务

```bash
cd /home/lenovo/projects/ops-manual-rag-assistant
cp .env.example .env
# 按需修改 .env 后
./scripts/start_local.sh
```

启动后访问：

- 新版总览：`http://127.0.0.1:8000/app`
- 知识问答：`http://127.0.0.1:8000/app/search`
- 资料库管理：`http://127.0.0.1:8000/app/library`
- 时间轴：`http://127.0.0.1:8000/app/timeline`
- 专题视图：`http://127.0.0.1:8000/app/topics`

## 共享使用

如果需要让局域网内其他人也能访问，不需要改代码，只要：

1. 在 `.env` 里设置：

```bash
OPS_ASSISTANT_HOST=0.0.0.0
OPS_ASSISTANT_PORT=8000
OPS_ASSISTANT_REQUIRE_AUTH=true
OPS_ASSISTANT_ADMIN_USERNAME=admin
OPS_ASSISTANT_ADMIN_PASSWORD=change-me
```

2. 启动共享服务：

```bash
./scripts/start_shared.sh
```

3. 让其他人在同一网络下访问：

```text
http://你的电脑IP:8000/app
```

推荐把资料库管理页保持管理员认证开启，普通用户使用 `/app/search`、`/app/timeline`、`/app/topics` 即可。

兼容 JSON/API 路由仍可继续使用：

- `GET /healthz`
- `GET /catalog`
- `GET /search?q=...`
- `GET /case-timeline`
- `GET /topic-view`

## 资料库管理

### 1. 上传资料并自动入库

进入 `/app/library` 后可直接上传资料。支持字段：

- 资料标题
- 系统名称
- 来源类型
- 标签
- 日期范围
- 备注说明
- 文件选择
- 是否立即执行增量索引

支持格式：

- `.docx`
- `.pdf`
- `.xlsx`
- `.txt`

上传成功后：

1. 文件保存到 `data/raw/uploads/`
2. 资料元数据写入 `data/library/metadata.sqlite3`
3. 若勾选“立即执行增量索引”，会自动完成解析、切块和索引刷新
4. 资料可立刻进入问答和检索结果

### 2. 增量更新与全量重建

- 页面上可对单个文档执行“重新索引”
- 页面上可执行“全量重建”
- 也可使用脚本：

```bash
cd /home/lenovo/projects/ops-manual-rag-assistant
.venv/bin/python scripts/manage_library.py rebuild
.venv/bin/python scripts/manage_library.py reindex <doc_id>
```

### 3. 导出与分析

资料库页支持：

- 导出资料列表 CSV
- 导出资料列表 JSON
- 导出资料分析 JSON

命令行也支持：

```bash
.venv/bin/python scripts/manage_library.py export --kind documents --format csv
.venv/bin/python scripts/manage_library.py export --kind analysis --format json
.venv/bin/python scripts/manage_library.py analysis
```

### 4. 迁移包导入 / 导出

资料库页面 `/app/library` 已支持：

- 导出迁移包 ZIP
- 上传迁移包 ZIP 并导入

命令行也支持：

```bash
.venv/bin/python scripts/manage_library.py bundle-export --output /tmp/ops-assistant-bundle.zip
.venv/bin/python scripts/manage_library.py bundle-import --input /tmp/ops-assistant-bundle.zip
.venv/bin/python scripts/manage_library.py bundle-import --input /tmp/ops-assistant-bundle.zip --replace
```

说明：

- 默认导入会跳过当前库里已存在的 `doc_id`
- `--replace` 会先清空当前资料记录，再按迁移包导入并重建索引
- 迁移包会打包资料原件、标签、日期、分类、备注等元数据

这意味着后续迁移到另一台电脑或服务器时，不需要手工拷贝多层目录，只要导出 ZIP 再导入即可。

## 权限控制与访问日志

默认本地模式下 `OPS_ASSISTANT_REQUIRE_AUTH=false`，资料库管理页可直接访问。

如果需要启用管理员控制，请在 `.env` 中设置：

```bash
OPS_ASSISTANT_REQUIRE_AUTH=true
OPS_ASSISTANT_ADMIN_USERNAME=admin
OPS_ASSISTANT_ADMIN_PASSWORD=change-me
OPS_ASSISTANT_ADMIN_TOKEN=
```

启用后：

- `/app/library` 等管理页会先跳转到 `/login`
- 登录成功后会写入本地会话 cookie
- 也可以使用 `X-Admin-Token` 作为接口级管理员令牌

访问日志保存在：

- `data/logs/access.log`

日志内容包含：

- 请求时间
- 请求方法
- 请求路径
- 状态码
- 响应耗时
- 客户端地址
- 浏览器 UA
- 是否带管理员 Cookie / Token

## 运行测试

### 自动化回归测试

```bash
cd /home/lenovo/projects/ops-manual-rag-assistant
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

当前已覆盖：

- 原有搜索与案例链路
- 新版 `/app` 页面是否正常渲染
- 资料上传后是否能被搜索命中
- 资料导出接口是否可用

### 正式验收脚本

```bash
cd /home/lenovo/projects/ops-manual-rag-assistant
.venv/bin/python scripts/run_acceptance_checks.py
```

验收报告输出到：

- `docs/落地验收报告.md`

## 部署

### 本地启动脚本

```bash
./scripts/start_local.sh
```

### systemd 模板

参考：

- `deploy/systemd/ops-manual-rag-assistant.service`

这个模板已改成读取 `.env` 并调用 `scripts/start_shared.sh`，适合直接做局域网共享部署。

### Docker / 服务器部署

已提供：

- `Dockerfile`
- `deploy/docker-compose.yml`

默认会把资料目录挂到容器外：

```bash
cd /home/lenovo/projects/ops-manual-rag-assistant/deploy
docker compose up -d --build
```

这样项目代码可以迁移或重建，资料库数据仍保留在 volume 中，更适合长期运行。

### 全量重建脚本

```bash
./scripts/rebuild_indexes.sh
```

## 当前验收结论

当前版本已经达到：

- 本地可运行
- 可上传资料
- 可增量索引
- 可全量重建
- 可问答检索
- 可案例复盘
- 可导出分析
- 可开启管理员控制
- 可记录访问日志

详细见：

- `docs/落地验收报告.md`
- `docs/部署与运维手册.md`
- `docs/当前阶段代码审查与修复说明.md`
- `docs/共享部署与迁移手册.md`

## 公开仓库说明

这是用于展示项目能力的公开代码仓库版本，已移除本地资料库数据、上传资料、索引文件、访问日志和环境配置文件。
