# Zero-F12 认证实现

## 目标

普通用户只做一件事：**在官方网页正常登录**。

系统自动完成：

`验证旧会话 → 失效则打开浏览器 → 用户登录 → CDP 捕获会话 → API Workflow 继续运行`

## 为什么不用 Playwright

正式交付选择直接使用 Chrome/Edge 自带的 Chrome DevTools Protocol（CDP）：

- 不下载 Playwright Chromium；
- 不需要 chromedriver / Selenium Manager；
- Windows 上优先使用已安装 Chrome，没有则尝试 Edge；
- Python 侧只增加很小的 `websocket-client` 依赖，由 `setup.bat` 自动安装；
- 浏览器升级时不需要匹配 driver 版本。

## 代码位置

- `browser_auth.py`：浏览器检测、启动、CDP、Cookie/请求头捕获、会话验证。
- `webui/app.py`：工作流运行前调用 `ensure_arkswift()` / `ensure_cl()`；失效时自动触发网页登录。
- `revflow/revflow.py`：不再解析用户 cURL；CL Cookie/account 来自 `workspace/_auth/cl.json`，country/language 来自 `config/markets.json`。
- `webui/templates/index.html` / `webui/static/app.js`：UI 从“Configure Cookie/cURL”改成“Sign in / refresh”。

## ArkSwift

捕获目标：`Authorization_web` 以及同域 Cookie。程序仍写入既有 `sku_write/auth.json`，因此原有 SKU Detect / Preflight / Write 客户端无需重写。

## CL

捕获目标：

- 当前 `cl.aosom.cloud` Cookie Jar；
- 请求头 `account`；
- User-Agent / Referer；
- 一个可复用的轻量 GET probe URL（如页面自然产生）。

保存到 `workspace/_auth/cl.json`。

RevFlow 的 GetDetail URL 由程序构造：

- `keyValue`：当前 SKU；
- `language`：`config/markets.json → cl_language`；
- `country`：`config/markets.json → cl_country_guid`；
- `lineGuid`：现有 `GetPageList` 自动解析；
- `_`：当前毫秒时间戳。

因此不再需要真实 GetDetail cURL。

## 新电脑使用

1. 解压正式包；
2. 安装 Python 3.10+；
3. 双击 `setup.bat`；
4. 双击 `start.bat`；
5. 第一次运行需要 ArkSwift/CL 的步骤时，登录窗口会自动出现；
6. 正常登录后返回控制台继续即可。

不需要预装 Playwright。Chrome 与 Edge 至少存在一个即可；如果公司镜像没有二者，可安装任意一个，或用 `ARKSWIFT_BROWSER_EXE` 指向 Chromium 系浏览器可执行文件。
