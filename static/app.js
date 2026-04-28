// CPACodexKeeper UI — vanilla JS, no build step.

const POLL_INTERVAL_MS = 5000;
const TOKEN_KEY = "cpa-keeper-ui-token";

const STAT_LABELS = [
  ["total", "总计", null],
  ["alive", "存活", "ok"],
  ["dead", "死号", "bad"],
  ["disabled", "已禁用", null],
  ["enabled", "已启用", "ok"],
  ["refreshed", "已刷新", "ok"],
  ["skipped", "跳过", null],
  ["network_error", "网络错误", "bad"],
];

const FIELD_LABELS = {
  cpa_endpoint: "CPA 接口地址",
  cpa_token: "CPA Token",
  proxy: "代理 URL",
  interval_seconds: "巡检间隔 (秒)",
  quota_threshold: "配额阈值 (%)",
  expiry_threshold_days: "刷新阈值 (天)",
  usage_timeout_seconds: "OpenAI 超时 (秒)",
  cpa_timeout_seconds: "CPA 超时 (秒)",
  max_retries: "最大重试",
  worker_threads: "并发线程数",
  enable_refresh: "启用刷新",
  ui_host: "UI 监听地址",
  ui_port: "UI 监听端口",
  ui_token: "UI 访问 Token",
};

const state = {
  reports: [],
  expanded: new Set(),
  tokenQuery: "",
  statusFilter: "all",
  configValues: {},
  configSources: {},
  configDirty: {},
  policyFields: [],
  transportFields: [],
  secretFields: new Set(),
  overridesPath: "",
  scanning: false,
  writeAuthConfigured: false,
};

let toastTimer = null;
let toastHideTimer = null;

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch(path, options = {}) {
  const opts = { ...options };
  opts.headers = { ...(opts.headers || {}), ...authHeaders() };
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    promptForToken();
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function promptForToken() {
  const current = localStorage.getItem(TOKEN_KEY) || "";
  const next = prompt("UI 访问 Token (留空清除)", current);
  if (next === null) return;
  if (next.trim()) localStorage.setItem(TOKEN_KEY, next.trim());
  else localStorage.removeItem(TOKEN_KEY);
  refresh();
}

function toast(message, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  el.hidden = false;
  requestAnimationFrame(() => el.classList.add("show"));
  clearTimeout(toastTimer);
  clearTimeout(toastHideTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    toastHideTimer = setTimeout(() => { el.hidden = true; }, 280);
  }, 3000);
}

function fmtPct(value) {
  if (value === null || value === undefined) return "—";
  return `${value}%`;
}

function fmtTimestamp(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleString();
}

function statusPill(report) {
  if (report.last_outcome === "dead") return `<span class="pill bad">已删除</span>`;
  if (report.disabled) return `<span class="pill warn">已禁用</span>`;
  if (report.last_outcome === "refreshed") return `<span class="pill ok">已刷新</span>`;
  if (report.last_outcome === "enabled") return `<span class="pill ok">已启用</span>`;
  if (report.last_outcome === "alive") return `<span class="pill ok">正常</span>`;
  if (report.last_outcome === "network_error") return `<span class="pill bad">网络错误</span>`;
  if (report.last_outcome === "skipped") return `<span class="pill muted">跳过</span>`;
  return `<span class="pill muted">未知</span>`;
}

function reportStatusKey(report) {
  if (report.last_outcome === "dead") return "dead";
  if (report.disabled) return "disabled";
  if (report.last_outcome === "network_error") return "network_error";
  if (report.last_outcome === "skipped") return "skipped";
  if (report.last_outcome === "refreshed") return "refreshed";
  if (report.last_outcome === "enabled") return "alive";
  if (report.last_outcome === "alive") return "alive";
  return "unknown";
}

