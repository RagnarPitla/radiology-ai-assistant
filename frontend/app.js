(function () {
  "use strict";

  const state = {
    studies: [],
    selectedStudy: null,
    currentReport: null,
    frameIndex: 0,
    filter: "all",
    sort: "priority",
    chatHistory: [],
    config: null,
    health: null
  };

  const priorityRank = { stat: 0, urgent: 1, routine: 2 };
  const els = {};

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    bindElements();
    bindEvents();
    loadStartup();
  }

  function bindElements() {
    [
      "offline-pill", "runtime-line", "disclaimer-banner", "disclaimer-text",
      "dismiss-disclaimer", "ingest-sample", "dicom-upload", "filter-chips",
      "sort-select", "worklist", "worklist-count", "study-subtitle",
      "selected-badges", "image-stage", "study-meta", "prev-frame",
      "next-frame", "frame-slider", "frame-label", "indication", "technique",
      "comparison", "findings", "impression", "draft-report",
      "generate-impression", "save-report", "run-triage", "triage-result",
      "report-disclaimer", "report-note", "chat-log", "chat-form",
      "chat-input", "chat-disclaimer", "kb-upload", "kb-query", "kb-search",
      "kb-docs", "kb-results", "toast-region"
    ].forEach((id) => {
      els[toKey(id)] = document.getElementById(id);
    });
  }

  function toKey(id) {
    return id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  }

  function bindEvents() {
    els.dismissDisclaimer.addEventListener("click", () => {
      els.disclaimerBanner.classList.add("hidden");
    });
    els.ingestSample.addEventListener("click", () => withBusy(els.ingestSample, ingestSample));
    els.dicomUpload.addEventListener("change", () => uploadFiles(els.dicomUpload, "/api/studies/ingest-upload", refreshStudies));
    els.filterChips.addEventListener("click", onFilterClick);
    els.sortSelect.addEventListener("change", () => {
      state.sort = els.sortSelect.value;
      renderWorklist();
    });
    els.prevFrame.addEventListener("click", () => showFrame(state.frameIndex - 1));
    els.nextFrame.addEventListener("click", () => showFrame(state.frameIndex + 1));
    els.frameSlider.addEventListener("input", () => showFrame(Number(els.frameSlider.value)));
    els.draftReport.addEventListener("click", () => withBusy(els.draftReport, draftReport));
    els.generateImpression.addEventListener("click", () => withBusy(els.generateImpression, generateImpression));
    els.saveReport.addEventListener("click", () => withBusy(els.saveReport, saveReport));
    els.runTriage.addEventListener("click", () => withBusy(els.runTriage, runTriage));
    els.chatForm.addEventListener("submit", sendChat);
    els.kbUpload.addEventListener("change", () => uploadFiles(els.kbUpload, "/api/knowledge/ingest-upload", loadKnowledgeDocs));
    els.kbSearch.addEventListener("click", () => withBusy(els.kbSearch, searchKnowledge));
    document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", switchTab));
  }

  async function loadStartup() {
    await Promise.all([loadHealth(), loadConfig()]);
    await Promise.all([refreshStudies(), loadKnowledgeDocs()]);
  }

  async function api(path, options) {
    const request = options || {};
    const headers = request.headers ? { ...request.headers } : {};
    if (request.body && !(request.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    let response;
    try {
      response = await fetch(path, { ...request, headers });
    } catch (error) {
      throw new Error("Local endpoint is not reachable.");
    }
    if (response.status === 404) {
      throw new Error("Not available yet.");
    }
    if (!response.ok) {
      const message = await response.text().catch(() => "");
      throw new Error(message || `Request failed with status ${response.status}.`);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  async function loadHealth() {
    try {
      state.health = await api("/api/health");
      const health = state.health;
      const isLocal = Boolean(health.offline_mode);
      els.offlinePill.className = `pill ${isLocal ? "local" : "warn"}`;
      setText(els.offlinePill, isLocal ? "LOCAL / OFFLINE" : "LOCAL MODE UNKNOWN");
      const modelText = health.model || state.config?.chat_model || "model pending";
      setText(els.runtimeLine, `${health.runtime || "runtime"} | ${modelText} | LLM ${health.llm_available ? "ready" : "not ready"}`);
    } catch (error) {
      els.offlinePill.className = "pill error";
      setText(els.offlinePill, "LOCAL CHECK FAILED");
      setText(els.runtimeLine, error.message);
      toast(error.message);
    }
  }

  async function loadConfig() {
    try {
      state.config = await api("/api/config");
      if (state.config.disclaimer) {
        setText(els.disclaimerText, state.config.disclaimer);
        setText(els.reportDisclaimer, state.config.disclaimer);
        setText(els.chatDisclaimer, state.config.disclaimer);
        els.disclaimerBanner.classList.remove("hidden");
      }
      if (state.health) {
        await loadHealth();
      }
    } catch (error) {
      showInline(els.reportNote, "Configuration is not available yet.");
    }
  }

  async function refreshStudies(reloadSelected) {
    const shouldReloadSelected = reloadSelected !== false;
    try {
      state.studies = await api("/api/studies/");
      if (state.selectedStudy && !shouldReloadSelected) {
        const found = state.studies.find((study) => study.id === state.selectedStudy.id);
        if (found) {
          state.selectedStudy = { ...state.selectedStudy, ...found };
          renderStudy();
        }
      }
      renderWorklist();
      if (state.selectedStudy && shouldReloadSelected) {
        const found = state.studies.find((study) => study.id === state.selectedStudy.id);
        if (found) {
          await selectStudy(found.id, false);
        }
      }
    } catch (error) {
      state.studies = [];
      renderWorklist();
      setText(els.worklist, endpointNote(error));
      els.worklist.classList.add("empty-state");
      toast(endpointNote(error));
    }
  }

  async function ingestSample() {
    try {
      const result = await api("/api/studies/ingest", {
        method: "POST",
        body: JSON.stringify({})
      });
      toast(result.message || "Ingest complete.");
      await refreshStudies();
    } catch (error) {
      toast(endpointNote(error));
    }
  }

  async function uploadFiles(input, endpoint, callback) {
    if (!input.files.length) {
      return;
    }
    const form = new FormData();
    Array.from(input.files).forEach((file) => form.append("files", file));
    input.closest(".upload-drop")?.classList.add("loading");
    try {
      const result = await api(endpoint, { method: "POST", body: form });
      toast(result.message || "Upload complete.");
      await callback();
    } catch (error) {
      toast(endpointNote(error));
    } finally {
      input.value = "";
      input.closest(".upload-drop")?.classList.remove("loading");
    }
  }

  function onFilterClick(event) {
    const button = event.target.closest("[data-filter]");
    if (!button) {
      return;
    }
    state.filter = button.dataset.filter;
    els.filterChips.querySelectorAll(".chip").forEach((chip) => chip.classList.toggle("active", chip === button));
    renderWorklist();
  }

  function filteredStudies() {
    return state.studies.filter((study) => {
      if (state.filter === "all") {
        return true;
      }
      if (state.filter === "critical") {
        return Boolean(study.critical);
      }
      if (["stat", "urgent", "routine"].includes(state.filter)) {
        return study.priority === state.filter;
      }
      return study.status === state.filter;
    }).sort((a, b) => {
      if (state.sort === "priority") {
        return (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9) || dateValue(b) - dateValue(a);
      }
      if (state.sort === "date") {
        return dateValue(b) - dateValue(a);
      }
      return String(a[state.sort] || "").localeCompare(String(b[state.sort] || ""));
    });
  }

  function renderWorklist() {
    clear(els.worklist);
    const studies = filteredStudies();
    setText(els.worklistCount, `${studies.length} of ${state.studies.length} studies`);
    if (!studies.length) {
      els.worklist.classList.add("empty-state");
      setText(els.worklist, state.studies.length ? "No studies match the current filter." : "Load sample studies or upload local DICOM files.");
      return;
    }
    els.worklist.classList.remove("empty-state");
    studies.forEach((study) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "study-card";
      if (state.selectedStudy?.id === study.id) {
        button.classList.add("selected");
      }
      button.addEventListener("click", () => selectStudy(study.id, true));
      const name = document.createElement("strong");
      setText(name, study.patient_name || "Anonymous patient");
      const lineOne = div("study-line", [study.patient_id, study.modality, study.body_part].filter(Boolean).join(" | "));
      const lineTwo = div("study-line", [study.description, formatDate(study.study_date), `${study.num_images || 0} images`].filter(Boolean).join(" | "));
      const badges = div("badge-row");
      badges.appendChild(badge(study.priority || "routine", study.priority || "routine"));
      badges.appendChild(badge("status", statusLabel(study.status)));
      if (study.critical) {
        badges.appendChild(badge("critical", "Critical"));
      }
      button.append(name, lineOne, lineTwo, badges);
      els.worklist.appendChild(button);
    });
  }

  async function selectStudy(id, showToast) {
    try {
      const detail = await api(`/api/studies/${encodeURIComponent(id)}`);
      state.selectedStudy = detail;
      state.frameIndex = 0;
      clear(els.triageResult);
      renderStudy();
      await loadReport(id);
      if (showToast) {
        toast("Study loaded.");
      }
    } catch (error) {
      toast(endpointNote(error));
    }
  }

  function renderStudy() {
    const study = state.selectedStudy;
    if (!study) {
      return;
    }
    setText(els.studySubtitle, [study.patient_name, study.modality, study.body_part, formatDate(study.study_date)].filter(Boolean).join(" | "));
    clear(els.selectedBadges);
    els.selectedBadges.appendChild(badge(study.priority || "routine", study.priority || "routine"));
    els.selectedBadges.appendChild(badge("status", statusLabel(study.status)));
    if (study.critical) {
      els.selectedBadges.appendChild(badge("critical", "Critical"));
    }
    renderMeta(study);
    setupFrames(study);
  }

  function renderMeta(study) {
    clear(els.studyMeta);
    const rows = {
      "Patient ID": study.patient_id,
      "Study UID": study.study_uid,
      "Description": study.description,
      "Body part": study.body_part,
      "Modality": study.modality,
      "Frames": study.frame_count,
      "Status": statusLabel(study.status)
    };
    Object.entries(rows).forEach(([key, value]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      setText(dt, key);
      setText(dd, value == null || value === "" ? "Not recorded" : String(value));
      els.studyMeta.append(dt, dd);
    });
  }

  function setupFrames(study) {
    const count = Number(study.frame_count || 0);
    els.frameSlider.max = Math.max(0, count - 1);
    els.frameSlider.disabled = count <= 1;
    els.prevFrame.disabled = count <= 1;
    els.nextFrame.disabled = count <= 1;
    if (!count) {
      clear(els.imageStage);
      els.imageStage.appendChild(div("empty-state", "No renderable images for this study."));
      setText(els.frameLabel, "Frame 0 of 0");
      return;
    }
    showFrame(0);
  }

  function showFrame(index) {
    const study = state.selectedStudy;
    if (!study) {
      return;
    }
    const count = Number(study.frame_count || 0);
    if (!count) {
      return;
    }
    state.frameIndex = Math.min(Math.max(index, 0), count - 1);
    els.frameSlider.value = state.frameIndex;
    els.prevFrame.disabled = state.frameIndex === 0;
    els.nextFrame.disabled = state.frameIndex === count - 1;
    setText(els.frameLabel, `Frame ${state.frameIndex + 1} of ${count}`);
    clear(els.imageStage);
    const img = document.createElement("img");
    img.alt = `DICOM frame ${state.frameIndex + 1}`;
    img.src = `/api/studies/${encodeURIComponent(study.id)}/frame/${state.frameIndex}.png`;
    img.addEventListener("error", () => {
      clear(els.imageStage);
      els.imageStage.appendChild(div("empty-state", "This frame is not available yet."));
    });
    els.imageStage.appendChild(img);
  }

  async function loadReport(studyId) {
    clearReportNote();
    state.currentReport = null;
    els.indication.value = "";
    setReportFields({});
    try {
      const report = await api(`/api/reports/${encodeURIComponent(studyId)}`);
      state.currentReport = report;
      setReportFields(report);
      showAiDisclaimer(report.disclaimer);
    } catch (error) {
      if (error.message !== "Not available yet.") {
        showInline(els.reportNote, "No saved report for this study yet.");
      } else {
        showInline(els.reportNote, "Report service is not available yet.");
      }
    }
  }

  function setReportFields(report) {
    els.technique.value = report.technique || "";
    els.comparison.value = report.comparison || "";
    els.findings.value = report.findings || "";
    els.impression.value = report.impression || "";
  }

  async function draftReport() {
    const study = requireStudy();
    if (!study) {
      return;
    }
    clearReportNote();
    const report = await api("/api/reports/draft", {
      method: "POST",
      body: JSON.stringify({
        study_id: study.id,
        modality: study.modality || "",
        body_part: study.body_part || "",
        indication: els.indication.value,
        findings: els.findings.value,
        comparison: els.comparison.value,
        technique: els.technique.value,
        style: "concise clinical radiology"
      })
    });
    state.currentReport = report;
    setReportFields(report);
    showAiDisclaimer(report.disclaimer);
    showInline(els.reportNote, "Draft report generated locally. Review before clinical use.");
  }

  async function generateImpression() {
    const study = requireStudy();
    if (!study) {
      return;
    }
    clearReportNote();
    const result = await api("/api/reports/impression", {
      method: "POST",
      body: JSON.stringify({
        findings: els.findings.value,
        indication: els.indication.value,
        modality: study.modality || ""
      })
    });
    els.impression.value = result.impression || "";
    showAiDisclaimer(result.disclaimer);
    showInline(els.reportNote, "Impression generated locally. Review before saving.");
  }

  async function saveReport() {
    const study = requireStudy();
    if (!study) {
      return;
    }
    clearReportNote();
    const report = {
      ...(state.currentReport || {}),
      study_id: study.id,
      technique: els.technique.value,
      comparison: els.comparison.value,
      findings: els.findings.value,
      impression: els.impression.value,
      status: "draft",
      model: state.currentReport?.model || state.config?.chat_model || "",
      disclaimer: state.currentReport?.disclaimer || state.config?.disclaimer || ""
    };
    const saved = await api("/api/reports/save", {
      method: "POST",
      body: JSON.stringify(report)
    });
    state.currentReport = saved;
    showAiDisclaimer(saved.disclaimer);
    showInline(els.reportNote, "Report saved.");
  }

  async function runTriage() {
    const study = requireStudy();
    if (!study) {
      return;
    }
    const text = `${els.findings.value}\n${els.impression.value}`.trim();
    if (!text) {
      toast("Add findings or impression text before triage.");
      return;
    }
    const result = await api("/api/triage/analyze", {
      method: "POST",
      body: JSON.stringify({ text, study_id: study.id, modality: study.modality || "" })
    });
    renderTriage(result);
    showAiDisclaimer(result.disclaimer);
    await refreshStudies(false);
  }

  function renderTriage(result) {
    clear(els.triageResult);
    els.triageResult.appendChild(badge(result.level || "routine", result.level || "routine"));
    if (result.critical) {
      els.triageResult.appendChild(badge("critical", "Critical"));
    }
    const rationale = document.createElement("div");
    setText(rationale, result.rationale || "No rationale returned.");
    els.triageResult.appendChild(rationale);
  }

  async function sendChat(event) {
    event.preventDefault();
    const message = els.chatInput.value.trim();
    if (!message) {
      return;
    }
    els.chatInput.value = "";
    appendChat("user", message);
    state.chatHistory.push({ role: "user", content: message });
    els.chatForm.classList.add("loading");
    try {
      const result = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          message,
          history: state.chatHistory.slice(0, -1),
          study_id: state.selectedStudy?.id,
          use_tools: true
        })
      });
      appendChat("assistant", result.reply || "", result.tool_calls || []);
      state.chatHistory.push({ role: "assistant", content: result.reply || "" });
      if (result.disclaimer || state.config?.disclaimer) {
        setText(els.chatDisclaimer, result.disclaimer || state.config.disclaimer);
        els.chatDisclaimer.classList.remove("hidden");
      }
    } catch (error) {
      appendChat("assistant", endpointNote(error));
      toast(endpointNote(error));
    } finally {
      els.chatForm.classList.remove("loading");
    }
  }

  function appendChat(role, content, tools) {
    if (els.chatLog.classList.contains("empty-state")) {
      clear(els.chatLog);
      els.chatLog.classList.remove("empty-state");
    }
    const message = div(`message ${role}`);
    const roleLabel = document.createElement("span");
    roleLabel.className = "role";
    setText(roleLabel, role);
    const body = document.createElement("span");
    setText(body, content);
    message.append(roleLabel, body);
    if (tools && tools.length) {
      const toolLine = div("tools-line");
      setText(toolLine, `Tools used: ${tools.map((tool) => `${tool.name}: ${tool.result_summary || "done"}`).join(" | ")}`);
      message.appendChild(toolLine);
    }
    els.chatLog.appendChild(message);
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  function switchTab(event) {
    const target = event.currentTarget.dataset.tab;
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === target));
    document.getElementById("assistant-tab").classList.toggle("hidden", target !== "assistant");
    document.getElementById("knowledge-tab").classList.toggle("hidden", target !== "knowledge");
  }

  async function loadKnowledgeDocs() {
    try {
      const docs = await api("/api/knowledge/docs");
      renderDocs(docs);
    } catch (error) {
      clear(els.kbDocs);
      els.kbDocs.classList.add("empty-state");
      setText(els.kbDocs, "Knowledge service is not available yet.");
    }
  }

  function renderDocs(docs) {
    clear(els.kbDocs);
    if (!docs.length) {
      els.kbDocs.classList.add("empty-state");
      setText(els.kbDocs, "No knowledge documents loaded.");
      return;
    }
    els.kbDocs.classList.remove("empty-state");
    docs.forEach((doc) => {
      const item = div("doc-item");
      const info = document.createElement("div");
      const title = document.createElement("strong");
      setText(title, doc.title || doc.filename || "Untitled document");
      const meta = div("study-line", `${doc.num_chunks || 0} chunks | ${formatDate(doc.created_at)}`);
      info.append(title, meta);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "small ghost";
      setText(del, "Delete");
      del.addEventListener("click", () => deleteDoc(doc.id));
      item.append(info, del);
      els.kbDocs.appendChild(item);
    });
  }

  async function deleteDoc(id) {
    try {
      await api(`/api/knowledge/docs/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("Knowledge document deleted.");
      await loadKnowledgeDocs();
    } catch (error) {
      toast(endpointNote(error));
    }
  }

  async function searchKnowledge() {
    const query = els.kbQuery.value.trim();
    if (!query) {
      toast("Enter a knowledge search query.");
      return;
    }
    try {
      const result = await api("/api/knowledge/search", {
        method: "POST",
        body: JSON.stringify({ query, top_k: 5 })
      });
      renderKbHits(result.hits || []);
    } catch (error) {
      clear(els.kbResults);
      els.kbResults.classList.add("empty-state");
      setText(els.kbResults, endpointNote(error));
    }
  }

  function renderKbHits(hits) {
    clear(els.kbResults);
    if (!hits.length) {
      els.kbResults.classList.add("empty-state");
      setText(els.kbResults, "No local knowledge hits found.");
      return;
    }
    els.kbResults.classList.remove("empty-state");
    hits.forEach((hit) => {
      const item = div("kb-hit");
      const title = document.createElement("strong");
      const score = typeof hit.score === "number" ? hit.score.toFixed(3) : "n/a";
      setText(title, `${hit.doc_title || hit.doc_id || "Document"} | score ${score}`);
      const text = document.createElement("p");
      setText(text, hit.text || "");
      item.append(title, text);
      els.kbResults.appendChild(item);
    });
  }

  function requireStudy() {
    if (!state.selectedStudy) {
      toast("Select a study first.");
      return null;
    }
    return state.selectedStudy;
  }

  async function withBusy(button, fn) {
    button.disabled = true;
    button.classList.add("loading");
    try {
      await fn();
    } catch (error) {
      toast(endpointNote(error));
    } finally {
      button.disabled = false;
      button.classList.remove("loading");
    }
  }

  function showAiDisclaimer(text) {
    const disclaimer = text || state.config?.disclaimer;
    if (!disclaimer) {
      return;
    }
    setText(els.reportDisclaimer, disclaimer);
    els.reportDisclaimer.classList.remove("hidden");
  }

  function clearReportNote() {
    els.reportNote.classList.add("hidden");
    setText(els.reportNote, "");
  }

  function showInline(element, message) {
    setText(element, message);
    element.classList.remove("hidden");
  }

  function toast(message) {
    const node = div("toast", message);
    els.toastRegion.appendChild(node);
    window.setTimeout(() => node.remove(), 4200);
  }

  function endpointNote(error) {
    if (error.message === "Not available yet.") {
      return "This local endpoint is not available yet.";
    }
    return error.message || "Local request failed.";
  }

  function badge(type, label) {
    const node = document.createElement("span");
    node.className = `badge ${type}`;
    setText(node, label);
    return node;
  }

  function div(className, text) {
    const node = document.createElement("div");
    if (className) {
      node.className = className;
    }
    if (text != null) {
      setText(node, text);
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function setText(node, value) {
    node.textContent = value == null ? "" : String(value);
  }

  function formatDate(value) {
    if (!value) {
      return "No date";
    }
    const text = String(value);
    if (/^\d{8}$/.test(text)) {
      return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
    }
    const date = new Date(text);
    if (Number.isNaN(date.getTime())) {
      return text;
    }
    return date.toLocaleDateString();
  }

  function dateValue(study) {
    const value = study.study_date || study.created_at || "";
    if (/^\d{8}$/.test(String(value))) {
      return Number(value);
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 0 : date.getTime();
  }

  function statusLabel(value) {
    return String(value || "unread").replace(/_/g, " ");
  }
}());
