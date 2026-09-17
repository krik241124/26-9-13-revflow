(async () => {
  const CONFIG = {
    storeId: "",
    lang: "cn",

    // 当前先查询“正常”商品
    status: "1",
    freezeStatus: "1",

    pageSize: 20,

    // 每个 SKU 请求完成后等待多少毫秒
    delayMs: 250,

    // 网络异常自动重试
    maxRetries: 3
  };

  // Read the same two configuration files used by every Python stage.
  async function chooseConfiguration() {
    return new Promise(resolve => {
      const panel = document.createElement("div");
      Object.assign(panel.style, {
        position: "fixed", top: "20px", right: "20px", zIndex: "2147483647",
        padding: "16px", background: "white", color: "black", border: "2px solid #2874a6"
      });
      const label = document.createElement("div");
      label.textContent = "选择 config 目录中的 runtime.json 和 markets.json（可同时选中）";
      const picker = document.createElement("input");
      picker.type = "file";
      picker.accept = ".json";
      picker.multiple = true;
      const error = document.createElement("div");
      error.style.color = "#b00";
      const loaded = {};
      picker.onchange = async () => {
        try {
          for (const file of picker.files) {
            loaded[file.name] = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
          }
          if (!loaded["runtime.json"] || !loaded["markets.json"]) {
            error.textContent = "还需要选择另一个配置文件：runtime.json / markets.json";
            return;
          }
          const market = loaded["runtime.json"].market;
          const info = loaded["markets.json"][market];
          if (!/^[a-z]{2}$/.test(market) || !info || info.country_code !== market.toUpperCase()) {
            throw new Error("市场配置不一致，请检查 config 目录。");
          }
          if (typeof info.store_id !== "string" || !/^\d+$/.test(info.store_id)) {
            throw new Error(`${market} 的 store_id 尚未确认，请填写 markets.json；ID 必须用引号。`);
          }
          panel.remove();
          resolve({market, storeId: info.store_id});
        } catch (exc) {
          error.textContent = exc.message;
        }
      };
      panel.append(label, picker, error);
      document.body.appendChild(panel);
    });
  }

  const selectedConfig = await chooseConfiguration();
  CONFIG.storeId = selectedConfig.storeId;

  const sleep = ms =>
    new Promise(resolve => setTimeout(resolve, ms));

  // =========================
  // 1. 打开 TXT 文件选择器
  // =========================

  function chooseCountryAndFile() {
    return new Promise((resolve, reject) => {
      const old = document.getElementById(
        "__sku_detect_start_panel__"
      );

      if (old) {
        old.remove();
      }

      const panel = document.createElement("div");
      panel.id = "__sku_detect_start_panel__";

      Object.assign(panel.style, {
        position: "fixed",
        top: "20px",
        right: "20px",
        zIndex: "2147483647",
        padding: "14px",
        borderRadius: "10px",
        background: "#fff",
        color: "#111",
        boxShadow: "0 4px 18px rgba(0,0,0,.25)",
        fontFamily: "Arial, sans-serif",
        fontSize: "14px"
      });

      const title = document.createElement("div");
      title.textContent = "SKU Detect";
      title.style.fontWeight = "700";
      title.style.marginBottom = "10px";

      const input = document.createElement("input");
      input.value = selectedConfig.market;
      input.readOnly = true;
      input.title = "国家来自 config/runtime.json";
      title.textContent = `SKU Detect | ${selectedConfig.market.toUpperCase()} | storeId=${CONFIG.storeId}`;
      input.maxLength = 3;

      Object.assign(input.style, {
        width: "180px",
        padding: "8px 10px",
        marginRight: "8px",
        border: "1px solid #ccc",
        borderRadius: "6px"
      });

      const button = document.createElement("button");
      button.textContent = "选择 SKU 文件并开始";

      Object.assign(button.style, {
        padding: "8px 12px",
        border: "0",
        borderRadius: "6px",
        cursor: "pointer",
        fontWeight: "600"
      });

      const errorText = document.createElement("div");
      errorText.style.marginTop = "8px";
      errorText.style.fontSize = "12px";
      errorText.style.color = "#c00";

      button.onclick = () => {
        const country =
          input.value.trim().toLowerCase();

        if (!/^[a-z]{2,3}$/.test(country)) {
          errorText.textContent =
            "请输入 2~3 位国家代码，例如 ca / fr / us / ro";
          input.focus();
          return;
        }

        const fileInput =
          document.createElement("input");

        fileInput.type = "file";
        fileInput.accept =
          ".txt,text/plain,.csv";

        fileInput.onchange = () => {
          const file = fileInput.files?.[0];

          if (!file) {
            return;
          }

          panel.remove();

          resolve({
            country,
            file
          });
        };

        // 必须直接发生在真实点击事件里，
        // 否则 Chrome 会阻止文件选择器。
        fileInput.click();
      };

      panel.appendChild(title);
      panel.appendChild(input);
      panel.appendChild(button);
      panel.appendChild(errorText);

      document.body.appendChild(panel);

      input.focus();
    });
  }

  // =========================
  // 2. 解析 SKU
  // =========================

  function parseSkus(text) {
    const values = text
      .split(/[\r\n,;\t]+/)
      .map(x => x.trim())
      .filter(x => x && !x.startsWith("#"));

    // 保留原始顺序，同时去重
    return [...new Set(values)];
  }

  // =========================
  // 3. 判断 JSON 是否包含精确 SKU
  // =========================

  const skuFieldNames = new Set([
    "sellersku",
    "seller_sku",
    "seller-sku",
    "sku"
  ]);

  function findExactSku(value, target) {
    const normalizedTarget =
      String(target).trim().toUpperCase();

    if (!value || typeof value !== "object") {
      return false;
    }

    if (Array.isArray(value)) {
      return value.some(item =>
        findExactSku(item, target)
      );
    }

    // 优先检查看起来就是 SKU 的字段
    for (const [key, val] of Object.entries(value)) {
      const normalizedKey = key.toLowerCase();

      if (
        skuFieldNames.has(normalizedKey) &&
        typeof val === "string" &&
        val.trim().toUpperCase() === normalizedTarget
      ) {
        return true;
      }
    }

    // 再递归检查子对象
    return Object.values(value).some(val => {
      if (val && typeof val === "object") {
        return findExactSku(val, target);
      }

      return false;
    });
  }

  // =========================
  // 4. 请求 ArkSwift
  // =========================

  async function requestPage(sku, pageNum) {
    const params = new URLSearchParams({
      _storeId: CONFIG.storeId,
      _lang: CONFIG.lang,
      pageNum: String(pageNum),
      pageSize: String(CONFIG.pageSize),
      sellerSku: sku,
      status: CONFIG.status,
      freezeStatus: CONFIG.freezeStatus
    });

    const response = await fetch(
      `/rest/v1/seller/goods/list?${params.toString()}`,
      {
        method: "GET",
        credentials: "include",
        signal: AbortSignal.timeout(30000)
      }
    );

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const json = await response.json();

    if (json?.code !== 200) {
      throw new Error(
        `API ${json?.code ?? "UNKNOWN"}: ${json?.msg ?? ""}`
      );
    }

    return json;
  }

  // =========================
  // 5. 单个 SKU 检查
  // =========================

  async function checkSkuOnce(sku) {
    let pageNum = 1;

    while (true) {
      const json = await requestPage(sku, pageNum);

      const data = json?.data;
      if (!data || !Array.isArray(data.list) || !Number.isInteger(Number(data.pages))
          || Number(data.pages) < 0 || !Number.isFinite(Number(data.total)) || Number(data.total) < 0
          || (Number(data.total) > 0 && Number(data.pages) < 1)) {
        throw new Error("API 商品列表结构异常；无法判断 SKU 是否存在");
      }

      const list = Array.isArray(data.list)
        ? data.list
        : [];

      const exactMatch = list.some(product =>
        findExactSku(product, sku)
      );

      if (exactMatch) {
        return {
          sku,
          exists: "是",
          total: Number(data.total || 0),
          error: ""
        };
      }

      const pages = Number(data.pages || 0);

      if (pages === 0 || pageNum >= pages) {
        return {
          sku,
          exists: "否",
          total: Number(data.total || 0),
          error: ""
        };
      }

      pageNum++;
    }
  }

  // =========================
  // 6. 自动重试
  // =========================

  async function checkSku(sku) {
    let lastError;

    for (
      let attempt = 1;
      attempt <= CONFIG.maxRetries;
      attempt++
    ) {
      try {
        return await checkSkuOnce(sku);
      } catch (error) {
        lastError = error;

        console.warn(
          `${sku} 第 ${attempt}/${CONFIG.maxRetries} 次请求失败：`,
          error.message
        );

        await sleep(attempt * 1000);
      }
    }

    return {
      sku,
      exists: "ERROR",
      total: "",
      error: lastError?.message || "未知错误"
    };
  }

  // =========================
  // 7. 文件输出
  // =========================

  async function writeFileToDirectory(
    dirHandle,
    filename,
    content
  ) {
    const fileHandle =
      await dirHandle.getFileHandle(filename, {
        create: true
      });

    const writable =
      await fileHandle.createWritable();

    await writable.write(content);
    await writable.close();
  }

  function showSaveButton(onClick) {
    const old = document.getElementById(
      "__sku_detect_save_button__"
    );

    if (old) {
      old.remove();
    }

    const button = document.createElement("button");

    button.id = "__sku_detect_save_button__";
    button.textContent = "保存 SKU Detect 结果";

    Object.assign(button.style, {
      position: "fixed",
      top: "20px",
      right: "20px",
      zIndex: "2147483647",
      padding: "12px 18px",
      border: "0",
      borderRadius: "8px",
      cursor: "pointer",
      fontSize: "14px",
      fontWeight: "600",
      boxShadow: "0 4px 16px rgba(0,0,0,.25)"
    });

    button.onclick = onClick;

    document.body.appendChild(button);
  }

  // =========================
  // 8. 主程序
  // =========================

  console.log(
    `当前市场 ${selectedConfig.market.toUpperCase()}，店铺 ${CONFIG.storeId}。请选择 workspace/${selectedConfig.market}/00_input/skus.txt。`
  );

  const {
    country,
    file
  } = await chooseCountryAndFile();

  const text = await file.text();

  const skus = parseSkus(text);

  if (!skus.length) {
    console.error("文件中没有找到 SKU。");
    return;
  }

  console.log(
    `读取完成：${file.name}`
  );

  console.log(
    `共 ${skus.length} 个唯一 SKU，开始检查……`
  );

  const results = [];

  const startedAt = Date.now();

  for (let i = 0; i < skus.length; i++) {
    const sku = skus[i];

    const result = await checkSku(sku);

    results.push(result);

    const done = i + 1;

    const elapsed =
      (Date.now() - startedAt) / 1000;

    const avg = elapsed / done;

    const remaining =
      avg * (skus.length - done);

    console.log(
      `[${done}/${skus.length}]`,
      sku,
      "→",
      result.exists,
      `| total=${result.total}`,
      `| ETA≈${Math.ceil(remaining / 60)}分钟`
    );

    await sleep(CONFIG.delayMs);
  }

  // =========================
  // 9. 统计
  // =========================

  const exists = results.filter(
    x => x.exists === "是"
  );

  const needCreate = results.filter(
    x => x.exists === "否"
  );

  const errors = results.filter(
    x => x.exists === "ERROR"
  );

  console.table(results);

  console.log("============== 完成 ==============");

  console.log(`总数：${results.length}`);
  console.log(`已存在：${exists.length}`);
  console.log(`需要创建：${needCreate.length}`);
  console.log(`错误：${errors.length}`);

  // =========================
  // 10. 保存三个清单和最后写入的完整性记录
  // =========================

  const needCreateText = needCreate
    .map(x => x.sku)
    .join("\r\n");

  const originalText = skus.join("\r\n");
  const errorsText = errors.map(x => x.sku).join("\r\n");
  if (errors.length) {
    const warning = document.createElement("div");
    warning.textContent = `⚠ 检测存在 ${errors.length} 个 ERROR。保存结果后重新检测原始完整清单；不要继续执行 RevFlow。`;
    Object.assign(warning.style, {
      position: "fixed", top: "80px", right: "20px", zIndex: "2147483647",
      maxWidth: "480px", background: "#fff1f0", color: "#a00", padding: "14px", border: "2px solid #a00"
    });
    document.body.appendChild(warning);
    console.error(warning.textContent);
  }

  async function hashText(content) {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
    return Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, "0")).join("");
  }

  showSaveButton(async () => {
    try {
      if (!window.showDirectoryPicker) {
        throw new Error(
          "当前浏览器不支持目录直写，请使用新版 Chrome / Edge。"
        );
      }

      const dirHandle =
        await window.showDirectoryPicker({
          id: "sku-detect-output",
          mode: "readwrite"
        });

      // Invalidate first, so an interrupted save cannot reuse an older success record.
      await writeFileToDirectory(dirHandle, `${country}-detect.json`, JSON.stringify({complete: false}));
      const outputs = {
        [`${country}-all.txt`]: originalText,
        [`${country}-need_create.txt`]: needCreateText,
        [`${country}-errors.txt`]: errorsText
      };
      const hashes = {};
      for (const [name, content] of Object.entries(outputs)) {
        await writeFileToDirectory(dirHandle, name, content);
        hashes[name] = await hashText(content);
      }
      await writeFileToDirectory(dirHandle, `${country}-detect.json`, JSON.stringify({
        market: country, store_id: CONFIG.storeId, complete: true,
        checked_at: new Date().toISOString(), count: skus.length,
        errors: errors.length, files: hashes
      }, null, 2));

      const button = document.getElementById(
        "__sku_detect_save_button__"
      );

      if (button) {
        button.textContent = "已保存 ✓";
        setTimeout(() => button.remove(), 1500);
      }

      console.log(
        `结果已写入所选目录：${country}-all.txt / ${country}-need_create.txt / ${country}-errors.txt / ${country}-detect.json`
      );
    } catch (error) {
      if (error?.name === "AbortError") {
        console.warn("已取消选择输出目录。");
        return;
      }

      console.error(
        "保存结果失败：",
        error
      );
    }
  });

  console.log(
    `扫描完成。请点击页面右上角“保存 SKU Detect 结果”，选择 workspace/${country}/01_detect。`
  );
})().catch(error => {
  console.error("[SKU Detect]", error);
  alert(`SKU Detect 未完成：${error.message}。不要继续执行 RevFlow。`);
});