function lastActionPreview(report) {
  if (!report.last_actions || report.last_actions.length === 0) return "—";
  const decisive = report.last_actions.filter((a) =>
    /^(DELETE|DISABLE|ENABLE|REFRESH|WARN|ERROR|MANUAL)/.test(a)
  );
  const pick = decisive.length > 0 ? decisive[decisive.length - 1] : report.last_actions[report.last_actions.length - 1];
  return escapeHtml(pick);
}

function matchesTokenFilters(report) {
  const status = state.statusFilter;
  if (status !== "all" && reportStatusKey(report) !== status) return false;
  const query = state.tokenQuery.trim().toLowerCase();
  if (!query) return true;
  const haystack = [
    report.name,
    report.email,
    report.plan_type,
    report.expiry_remaining_human,
    ...(report.last_actions || []),
  ].join(" ").toLowerCase();
  return haystack.includes(query);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function renderStats(stats) {
  const el = document.getElementById("stats");
  el.innerHTML = STAT_LABELS.map(([key, label, kind]) => `
    <div class="stat-card ${kind || ""}">
      <div class="label">${label}</div>
      <div class="value">${stats[key] ?? 0}</div>
    </div>
  `).join("");
}

function renderTokens(reports) {
  const tbody = document.getElementById("token-rows");
  if (!reports || reports.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">暂无数据，触发一次巡检后回来看看</td></tr>`;
    return;
  }
  const filtered = [...reports].filter(matchesTokenFilters)
    .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">没有匹配的 token</td></tr>`;
    return;
  }
  const rows = [];
  const actionDisabled = state.writeAuthConfigured ? "" : "disabled title=\"需先设置 CPA_UI_TOKEN\"";
  for (const report of filtered) {
    const expanded = state.expanded.has(report.name);
    const arrow = expanded ? "▾" : "▸";
    rows.push(`
      <tr class="row" data-name="${escapeHtml(report.name)}">
        <td><span class="expand-icon">${arrow}</span></td>
        <td><code>${escapeHtml(report.name)}</code></td>
        <td>${escapeHtml(report.email || "—")}</td>
        <td>${statusPill(report)}</td>
        <td>${escapeHtml(report.plan_type || "—")}</td>
        <td class="num">${fmtPct(report.primary_used_percent)}</td>
        <td class="num">${fmtPct(report.secondary_used_percent)}</td>
        <td>${escapeHtml(report.expiry_remaining_human || "—")}</td>
        <td>${lastActionPreview(report)}</td>
        <td class="actions-col">
          <div class="actions-cell">
            <button class="btn btn-outline btn-sm" data-act="rescan" data-name="${escapeHtml(report.name)}" ${actionDisabled}>重扫</button>
            <button class="btn btn-outline btn-sm" data-act="toggle" data-name="${escapeHtml(report.name)}" data-disabled="${report.disabled ? "1" : "0"}" ${actionDisabled}>${report.disabled ? "启用" : "禁用"}</button>
            <button class="btn btn-outline btn-sm" data-act="refresh" data-name="${escapeHtml(report.name)}" ${actionDisabled}>刷新</button>
            <button class="btn btn-danger btn-sm" data-act="delete" data-name="${escapeHtml(report.name)}" ${actionDisabled}>删除</button>
          </div>
        </td>
      </tr>
    `);
    if (expanded) {
      const lines = (report.last_log_lines || []).join("\n") || "(无日志)";
      rows.push(`
        <tr class="detail-row" data-name="${escapeHtml(report.name)}">
          <td></td>
          <td colspan="9" class="detail">
            <div class="detail-meta">
              checked at ${fmtTimestamp(report.checked_at)} · expiry: ${escapeHtml(report.expiry || "—")}
            </div>
            <pre>${escapeHtml(lines)}</pre>
          </td>
        </tr>
      `);
    }
  }
  tbody.innerHTML = rows.join("");
}

