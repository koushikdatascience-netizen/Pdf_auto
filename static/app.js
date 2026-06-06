const $ = (selector) => document.querySelector(selector);
const state = { file: null, resolutionId: null, approvals: [], busy: false, issueCount: 0, mappedCount: 0 };

const els = {
  company: $("#companyCode"), year: $("#yearCode"),
  strict: $("#strictTotal"), file: $("#pdfInput"), fileLabel: $("#fileLabel"),
  preview: $("#previewButton"), status: $("#status"), workspace: $("#workspace"),
  title: $("#workspaceTitle"), health: $("#healthBadge"), modal: $("#confirmModal"),
  confirmText: $("#confirmText"), confirmInsert: $("#confirmInsert"), toast: $("#toast"),
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  })[char]);
}

function setBusy(busy, message) {
  state.busy = busy;
  els.preview.disabled = busy || !state.file;
  document.body.classList.toggle("busy", busy);
  if (message) setStatus(message, busy ? "working" : "neutral");
}

function setStatus(message, kind = "neutral") {
  els.status.className = `status ${kind}`;
  els.status.textContent = message;
}

function setStep(step) {
  const order = ["upload", "resolve", "review", "complete"];
  const current = order.indexOf(step);
  document.querySelectorAll(".workflow-step").forEach((element) => {
    const index = order.indexOf(element.dataset.step);
    element.classList.toggle("active", index === current);
    element.classList.toggle("done", index < current);
  });
}

function showToast(message, kind = "ok") {
  els.toast.className = `toast ${kind}`;
  els.toast.textContent = message;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => els.toast.classList.add("hidden"), 3200);
}

