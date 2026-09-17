# SKU Write

保留原有 Mapping、图片容错、Payload、草稿保存、150035、断点续跑和 Audit 逻辑。

推荐根目录入口：`run.bat preflight` → 必要时 `run.bat mapping` → 再 preflight → `run.bat dry-run` → `run.bat write`。

国家与店铺统一来自根 config；本目录 config.json 仅保留 run/upload/defaults 策略。auth.json 为本机凭证，交付仅提供空 auth.example.json。

输入为 workspace/<market>/02_extract，输出为 workspace/<market>/03_write。mapping 只存三个正式知识库 JSON；映射备份与建议 CSV 存 review。

dry-run 读取实时字典但不上传、不保存草稿、不修改生产 State/Audit。Preflight 的 BLOCKED/CL_ERROR 返回非零退出码；正式 SOP 要先解决，main 仍做逐 SKU 第二次校验。

参数：main.py 支持 --sku、--dry-run、--force；apply_mapping_review.py 支持 --file（相对项目根目录）。详见 [使用说明](../docs/使用说明.md)。