function renderConfig(values, sources, policy, transport, secrets, overridesPath) {
  document.getElementById("overrides-path").textContent = overridesPath || "(unknown)";
  const form = document.getElementById("config-form");
  const sections = [
    { title: "Policy (热更新)", fields: policy },
    { title: "Transport (需重启)", fields: transport },
  ];
  const html = sections.map((sec) => `
    <div class="config-section">
      <h3>${sec.title}</h3>
      ${sec.fields.map((f) => renderField(f, values[f], sources[f], secrets.has(f), sec.title.includes("重启"))).join("")}
    </div>
  `).join("");
  form.innerHTML = html;
  form.querySelectorAll("[data-field]").forEach((input) => {
    input.addEventListener("change", () => onFieldChange(input));
    input.addEventListener("blur", () => commitField(input));
    if (input.tagName === "INPUT" && input.type !== "checkbox") {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); commitField(input); }
      });
    }
  });
}

function renderField(field, value, source, isSecret, isTransport) {
  const label = FIELD_LABELS[field] || field;
  const sourceTag = source ? `<span class="source">${source}</span>` : "";
  const transportHint = isTransport && source === "override" ? `<span class="hint">需重启容器后生效</span>` : "";
  if (typeof value === "boolean") {
    return `
      <div class="field">
        <label>${escapeHtml(label)}${sourceTag}</label>
        <select data-field="${field}" ${state.writeAuthConfigured ? "" : "disabled"}>
          <option value="true" ${value ? "selected" : ""}>true</option>
          <option value="false" ${!value ? "selected" : ""}>false</option>
        </select>
        ${transportHint}
      </div>
    `;
  }
  const display = isSecret && value ? "" : (value === null || value === undefined ? "" : String(value));
  const placeholder = isSecret ? (value ? "(已设置)" : "(空)") : "";
  const inputType = isSecret ? "password" : "text";
  return `
    <div class="field">
      <label>${escapeHtml(label)}${sourceTag}</label>
      <input data-field="${field}" type="${inputType}" value="${escapeHtml(display)}" placeholder="${escapeHtml(placeholder)}" autocomplete="off" ${state.writeAuthConfigured ? "" : "disabled"} />
      ${transportHint}
    </div>
  `;
}

function onFieldChange(input) {
  const field = input.dataset.field;
  state.configDirty[field] = true;
  input.closest(".field")?.classList.add("dirty");
}

async function commitField(input) {
  const field = input.dataset.field;
  if (!state.writeAuthConfigured) {
    toast("需先设置 CPA_UI_TOKEN 才能修改配置", "bad");
    return;
  }
  if (!state.configDirty[field]) return;
  let raw = input.value;
  if (input.tagName === "SELECT") {
    raw = input.value === "true";
  }
  if (state.secretFields.has(field) && raw === "") {
    delete state.configDirty[field];
    input.closest(".field")?.classList.remove("dirty");
    return;
  }
  try {
    const res = await apiFetch("/api/config", { method: "PUT", body: { [field]: raw } });
    state.configValues[field] = res.values[field];
    delete state.configDirty[field];
    input.closest(".field")?.classList.remove("dirty");
    if ((res.restart_required_fields || []).length > 0) {
      toast(`已保存 ${field}（重启容器后生效）`, "ok");
    } else {
      toast(`已保存 ${field}`, "ok");
    }
    refresh();
  } catch (err) {
    toast(`保存失败: ${err.message}`, "bad");
  }
}