function apiHeaders(json = false) {
  return json ? { "Content-Type": "application/json" } : {};
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...apiHeaders(options.json), ...(options.headers || {}) } });
  let payload;
  try { payload = await response.json(); } catch { payload = { detail: `HTTP ${response.status}` }; }
  if (!response.ok) {
    const error = new Error(typeof payload.detail === "string" ? payload.detail : payload.detail?.message || `HTTP ${response.status}`);
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function checkHealth() {
  try {
    const result = await api("/api/v1/health");
    els.health.className = "health ok";
    els.health.textContent = `${result.database} · v${result.version}`;
    setStatus("Local agent and ERP database are connected.", "ok");
  } catch (error) {
    els.health.className = "health danger";
    els.health.textContent = "Agent unavailable";
    setStatus(error.message, "danger");
  }
}

function selectFile(file) {
  state.file = file || null;
  els.fileLabel.textContent = file ? file.name : "Choose or drop a PDF";
  els.preview.disabled = !file || state.busy;
  if (file) {
    setStep("upload");
    setStatus(`${file.name} is ready for preview.`, "ok");
  }
}

async function previewPdf() {
  if (!state.file) return;
  const form = new FormData();
  form.append("companycode", els.company.value.trim());
  form.append("yearcode", els.year.value.trim());
  form.append("strict_total", String(els.strict.checked));
  form.append("pdf", state.file);
  setBusy(true, "Extracting PDF and validating ERP masters...");
  try {
    const result = await api("/api/v1/purchases/from-pdf/preview", { method: "POST", body: form });
    renderReady(result);
  } catch (error) {
    handleWorkflowError(error);
  } finally {
    setBusy(false);
  }
}

function handleWorkflowError(error) {
  const payload = error.payload || {};
  if (payload.resolution_required) return renderResolution(payload);
  if (payload.duplicate) return renderDuplicate(payload);
  renderError(error.message, payload);
}

function renderError(message, payload = {}) {
  setStep("upload");
  els.title.textContent = "Preview could not be prepared";
  els.workspace.className = "message-state danger-panel";
  els.workspace.innerHTML = `<h3>${escapeHtml(message)}</h3><p>Nothing was inserted. Correct the PDF or configuration, then try again.</p>
    <details><summary>Technical details</summary><pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre></details>`;
  setStatus(message, "danger");
}

function renderDuplicate(payload) {
  setStep("complete");
  const rows = payload.duplicates || (payload.existing_result ? [payload.existing_result] : []);
  els.title.textContent = "Existing purchase found";
  els.workspace.className = "message-state warning-panel";
  els.workspace.innerHTML = `<h3>Duplicate insert blocked</h3>
    <p>${escapeHtml(payload.action || "Open the existing ERP purchase. No insert is allowed.")}</p>
    ${rows.map((row) => `<dl class="summary-grid">
      <div><dt>Document</dt><dd>${escapeHtml(row.docno || "Exact uploaded PDF")}</dd></div>
      <div><dt>Transaction ID</dt><dd>${escapeHtml(row.trnid || row.result?.trnid || "-")}</dd></div>
      <div><dt>Transaction No.</dt><dd>${escapeHtml(row.trnno || row.result?.trnno || "-")}</dd></div>
      <div><dt>Date</dt><dd>${escapeHtml(row.trndate || "-")}</dd></div>
    </dl>`).join("")}`;
  setStatus("Duplicate detected. Database insert is disabled.", "warning");
}

function suggestionRow(issue, suggestion) {
  const name = suggestion.item_name || suggestion.supplier_name;
  const code = suggestion.itemcode || suggestion.suppliercode;
  const details = suggestion.itemcode
    ? `${suggestion.ml ?? "-"} ml · pack ${suggestion.packing ?? "-"} · ${suggestion.strength_name ?? "-"}`
    : "Existing supplier master";
  const confidence = suggestion.confidence ? `<span class="confidence ${escapeHtml(suggestion.confidence)}">${escapeHtml(suggestion.confidence)} ${escapeHtml(suggestion.match_score)}%</span>` : "";
  return `<tr>
    <td><strong>${escapeHtml(name)}</strong><small>${escapeHtml(details)}</small></td>
    <td><code>${escapeHtml(code)}</code></td>
    <td>${confidence}<small>${escapeHtml((suggestion.match_reasons || []).join("; "))}</small></td>
    <td><button class="compact map-choice" data-type="${escapeHtml(issue.type)}" data-source="${escapeHtml(issue.source)}"
      data-name="${escapeHtml(name)}" data-code="${escapeHtml(code)}" data-ml="${escapeHtml(issue.ml ?? "")}"
      data-batch="${escapeHtml(issue.batch ?? "")}">Map</button></td>
  </tr>`;
}

function issuePanel(issue, index) {
  const suggestions = issue.suggestions || [];
  return `<section class="issue" data-index="${index}">
    <div class="issue-head">
      <div><span class="issue-type">${escapeHtml(issue.type)}</span><h3>${escapeHtml(issue.source)}</h3>
        <p>${escapeHtml(issue.message)}</p></div>
      ${issue.type === "item" ? `<dl class="inline-facts"><div><dt>ML</dt><dd>${escapeHtml(issue.ml ?? "-")}</dd></div>
        <div><dt>Batch</dt><dd>${escapeHtml(issue.batch ?? "-")}</dd></div></dl>` : ""}
    </div>
    <div class="search-line">
      <input class="master-query" value="${escapeHtml(issue.source)}" placeholder="Search existing ERP master">
      <button class="secondary compact search-master" data-type="${escapeHtml(issue.type)}">Search ERP</button>
    </div>
    <div class="table-wrap"><table><thead><tr><th>Suggested ERP master</th><th>Code</th><th>Match</th><th></th></tr></thead>
      <tbody class="suggestion-results">${suggestions.length ? suggestions.map((s) => suggestionRow(issue, s)).join("") :
        `<tr><td colspan="4" class="muted-cell">No close match found. Search ERP, or create this item in the ERP Item Master screen.</td></tr>`}</tbody>
    </table></div>
    ${issue.type === "item" ? `<p class="erp-note">Not present in ERP? Create it in the ERP Item Master screen, then search here and map it. This importer never creates item masters.</p>` : ""}
  </section>`;
}

function renderResolution(payload) {
  setStep("resolve");
  state.resolutionId = payload.resolution_id;
  state.issueCount = payload.unresolved_count;
  state.mappedCount = 0;
  els.title.textContent = `${payload.unresolved_count} master mapping ${payload.unresolved_count === 1 ? "issue" : "issues"}`;
  els.workspace.className = "resolution";
  els.workspace.innerHTML = `<section class="resolution-overview">
    <div><p class="eyebrow">Mapping progress</p><strong id="mappingProgressText">0 of ${payload.unresolved_count} confirmed</strong></div>
    <div class="progress-track"><span id="mappingProgressBar" style="width:0%"></span></div>
    <div class="resolution-stats"><span>${payload.unresolved.filter((issue) => issue.type === "supplier").length} suppliers</span><span>${payload.unresolved.filter((issue) => issue.type === "item").length} items</span></div>
    </section>
    <div class="notice warning-panel"><strong>Operator action required</strong>
    <span>Map each PDF value to an existing ERP master. No purchase has been inserted.</span></div>
    <div class="issues">${payload.unresolved.map(issuePanel).join("")}</div>
    <div class="sticky-actions"><span>After saving mappings, retry this same PDF session.</span>
      <button id="retryResolution" type="button">Retry validation</button></div>`;
  bindResolutionActions(payload.unresolved);
  setStatus("Resolve the highlighted ERP master mappings.", "warning");
}

function bindResolutionActions(issues) {
  document.querySelectorAll(".search-master").forEach((button) => button.addEventListener("click", async () => {
    const panel = button.closest(".issue");
    const issue = issues[Number(panel.dataset.index)];
    const query = panel.querySelector(".master-query").value.trim();
    if (!query) return;
    button.disabled = true;
    try {
      const path = issue.type === "item"
        ? `/api/v1/masters/items?query=${encodeURIComponent(query)}`
        : `/api/v1/masters/suppliers?companycode=${encodeURIComponent(els.company.value.trim())}&query=${encodeURIComponent(query)}`;
      const result = await api(path);
      panel.querySelector(".suggestion-results").innerHTML = result.results.length
        ? result.results.map((s) => suggestionRow(issue, s)).join("")
        : `<tr><td colspan="4" class="muted-cell">No existing ERP master matched this search.</td></tr>`;
      bindMapButtons();
    } catch (error) { setStatus(error.message, "danger"); }
    finally { button.disabled = false; }
  }));
  bindMapButtons();
  $("#retryResolution").addEventListener("click", retryResolution);
}

function bindMapButtons() {
  document.querySelectorAll(".map-choice").forEach((button) => button.addEventListener("click", async () => {
    const isItem = button.dataset.type === "item";
    const label = isItem ? `${button.dataset.name} (${button.dataset.code})` : button.dataset.name;
    if (!window.confirm(`Map PDF value "${button.dataset.source}" to ERP master "${label}"?`)) return;
    button.disabled = true;
    try {
      const body = isItem
        ? { source_name: button.dataset.source, ml: Number(button.dataset.ml) || null, batch: button.dataset.batch, item_code: button.dataset.code }
        : { companycode: els.company.value.trim(), source_name: button.dataset.source, target_name: button.dataset.name };
      await api(isItem ? "/api/v1/mappings/items" : "/api/v1/mappings/suppliers", {
        method: "POST", json: true, body: JSON.stringify(body),
      });
      button.textContent = "Mapped";
      const panel = button.closest(".issue");
      if (!panel.classList.contains("resolved-local")) state.mappedCount += 1;
      panel.classList.add("resolved-local");
      panel.querySelectorAll(".map-choice").forEach((choice) => { choice.disabled = true; });
      updateMappingProgress();
      showToast(`Mapped to ${label}`);
      setStatus("Mapping verified and saved. Retry validation when all issues are mapped.", "ok");
    } catch (error) {
      button.disabled = false;
      setStatus(error.message, "danger");
    }
  }));
}

function updateMappingProgress() {
  const percent = state.issueCount ? Math.round(state.mappedCount / state.issueCount * 100) : 0;
  const text = $("#mappingProgressText");
  const bar = $("#mappingProgressBar");
  if (text) text.textContent = `${state.mappedCount} of ${state.issueCount} confirmed`;
  if (bar) bar.style.width = `${percent}%`;
}

async function retryResolution() {
  if (!state.resolutionId) return;
  setBusy(true, "Rechecking saved mappings and ERP masters...");
  try {
    const result = await api(`/api/v1/resolutions/${encodeURIComponent(state.resolutionId)}/retry`, { method: "POST" });
    renderReady({ ...result, source_file: state.file?.name });
  } catch (error) { handleWorkflowError(error); }
  finally { setBusy(false); }
}

function money(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : escapeHtml(value);
}

function purchaseView(approval, index) {
  const p = approval.preview;
  const items = p.items || [];
  const taxRows = p.tax_rows || (p.tax ? [p.tax] : []);
  return `<section class="purchase-preview">
    <div class="preview-head"><div><span class="ready-label">Validated</span><h3>Purchase ${index + 1}: ${escapeHtml(p.docno)}</h3></div>
      <strong class="total">₹ ${money(p.totnetamt)}</strong></div>
    <dl class="summary-grid">
      <div><dt>Supplier</dt><dd>${escapeHtml(p.supplier)} <code>${escapeHtml(p.suppliercode)}</code></dd></div>
      <div><dt>Invoice date</dt><dd>${escapeHtml(p.trndate)}</dd></div>
      <div><dt>Company / year</dt><dd>${escapeHtml(p.companycode)} / ${escapeHtml(p.yearcode)}</dd></div>
      <div><dt>Document</dt><dd>${escapeHtml(p.docno)}</dd></div>
    </dl>
    <h4>Items</h4>
    <div class="table-wrap"><table><thead><tr><th>#</th><th>ERP item</th><th>Batch</th><th>Cases / Qty</th><th>Rate</th><th>Amount</th></tr></thead>
      <tbody>${items.map((item, i) => `<tr><td>${i + 1}</td><td><strong>${escapeHtml(item.item_name || item.itemcode)}</strong><small><code>${escapeHtml(item.itemcode)}</code> · ${escapeHtml(item.ml || "-")} ml</small></td>
        <td>${escapeHtml(item.batchno)}</td><td>${escapeHtml(item.itembox ?? item.itemquantity)}</td><td>${money(item.itemboxrate ?? item.itemrate)}</td><td>${money(item.itemamount)}</td></tr>`).join("")}</tbody></table></div>
    ${taxRows.length ? `<h4>Tax and accounts</h4><div class="table-wrap"><table><thead><tr><th>Code / account</th><th>Rate</th><th>On amount</th><th>Tax amount</th></tr></thead>
      <tbody>${taxRows.map((tax) => `<tr><td>${escapeHtml(tax.TaxCode || tax.TaxAccount || "-")}</td><td>${escapeHtml(tax.TaxRate || "-")}</td><td>${money(tax.OnAmount || 0)}</td><td>${money(tax.TaxAmount || 0)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${(p.warnings || []).length ? `<div class="notice warning-panel">${p.warnings.map(escapeHtml).join("<br>")}</div>` : ""}
  </section>`;
}

function renderReady(payload) {
  setStep("review");
  state.approvals = payload.purchases || [];
  state.resolutionId = null;
  els.title.textContent = `${state.approvals.length} validated purchase ${state.approvals.length === 1 ? "preview" : "previews"}`;
  els.workspace.className = "ready-workspace";
  const itemRows = state.approvals.reduce((total, approval) => total + (approval.preview.items || []).length, 0);
  const taxRows = state.approvals.reduce((total, approval) => total + (approval.preview.tax_rows || (approval.preview.tax ? [approval.preview.tax] : [])).length, 0);
  const grandTotal = state.approvals.reduce((total, approval) => total + Number(approval.preview.totnetamt || 0), 0);
  els.workspace.innerHTML = `<section class="change-summary">
    <div><span>Purchase headers</span><strong>${state.approvals.length}</strong><small>purchasemain rows</small></div>
    <div><span>Item details</span><strong>${itemRows}</strong><small>purchasedetail rows</small></div>
    <div><span>Tax details</span><strong>${taxRows}</strong><small>PurchaseTaxDetail rows</small></div>
    <div><span>Net value</span><strong>INR ${money(grandTotal)}</strong><small>validated total</small></div>
    </section>
    <div class="notice ok-panel"><strong>Ready for operator approval</strong>
    <span>All mappings and totals passed validation. Review every row before inserting.</span></div>
    ${state.approvals.map(purchaseView).join("")}
    <div class="sticky-actions"><span>Insert is transactional. Any failure rolls back all rows.</span>
      <button id="openInsert" type="button">Approve and insert</button></div>`;
  $("#openInsert").addEventListener("click", () => {
    els.confirmText.value = "";
    els.confirmInsert.disabled = true;
    $("#confirmSummary").innerHTML = `<strong>${state.approvals.length} purchase${state.approvals.length === 1 ? "" : "s"}</strong>
      <span>${itemRows} item rows | ${taxRows} tax rows | INR ${money(grandTotal)}</span>`;
    els.modal.classList.remove("hidden");
    els.confirmText.focus();
  });
  setStatus("Preview validated. Review it before insertion.", "ok");
}

async function insertPurchases() {
  els.modal.classList.add("hidden");
  setBusy(true, "Inserting approved purchase transaction...");
  try {
    const results = [];
    for (const approval of state.approvals) {
      results.push(await api("/api/v1/purchases/insert", {
        method: "POST", json: true, body: JSON.stringify({ approval_token: approval.approval_token }),
      }));
    }
    setStep("complete");
    els.title.textContent = "Purchase insertion complete";
    els.workspace.className = "completion-workspace";
    els.workspace.innerHTML = `<section class="completion-banner"><span class="completion-check">OK</span><div><p class="eyebrow">Transaction committed</p>
      <h3>ERP purchase saved successfully</h3><p>All displayed rows were inserted together. This receipt contains the ERP references for verification.</p></div></section>
      ${results.map(({ result }) => `<section class="receipt"><div class="receipt-head"><strong>ERP insertion receipt</strong><span>${new Date().toLocaleString()}</span></div>
      <dl class="summary-grid"><div><dt>Transaction ID</dt><dd>${escapeHtml(result.trnid)}</dd></div>
        <div><dt>Transaction No.</dt><dd>${escapeHtml(result.trnno)}</dd></div><div><dt>Detail rows</dt><dd>${escapeHtml(result.detail_rows_inserted)}</dd></div>
        <div><dt>Tax rows</dt><dd>${escapeHtml(result.tax_rows_inserted)}</dd></div></dl>
        <div class="receipt-tables"><span>trnidmst ${result.transaction_master_inserted ? "created" : "verified"}</span><span>purchasemain 1 row</span>
        <span>purchasedetail ${escapeHtml(result.detail_rows_inserted)} rows</span><span>PurchaseTaxDetail ${escapeHtml(result.tax_rows_inserted)} rows</span></div></section>`).join("")}
      <div class="completion-actions"><button id="completeNewPdf" type="button">Import another PDF</button></div>`;
    $("#completeNewPdf").addEventListener("click", reset);
    showToast("Purchase committed to ERP");
    setStatus("Purchase inserted successfully.", "ok");
  } catch (error) { renderError(error.message, error.payload); }
  finally { setBusy(false); }
}

function reset() {
  state.file = null; state.resolutionId = null; state.approvals = [];
  els.file.value = ""; selectFile(null);
  els.title.textContent = "Waiting for invoice";
  setStep("upload");
  els.workspace.className = "empty-state";
  els.workspace.innerHTML = `<div class="empty-symbol">PDF</div><h3>Upload an invoice PDF</h3>
    <p>The agent will extract it, check duplicates, validate ERP masters, and prepare a database-safe preview.</p>`;
}

els.file.addEventListener("change", () => selectFile(els.file.files[0]));
$("#dropzone").addEventListener("dragover", (event) => event.preventDefault());
$("#dropzone").addEventListener("drop", (event) => { event.preventDefault(); selectFile(event.dataTransfer.files[0]); });
els.preview.addEventListener("click", previewPdf);
$("#checkAgent").addEventListener("click", checkHealth);
$("#resetButton").addEventListener("click", reset);
$("#cancelInsert").addEventListener("click", () => els.modal.classList.add("hidden"));
els.confirmText.addEventListener("input", () => { els.confirmInsert.disabled = els.confirmText.value.trim().toUpperCase() !== "INSERT"; });
els.confirmInsert.addEventListener("click", insertPurchases);
checkHealth();
