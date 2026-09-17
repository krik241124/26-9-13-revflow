# RevFlow

从 CL 提取 SKU 信息与图片，保留原有请求解析、lineGuid resolver、去重、图片策略与续跑行为。

推荐入口：根目录 `run.bat extract`；也可用项目 Python 直接运行本目录 `revflow.py`。路径不依赖当前工作目录。

- 国家和抓取参数：`../config/runtime.json`。
- 输入：`../workspace/<market>/01_detect/` 完整 Detect 结果。
- 凭证：本目录 `getdetail.curl.txt`，需来自 CL 对应国家。
- 输出：`../workspace/<market>/02_extract/`，包含 summary、商品/图片以及 run_context.json。
- `--force`：重新提取待创建 SKU；默认复用已完成缓存。

不再读取旧 config.json、根目录 skus.txt 或 audit_to_do_skus.txt。缺检测、检测错误、混合结果均停止。详细步骤见 [使用说明](../docs/使用说明.md)。
