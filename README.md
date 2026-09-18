# ArkSwift AutoListing

ArkSwift 内部自动上品工具。日常运营通过本地 Web 控制台完成：

`SKU Detect → RevFlow → Preflight / Mapping → Dry Run → ArkSwift Draft / Audit`

**运营人员：直接双击 `使用说明.html` 查看完整说明。**

## 快速开始

第一次在一台电脑上使用：

1. 安装 Python 3.10+。`setup.bat` 会优先使用 `py -3`，没有 Python Launcher 时也会尝试普通 `python`。
2. 双击 `setup.bat`，等待依赖安装与工作目录初始化完成。
3. 双击 `start.bat`，浏览器会自动打开本地控制台。

以后日常使用只需双击 `start.bat`。

控制台只监听 `127.0.0.1`，默认端口 8765；端口占用时会自动选择 8766～8775。版本号以 `VERSION.txt` 和控制台右上角显示为准。

## 日常流程

1. 选择目标市场。ArkSwift 登录按市场/店铺隔离并需在切换国家后重新 Check / Sign in；CL 登录态全局复用，国家上下文由 `config/markets.json` 自动选择。
2. **Step 1 · SKU Detect**：粘贴或导入 SKU，确认 Errors 为 0。
3. **Step 2 · RevFlow**：抓取 CL 产品资料和图片。
4. **Step 3 · Preflight / Mapping**：处理 Mapping、尺寸、重量、图片等阻塞项，直到预检通过。
5. **Step 4 · SKU Write**：先 Dry Run，再人工确认创建 ArkSwift 草稿；最后查看 Audit。

运营人员无需运行 Python、修改 JSON、复制中间文件或使用浏览器 Console。

## 目录说明

| 路径 | 用途 |
|---|---|
| `webui/` | 本地 Web 控制台 |
| `sku_detect/` | Python SKU Detect |
| `revflow/` | CL 数据与图片提取 |
| `sku_write/` | Preflight、Mapping、图片上传、Draft、Audit |
| `config/` | 市场与运行参数 |
| `workspace/` | 运行时生成；按市场保存输入、结果、State、Audit、Logs；不进入源码交付包 |
| `docs/` | 维护说明、故障排查、店铺配置 |
| `tools/build_release.py` | 白名单生成干净源码交付包 |

## 维护人员 CLI

日常优先使用 Web 控制台。排查或受控维护时可在项目根目录运行：

```powershell
.\run.bat check
.\run.bat detect
.\run.bat detect --retry-errors
.\run.bat extract
.\run.bat extract --force
.\run.bat preflight
.\run.bat mapping
.\run.bat dry-run
.\run.bat write
.\run.bat test
```

详细边界见 `docs/维护说明.md`。

## 凭证与运行数据

以下内容**只能保存在本机运行环境，不得进入源码 ZIP 或版本库**：

- `sku_write/auth.json`
- `revflow/getdetail.curl.txt`
- `workspace/`
- `archive/`
- `.venv/`
- `__pycache__/`、日志、临时 ZIP

仓库只保留空凭证模板 `sku_write/auth.example.json` 和说明模板 `revflow/getdetail.curl.example.txt`。

## 生成正式交付包

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe tools\build_release.py
```

脚本自动读取 `VERSION.txt`，生成 `ArkSwift_AutoListing_v<version>.zip`，并使用白名单排除真实凭证、工作区、历史归档、虚拟环境和缓存。
