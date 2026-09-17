# SKU Detect

v1.1 首选根目录 start.bat 控制台，或 run.bat detect。新增 detect.py 复用现有 Python Client；run.bat detect --retry-errors 会在完整清单中合并重试结果。以下 JS 保留作为浏览器备用入口。

在已登录 ArkSwift 的 Chrome / Edge 控制台执行 `sku_detect.js`，按页面面板选择本项目的 config/runtime.json 和 config/markets.json，再选择 workspace/<market>/00_input/skus.txt。

国家与店铺来自同一份配置，面板会显示供核对。无需改 JS；未确认 storeId 会停止。

扫描后将结果保存到 workspace/<market>/01_detect。包括 all、need_create、errors 三个 TXT 和 detect.json 完整性记录。三个 TXT 的空白行不会代表成功；错误清单必须为空、整套文件必须匹配，RevFlow 才接受。

保留原有正常商品过滤、精确 SKU 匹配和重试策略。API/网络/格式错误进入 errors，不能当作不存在或已存在。
出现错误请修复后重新检测原始完整清单，不能只重测错误 SKU 并覆盖 all 文件。详细流程见 [使用说明](../docs/使用说明.md)。