async function refresh() {
  try {
    const data = await apiFetch("/api/state");
    renderStats(data.stats);
    state.reports = data.reports || [];
    state.policyFields = data.policy_fields || [];
    state.transportFields = data.transport_fields || [];
    state.secretFields = new Set(data.secret_fields || []);
    state.configValues = data.settings || {};
    state.configSources = data.field_sources || {};
    state.overridesPath = data.overrides_path || "";
    state.scanning = data.scan_in_progress;
    state.writeAuthConfigured = Boolean(data.write_auth_configured);
    renderTokens(state.reports);
    document.getElementById("scan-all").disabled = data.scan_in_progress || !state.writeAuthConfigured;
    document.getElementById("scan-all").textContent = data.scan_in_progress ? "巡检中…" : (state.writeAuthConfigured ? "立即巡检" : "只读模式");
    document.getElementById("dry-run-tag").hidden = !data.dry_run;

    let metaText;
    if (data.scan_in_progress) {
      metaText = `巡检中…（开始于 ${fmtTimestamp(data.last_run_started_at)}）`;
    } else if (data.last_run_finished_at) {
      const ago = Math.max(0, Math.round(data.now - data.last_run_finished_at));
      metaText = `上次完成于 ${fmtTimestamp(data.last_run_finished_at)}（${ago} 秒前）`;
    } else {
      metaText = "从未巡检";
    }
    document.getElementById("last-run-meta").textContent = metaText;

    if (!document.getElementById("config-drawer").hidden) {
      renderConfig(state.configValues, state.configSources, state.policyFields, state.transportFields, state.secretFields, state.overridesPath);
    }
  } catch (err) {
    toast(`刷新失败: ${err.message}`, "bad");
  }
}

document.getElementById("scan-all").addEventListener("click", async () => {
  try {
    await apiFetch("/api/scan", { method: "POST" });
    toast("已触发整轮巡检", "ok");
    setTimeout(refresh, 800);
  } catch (err) {
    toast(`触发失败: ${err.message}`, "bad");
  }
});

document.getElementById("set-token").addEventListener("click", promptForToken);

document.getElementById("token-search").addEventListener("input", (e) => {
  state.tokenQuery = e.target.value;
  renderTokens(state.reports);
});

document.getElementById("status-filter").addEventListener("change", (e) => {
  state.statusFilter = e.target.value;
  renderTokens(state.reports);
});

function closeConfigDrawer() {
  document.getElementById("config-drawer").hidden = true;
  document.getElementById("drawer-backdrop").hidden = true;
}

document.getElementById("open-config").addEventListener("click", () => {
  const drawer = document.getElementById("config-drawer");
  document.getElementById("drawer-backdrop").hidden = false;
  drawer.hidden = false;
  renderConfig(state.configValues, state.configSources, state.policyFields, state.transportFields, state.secretFields, state.overridesPath);
});

document.getElementById("close-config").addEventListener("click", closeConfigDrawer);
document.getElementById("drawer-backdrop").addEventListener("click", closeConfigDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.getElementById("config-drawer").hidden) {
    closeConfigDrawer();
  }
});

document.getElementById("token-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (btn) {
    e.stopPropagation();
    const name = btn.dataset.name;
    const act = btn.dataset.act;
    try {
      if (act === "rescan") {
        await apiFetch(`/api/scan/${encodeURIComponent(name)}`, { method: "POST" });
        toast(`${name} 重扫完成`, "ok");
      } else if (act === "toggle") {
        const disabled = btn.dataset.disabled === "1";
        await apiFetch(`/api/tokens/${encodeURIComponent(name)}`, { method: "PATCH", body: { disabled: !disabled } });
        toast(`${name} 已${disabled ? "启用" : "禁用"}`, "ok");
      } else if (act === "refresh") {
        await apiFetch(`/api/tokens/${encodeURIComponent(name)}/refresh`, { method: "POST" });
        toast(`${name} 已刷新`, "ok");
      } else if (act === "delete") {
        if (!confirm(`确认删除 ${name}？`)) return;
        await apiFetch(`/api/tokens/${encodeURIComponent(name)}`, { method: "DELETE" });
        toast(`${name} 已删除`, "ok");
      }
      refresh();
    } catch (err) {
      toast(`${act} 失败: ${err.message}`, "bad");
    }
    return;
  }
  const row = e.target.closest("tr.row");
  if (!row) return;
  const name = row.dataset.name;
  if (state.expanded.has(name)) state.expanded.delete(name);
  else state.expanded.add(name);
  renderTokens(state.reports);
});

refresh();
setInterval(refresh, POLL_INTERVAL_MS);
