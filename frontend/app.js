(function () {
  "use strict";

  const state = {
    config: null,
    health: null,
    studies: [],
    selectedStudy: null,
    selectedStudyId: null,
    selectedImageUrl: "",
    frameIndex: 0,
    filter: "all",
    sort: "priority",
    analysis: null,
    activeFinding: null,
    chatHistory: [],
    skills: []
  };

  const priorityRank = { stat: 0, urgent: 1, routine: 2 };
  const els = {};

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("resize", () => window.requestAnimationFrame(positionBoxes));

  function init() {
    bindElements();
    bindEvents();
    loadStartup();
  }

  function bindElements() {
    [
      "app-name", "runtime-line", "offline-pill", "vision-pill", "top-disclaimer",
      "refresh-studies", "load-sample", "dicom-upload", "image-import", "filter-chips",
      "sort-select", "worklist", "worklist-count", "run-analysis", "study-subtitle",
      "image-stage", "study-meta", "finding-count", "analysis-findings", "analysis-detail",
      "prev-frame", "next-frame", "frame-slider", "frame-label", "indication", "technique",
      "comparison", "findings", "impression", "draft-report", "generate-impression",
      "save-report", "run-triage", "triage-result", "report-disclaimer", "report-note",
      "chat-log", "chat-form", "chat-input", "chat-disclaimer", "kb-folder",
      "kb-ingest-folder", "kb-urls", "kb-ingest-url", "kb-upload", "kb-url-list",
      "kb-docs", "kb-query", "kb-search", "kb-results", "skills-regenerate",
      "skills-list", "skill-detail", "toast-region"
    ].forEach((id) => {
      els[toKey(id)] = document.getElementById(id);
    });
  }

  function toKey(id) {
    return id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  }

  function bindEvents() {
    els.refreshStudies.addEventListener("click", () => withBusy(els.refreshStudies, refreshStudies));
    els.loadSample.addEventListener("click", () => withBusy(els.loadSample, loadSample));
    els.dicomUpload.addEventListener("change", () => uploadDicom());
    els.imageImport.addEventListener("change", () => importImage());
    els.filterChips.addEventListener("click", onFilterClick);
    els.sortSelect.addEventListener("change", () => { state.sort = els.sortSelect.value; renderWorklist(); });
    els.runAnalysis.addEventListener("click", () => withBusy(els.runAnalysis, runAnalysis));
    els.prevFrame.addEventListener("click", () => showFrame(state.frameIndex - 1));
    els.nextFrame.addEventListener("click", () => showFrame(state.frameIndex + 1));
    els.frameSlider.addEventListener("input", () => showFrame(Number(els.frameSlider.value)));
    els.draftReport.addEventListener("click", () => withBusy(els.draftReport, draftReport));
    els.generateImpression.addEventListener("click", () => withBusy(els.generateImpression, generateImpression));
    els.saveReport.addEventListener("click", () => withBusy(els.saveReport, saveReport));
    els.runTriage.addEventListener("click", () => withBusy(els.runTriage, runTriage));
    els.chatForm.addEventListener("submit", sendChat);
    els.kbIngestFolder.addEventListener("click", () => withBusy(els.kbIngestFolder, ingestFolder));
    els.kbIngestUrl.addEventListener("click", () => withBusy(els.kbIngestUrl, ingestUrls));
    els.kbUpload.addEventListener("change", () => uploadKnowledge());
    els.kbSearch.addEventListener("click", () => withBusy(els.kbSearch, searchKnowledge));
    els.skillsRegenerate.addEventListener("click", () => withBusy(els.skillsRegenerate, regenerateSkills));
    document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", switchTab));
  }

  async function loadStartup() {
    await Promise.all([loadConfig(), loadHealth()]);
    renderDisclaimers();
    await Promise.all([refreshStudies(), refreshKnowledge(), loadSkills()]);
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
    if (response.status === 404 || response.status === 503) {
      throw new Error("Not available yet.");
    }
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(text || `Request failed with status ${response.status}.`);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  async function loadConfig() {
    try {
      state.config = await api("/api/config");
      setText(els.appName, state.config.app || "Radiology AI Assistant");
    } catch (error) {
      note(els.topDisclaimer, "Configuration not available yet. Verify all AI output.");
    }
  }

  async function loadHealth() {
    try {
      state.health = await api("/api/health");
    } catch (error) {
      state.health = null;
      els.offlinePill.className = "pill error";
      setText(els.offlinePill, "LOCAL CHECK FAILED");
      setText(els.runtimeLine, error.message);
      return;
    }
    renderHealth();
  }

  function renderHealth() {
    const health = state.health || {};
    const cfg = state.config || {};
    els.offlinePill.className = health.offline_mode === false ? "pill warn" : "pill";
    setText(els.offlinePill, health.offline_mode === false ? "LOCAL MODE UNKNOWN" : "LOCAL / OFFLINE");
    els.visionPill.className = cfg.vision_available ? "pill" : "pill muted";
    setText(els.visionPill, `Vision ${cfg.vision_available ? "ready" : "not ready"}`);
    setText(els.runtimeLine, `${health.runtime || cfg.runtime || "runtime pending"} | LLM ${health.llm_available ? "ready" : "not ready"} | Vision ${cfg.vision_model || "pending"}`);
  }

  function renderDisclaimers() {
    const disclaimer = state.config?.disclaimer || "AI output is for clinical decision support only. A qualified clinician must verify all findings.";
    setText(els.topDisclaimer, disclaimer);
    setText(els.reportDisclaimer, disclaimer);
    setText(els.chatDisclaimer, disclaimer);
    renderHealth();
  }

  async function refreshStudies() {
    try {
      state.studies = await api("/api/studies/");
      renderWorklist();
      if (state.selectedStudyId && state.studies.some((study) => study.id === state.selectedStudyId)) {
        await selectStudy(state.selectedStudyId, true);
      }
    } catch (error) {
      state.studies = [];
      renderWorklist(endpointNote(error, "Studies API"));
    }
  }

  async function loadSample() {
    try {
      const result = await api("/api/studies/ingest", { method: "POST", body: JSON.stringify({}) });
      toast(result?.message || "Sample ingest complete.");
      await refreshStudies();
    } catch (error) {
      toast(endpointNote(error, "Sample ingest"));
    }
  }

  async function uploadDicom() {
    if (!els.dicomUpload.files.length) return;
    const form = new FormData();
    Array.from(els.dicomUpload.files).forEach((file) => form.append("files", file));
    await withBusy(els.dicomUpload.closest(".file-button"), async () => {
      try {
        const result = await api("/api/studies/ingest-upload", { method: "POST", body: form });
        toast(result?.message || "DICOM upload complete.");
        await refreshStudies();
      } catch (error) {
        toast(endpointNote(error, "DICOM upload"));
      } finally {
        els.dicomUpload.value = "";
      }
    });
  }

  async function importImage() {
    if (!els.imageImport.files.length) return;
    const form = new FormData();
    form.append("file", els.imageImport.files[0]);
    await withBusy(els.imageImport.closest(".file-button"), async () => {
      try {
        const result = await api("/api/analysis/upload-image", { method: "POST", body: form });
        state.selectedStudyId = result.study_id;
        state.selectedStudy = { id: result.study_id, modality: "IMG", description: "Imported image", frame_count: 1 };
        state.selectedImageUrl = result.image_url || `/api/analysis/image/${encodeURIComponent(result.study_id)}.png`;
        state.analysis = null;
        renderSelection();
        await loadPersistedAnalysis(result.study_id);
        await refreshStudies();
      } catch (error) {
        toast(endpointNote(error, "Image import"));
      } finally {
        els.imageImport.value = "";
      }
    });
  }

  function onFilterClick(event) {
    const button = event.target.closest("[data-filter]");
    if (!button) return;
    state.filter = button.dataset.filter;
    els.filterChips.querySelectorAll(".chip").forEach((chip) => chip.classList.toggle("active", chip === button));
    renderWorklist();
  }

  function filteredStudies() {
    return state.studies.filter((study) => {
      if (state.filter === "all") return true;
      if (state.filter === "critical") return Boolean(study.critical);
      if (["stat", "urgent", "routine"].includes(state.filter)) return study.priority === state.filter;
      return study.status === state.filter;
    }).sort((a, b) => {
      if (state.sort === "priority") return (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9) || dateValue(b) - dateValue(a);
      if (state.sort === "date") return dateValue(b) - dateValue(a);
      return String(a[state.sort] || "").localeCompare(String(b[state.sort] || ""));
    });
  }

  function renderWorklist(message) {
    clear(els.worklist);
    const studies = filteredStudies();
    setText(els.worklistCount, `${studies.length} of ${state.studies.length} studies`);
    if (message || !studies.length) {
      els.worklist.classList.add("empty-state");
      setText(els.worklist, message || (state.studies.length ? "No studies match this filter." : "Load studies, upload DICOM, or import a PNG/JPG."));
      return;
    }
    els.worklist.classList.remove("empty-state");
    studies.forEach((study) => {
      const card = el("button", { className: `study-card${study.id === state.selectedStudyId ? " active" : ""}`, type: "button" });
      card.addEventListener("click", () => selectStudy(study.id));
      const titleRow = el("div", { className: "study-title-row" });
      titleRow.append(el("strong", {}, study.patient_name || study.id || "Unknown"), badge(study.priority || "routine", study.priority || "routine"));
      const metaRow = el("div", { className: "study-meta-row" }, `${study.modality || "MOD"} | ${study.body_part || "Body part"}`, String(study.study_date || ""));
      const desc = el("div", { className: "study-desc" }, study.description || `${study.num_images || 0} images`);
      const statusRow = el("div", { className: "badge-row" });
      statusRow.append(badge(study.status || "unread", "status"));
      if (study.critical) statusRow.append(badge("critical", "critical"));
      card.append(titleRow, metaRow, desc, statusRow);
      els.worklist.append(card);
    });
  }

  async function selectStudy(id, quiet) {
    state.selectedStudyId = id;
    state.frameIndex = 0;
    state.analysis = null;
    state.activeFinding = null;
    try {
      state.selectedStudy = await api(`/api/studies/${encodeURIComponent(id)}`);
    } catch (error) {
      state.selectedStudy = state.studies.find((study) => study.id === id) || { id };
      if (!quiet) toast(endpointNote(error, "Study detail"));
    }
    state.selectedImageUrl = "";
    renderSelection();
    await Promise.all([loadReport(id), loadPersistedAnalysis(id)]);
    renderWorklist();
  }

  function renderSelection() {
    const study = state.selectedStudy;
    if (!study) {
      setText(els.studySubtitle, "Select a study or import an image.");
      renderImageEmpty("No image selected.");
      renderMeta(null);
      return;
    }
    const frameCount = Number(study.frame_count || study.num_images || 1);
    setText(els.studySubtitle, `${study.patient_name || study.id} | ${study.modality || "IMG"} | ${study.description || study.body_part || "Selected study"}`);
    renderMeta(study);
    if (state.selectedImageUrl) {
      showImage(state.selectedImageUrl);
    } else {
      showFrame(Math.min(state.frameIndex, Math.max(frameCount - 1, 0)));
    }
    els.runAnalysis.disabled = !state.selectedStudyId;
  }

  function renderMeta(study) {
    clear(els.studyMeta);
    if (!study) return;
    const rows = [
      ["Study", study.id], ["Patient", study.patient_name], ["Modality", study.modality],
      ["Body part", study.body_part], ["Priority", study.priority], ["Status", study.status],
      ["Frames", study.frame_count || study.num_images || 1]
    ];
    rows.forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") return;
      els.studyMeta.append(el("dt", {}, key), el("dd", {}, String(value)));
    });
  }

  function showFrame(index) {
    const study = state.selectedStudy;
    if (!study?.id) return;
    const count = Math.max(Number(study.frame_count || study.num_images || 1), 1);
    state.frameIndex = Math.min(Math.max(index, 0), count - 1);
    state.selectedImageUrl = `/api/studies/${encodeURIComponent(study.id)}/frame/${state.frameIndex}.png`;
    showImage(state.selectedImageUrl);
    els.frameSlider.disabled = count <= 1;
    els.prevFrame.disabled = state.frameIndex <= 0;
    els.nextFrame.disabled = state.frameIndex >= count - 1;
    els.frameSlider.max = String(count - 1);
    els.frameSlider.value = String(state.frameIndex);
    setText(els.frameLabel, `Frame ${state.frameIndex + 1} of ${count}`);
  }

  function showImage(src) {
    clear(els.imageStage);
    const img = el("img", { className: "viewer-image", alt: "Selected radiology image" });
    img.addEventListener("load", positionBoxes);
    img.addEventListener("error", () => renderImageEmpty("Image could not be loaded."));
    img.src = src;
    const layer = el("div", { className: "box-layer", id: "box-layer" });
    els.imageStage.append(img, layer);
    renderAnalysis();
  }

  function renderImageEmpty(text) {
    clear(els.imageStage);
    els.imageStage.append(el("div", { className: "empty-state" }, text));
  }

  async function loadPersistedAnalysis(id) {
    if (!id) return;
    try {
      state.analysis = await api(`/api/analysis/${encodeURIComponent(id)}`);
      if (state.analysis?.image_url) state.selectedImageUrl = state.analysis.image_url;
      if (state.analysis?.image_url) showImage(state.selectedImageUrl);
      renderAnalysis();
      applyAnalysisToFindings(false);
    } catch (error) {
      state.analysis = null;
      renderAnalysis(endpointNote(error, "Analysis"));
    }
  }

  async function runAnalysis() {
    if (!state.selectedStudyId) {
      toast("Select or import a study first.");
      return;
    }
    try {
      state.analysis = await api("/api/analysis/run", {
        method: "POST",
        body: JSON.stringify({ study_id: state.selectedStudyId, focus: "radiology findings with verifiable bounding boxes" })
      });
      state.selectedImageUrl = state.analysis.image_url || state.selectedImageUrl || `/api/analysis/image/${encodeURIComponent(state.selectedStudyId)}.png`;
      showImage(state.selectedImageUrl);
      renderAnalysis();
      applyAnalysisToFindings(true);
      toast("Analysis complete.");
    } catch (error) {
      renderAnalysis(endpointNote(error, "Analysis"));
      toast(endpointNote(error, "Analysis"));
    }
  }

  function renderAnalysis(message) {
    clear(els.analysisFindings);
    const findings = Array.isArray(state.analysis?.findings) ? state.analysis.findings : [];
    setText(els.findingCount, String(findings.length));
    if (message || !findings.length) {
      els.analysisFindings.classList.add("empty-state");
      setText(els.analysisFindings, message || "Run analysis to show finding boxes.");
    } else {
      els.analysisFindings.classList.remove("empty-state");
      findings.forEach((finding, idx) => {
        const number = idx + 1;
        const item = el("button", { className: `finding-item sev-${severity(finding)}`, type: "button", dataset: { index: String(idx) } });
        item.addEventListener("mouseenter", () => activateFinding(idx));
        item.addEventListener("focus", () => activateFinding(idx));
        item.addEventListener("click", () => activateFinding(idx));
        const head = el("div", { className: "finding-item-head" });
        head.append(el("strong", {}, `${number}. ${finding.label || "Finding"}`), severityChip(severity(finding)));
        item.append(head, el("div", {}, finding.description || "No description."));
        els.analysisFindings.append(item);
      });
    }
    setText(els.analysisDetail, state.analysis?.detail || state.analysis?.summary || "No analysis detail yet.");
    els.analysisDetail.classList.toggle("empty-state", !state.analysis?.detail && !state.analysis?.summary);
    positionBoxes();
  }

  function positionBoxes() {
    const layer = document.getElementById("box-layer");
    const img = els.imageStage.querySelector(".viewer-image");
    if (!layer || !img || !img.complete || !img.naturalWidth) return;
    const stageRect = els.imageStage.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    layer.style.left = `${imgRect.left - stageRect.left}px`;
    layer.style.top = `${imgRect.top - stageRect.top}px`;
    layer.style.width = `${imgRect.width}px`;
    layer.style.height = `${imgRect.height}px`;
    clear(layer);
    const findings = Array.isArray(state.analysis?.findings) ? state.analysis.findings : [];
    findings.forEach((finding, idx) => {
      if (!finding.box) return;
      const box = finding.box;
      const div = el("button", {
        className: `finding-box sev-${severity(finding)}${state.activeFinding === idx ? " active" : ""}`,
        type: "button",
        dataset: { index: String(idx), label: `${idx + 1}. ${finding.label || "Finding"}` }
      });
      div.style.left = `${clamp01(box.x) * imgRect.width}px`;
      div.style.top = `${clamp01(box.y) * imgRect.height}px`;
      div.style.width = `${clamp01(box.w) * imgRect.width}px`;
      div.style.height = `${clamp01(box.h) * imgRect.height}px`;
      div.addEventListener("mouseenter", () => activateFinding(idx));
      div.addEventListener("focus", () => activateFinding(idx));
      div.addEventListener("click", () => activateFinding(idx));
      layer.append(div);
    });
  }

  function activateFinding(idx) {
    state.activeFinding = idx;
    document.querySelectorAll(".finding-item").forEach((item) => item.classList.toggle("active", Number(item.dataset.index) === idx));
    document.querySelectorAll(".finding-box").forEach((box) => box.classList.toggle("active", Number(box.dataset.index) === idx));
  }

  function applyAnalysisToFindings(force) {
    const findings = Array.isArray(state.analysis?.findings) ? state.analysis.findings : [];
    if (!findings.length || (!force && els.findings.value.trim())) return;
    els.findings.value = findings.map((finding, idx) => `[Box ${idx + 1}] ${finding.label || "Finding"}: ${finding.description || ""}`).join("\n");
  }

  async function loadReport(id) {
    try {
      const report = await api(`/api/reports/${encodeURIComponent(id)}`);
      if (report) fillReport(report);
    } catch (error) {
      hide(els.reportNote);
    }
  }

  function reportPayload() {
    const study = state.selectedStudy || {};
    return {
      study_id: state.selectedStudyId || study.id || "",
      modality: study.modality || "",
      body_part: study.body_part || "",
      indication: els.indication.value.trim(),
      findings: els.findings.value.trim(),
      comparison: els.comparison.value.trim(),
      technique: els.technique.value.trim(),
      style: "concise radiology report",
      impression: els.impression.value.trim(),
      disclaimer: state.config?.disclaimer || ""
    };
  }

  async function draftReport() {
    if (!state.selectedStudyId) return toast("Select a study first.");
    try {
      const report = await api("/api/reports/draft", { method: "POST", body: JSON.stringify(reportPayload()) });
      fillReport(report);
      keepAnalysisVisible();
      showInline(els.reportNote, "Draft generated. Verify each finding against visible boxes before signing.");
    } catch (error) {
      showInline(els.reportNote, endpointNote(error, "Draft report"));
    }
  }

  async function generateImpression() {
    try {
      const result = await api("/api/reports/impression", { method: "POST", body: JSON.stringify(reportPayload()) });
      els.impression.value = result.impression || "";
      keepAnalysisVisible();
    } catch (error) {
      showInline(els.reportNote, endpointNote(error, "Generate impression"));
    }
  }

  async function saveReport() {
    try {
      const saved = await api("/api/reports/save", { method: "POST", body: JSON.stringify(reportPayload()) });
      fillReport(saved);
      toast("Report saved locally.");
    } catch (error) {
      showInline(els.reportNote, endpointNote(error, "Save report"));
    }
  }

  async function runTriage() {
    try {
      const text = [els.findings.value, els.impression.value].filter(Boolean).join("\n\n");
      const result = await api("/api/triage/analyze", { method: "POST", body: JSON.stringify({ text, study_id: state.selectedStudyId }) });
      clear(els.triageResult);
      els.triageResult.append(badge(result.level || "triage", result.critical ? "critical" : "status"));
      showInline(els.reportNote, result.rationale || "Triage complete.");
    } catch (error) {
      showInline(els.reportNote, endpointNote(error, "Triage"));
    }
  }

  function fillReport(report) {
    if (report.technique !== undefined) els.technique.value = report.technique || "";
    if (report.comparison !== undefined) els.comparison.value = report.comparison || "";
    if (report.findings !== undefined) els.findings.value = withBoxReferences(report.findings || "");
    if (report.impression !== undefined) els.impression.value = report.impression || "";
    if (report.disclaimer) setText(els.reportDisclaimer, report.disclaimer);
  }

  function withBoxReferences(text) {
    const findings = Array.isArray(state.analysis?.findings) ? state.analysis.findings : [];
    if (!findings.length || text.includes("[Box")) return text;
    return `${text}\n\nVerification boxes:\n${findings.map((finding, idx) => `[Box ${idx + 1}] ${finding.label || "Finding"}`).join("\n")}`;
  }

  function keepAnalysisVisible() {
    if (state.analysis) renderAnalysis();
  }

  async function sendChat(event) {
    event.preventDefault();
    const message = els.chatInput.value.trim();
    if (!message) return;
    els.chatInput.value = "";
    appendChat("user", message);
    const history = state.chatHistory.slice(-12);
    state.chatHistory.push({ role: "user", content: message });
    await withBusy(els.chatForm.querySelector("button"), async () => {
      try {
        const result = await api("/api/chat", {
          method: "POST",
          body: JSON.stringify({ message, history, study_id: state.selectedStudyId, use_tools: true })
        });
        const reply = result.reply || "No reply.";
        appendChat("assistant", reply, result.tool_calls || []);
        state.chatHistory.push({ role: "assistant", content: reply });
        if (result.disclaimer) setText(els.chatDisclaimer, result.disclaimer);
      } catch (error) {
        appendChat("assistant", endpointNote(error, "Chat"));
      }
    });
  }

  function appendChat(role, content, tools) {
    if (els.chatLog.classList.contains("empty-state")) {
      clear(els.chatLog);
      els.chatLog.classList.remove("empty-state");
    }
    const msg = el("div", { className: `chat-message ${role}` });
    msg.append(el("strong", {}, role), el("div", { className: "chat-bubble" }, content));
    if (tools?.length) {
      msg.append(el("div", { className: "tools-line" }, `Tools used: ${tools.map((tool) => tool.name || "tool").join(", ")}`));
    }
    els.chatLog.append(msg);
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  async function refreshKnowledge() {
    await Promise.all([loadKnowledgeUrls(), loadKnowledgeDocs()]);
  }

  async function ingestFolder() {
    const path = els.kbFolder.value.trim();
    if (!path) return toast("Enter a folder path.");
    try {
      await api("/api/knowledge/ingest-folder", { method: "POST", body: JSON.stringify({ path }) });
      toast("Folder indexing started.");
      await loadKnowledgeDocs();
    } catch (error) {
      toast(endpointNote(error, "Folder ingest"));
    }
  }

  async function ingestUrls() {
    const urls = els.kbUrls.value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean);
    if (!urls.length) return toast("Enter at least one URL.");
    try {
      await api("/api/knowledge/ingest-url", { method: "POST", body: JSON.stringify({ urls }) });
      els.kbUrls.value = "";
      toast("URL indexing started.");
      await loadKnowledgeUrls();
    } catch (error) {
      toast(endpointNote(error, "URL ingest"));
    }
  }

  async function uploadKnowledge() {
    if (!els.kbUpload.files.length) return;
    const form = new FormData();
    Array.from(els.kbUpload.files).forEach((file) => form.append("files", file));
    await withBusy(els.kbUpload.closest(".file-button"), async () => {
      try {
        await api("/api/knowledge/ingest-upload", { method: "POST", body: form });
        toast("Knowledge upload complete.");
        await loadKnowledgeDocs();
      } catch (error) {
        toast(endpointNote(error, "Knowledge upload"));
      } finally {
        els.kbUpload.value = "";
      }
    });
  }

  async function loadKnowledgeUrls() {
    try {
      const urls = await api("/api/knowledge/urls");
      renderRows(els.kbUrlList, urls, "No URLs indexed.", (item) => item.title || item.url || item.id, (item) => item.status || item.url, () => deleteKnowledgeUrl);
    } catch (error) {
      renderEmpty(els.kbUrlList, endpointNote(error, "Knowledge URLs"));
    }
  }

  async function loadKnowledgeDocs() {
    try {
      const docs = await api("/api/knowledge/docs");
      renderRows(els.kbDocs, docs, "No knowledge documents loaded.", (item) => item.title || item.name || item.id, (item) => item.source || item.status || "indexed", () => deleteKnowledgeDoc);
    } catch (error) {
      renderEmpty(els.kbDocs, endpointNote(error, "Knowledge docs"));
    }
  }

  async function deleteKnowledgeUrl(id) {
    try {
      await api(`/api/knowledge/urls/${encodeURIComponent(id)}`, { method: "DELETE" });
      await loadKnowledgeUrls();
    } catch (error) {
      toast(endpointNote(error, "Delete URL"));
    }
  }

  async function deleteKnowledgeDoc(id) {
    try {
      await api(`/api/knowledge/docs/${encodeURIComponent(id)}`, { method: "DELETE" });
      await loadKnowledgeDocs();
    } catch (error) {
      toast(endpointNote(error, "Delete doc"));
    }
  }

  async function searchKnowledge() {
    const query = els.kbQuery.value.trim();
    if (!query) return toast("Enter a search query.");
    try {
      const result = await api("/api/knowledge/search", { method: "POST", body: JSON.stringify({ query, top_k: 5 }) });
      clear(els.kbResults);
      const hits = result.hits || [];
      if (!hits.length) return renderEmpty(els.kbResults, "No hits.");
      els.kbResults.classList.remove("empty-state");
      hits.forEach((hit) => {
        els.kbResults.append(el("div", { className: "hit-row" },
          el("strong", {}, `${hit.doc_title || "Source"} | score ${formatScore(hit.score)}`),
          el("div", {}, hit.text || "")
        ));
      });
    } catch (error) {
      renderEmpty(els.kbResults, endpointNote(error, "Knowledge search"));
    }
  }

  async function loadSkills() {
    try {
      const result = await api("/api/skills");
      state.skills = result.skills || [];
      renderSkills();
    } catch (error) {
      renderEmpty(els.skillsList, endpointNote(error, "Skills"));
    }
  }

  async function regenerateSkills() {
    try {
      await api("/api/skills/regenerate", { method: "POST", body: JSON.stringify({}) });
      toast("Skill regeneration started.");
      await loadSkills();
    } catch (error) {
      toast(endpointNote(error, "Regenerate skills"));
    }
  }

  function renderSkills() {
    clear(els.skillsList);
    if (!state.skills.length) return renderEmpty(els.skillsList, "No skills generated.");
    els.skillsList.classList.remove("empty-state");
    state.skills.forEach((skill) => {
      const row = el("button", { className: "skill-row", type: "button" });
      row.append(el("strong", {}, skill.name || skill.slug || "Skill"), el("span", {}, skill.description || "No description."));
      row.addEventListener("click", () => loadSkillDetail(skill.slug));
      els.skillsList.append(row);
    });
  }

  async function loadSkillDetail(slug) {
    if (!slug) return;
    setText(els.skillDetail, "Loading skill...");
    try {
      const skill = await api(`/api/skills/${encodeURIComponent(slug)}`);
      setText(els.skillDetail, `${skill.name || slug}\n\n${skill.description || ""}\n\n${skill.markdown || ""}\n\nAgent:\n${skill.agent || "Not generated."}`);
      els.skillDetail.classList.remove("empty-state");
    } catch (error) {
      setText(els.skillDetail, endpointNote(error, "Skill detail"));
    }
  }

  function renderRows(container, rows, emptyText, titleFn, subtitleFn, deleteFnFactory) {
    clear(container);
    if (!Array.isArray(rows) || !rows.length) return renderEmpty(container, emptyText);
    container.classList.remove("empty-state");
    rows.forEach((item) => {
      const row = el("div", { className: "doc-row" });
      const actions = el("div", { className: "row-actions" });
      actions.append(el("strong", {}, titleFn(item)));
      const del = el("button", { className: "secondary delete-btn", type: "button" }, "Delete");
      del.addEventListener("click", () => deleteFnFactory()(item.id));
      actions.append(del);
      row.append(actions, el("span", {}, subtitleFn(item) || ""));
      container.append(row);
    });
  }

  function switchTab(event) {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button === tab));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.add("hidden"));
    document.getElementById(`${tab.dataset.tab}-tab`)?.classList.remove("hidden");
  }

  function badge(text, kind) {
    return el("span", { className: `badge ${kind || "status"}` }, text || "status");
  }

  function severityChip(sev) {
    return el("span", { className: `sev-chip sev-${sev}` }, sev);
  }

  function severity(finding) {
    const sev = String(finding?.severity || "normal").toLowerCase();
    return ["normal", "minor", "moderate", "critical"].includes(sev) ? sev : "normal";
  }

  function el(tag, options, ...children) {
    const node = document.createElement(tag);
    const opts = options || {};
    if (opts.className) node.className = opts.className;
    if (opts.type) node.type = opts.type;
    if (opts.alt) node.alt = opts.alt;
    if (opts.id) node.id = opts.id;
    if (opts.dataset) Object.entries(opts.dataset).forEach(([key, value]) => { node.dataset[key] = value; });
    children.flat().forEach((child) => {
      if (child === null || child === undefined) return;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function clear(node) {
    node.replaceChildren();
  }

  function setText(node, text) {
    node.textContent = text || "";
  }

  function note(node, text) {
    setText(node, text);
  }

  function hide(node) {
    node.classList.add("hidden");
  }

  function showInline(node, text) {
    setText(node, text);
    node.classList.remove("hidden");
  }

  function renderEmpty(node, text) {
    clear(node);
    node.classList.add("empty-state");
    setText(node, text);
  }

  async function withBusy(target, fn) {
    target?.classList.add("loading");
    if (target?.tagName === "BUTTON") target.disabled = true;
    try {
      return await fn();
    } finally {
      target?.classList.remove("loading");
      if (target?.tagName === "BUTTON") target.disabled = false;
      if (target === els.runAnalysis && !state.selectedStudyId) target.disabled = true;
    }
  }

  function toast(message) {
    const toastEl = el("div", { className: "toast" }, message);
    els.toastRegion.append(toastEl);
    window.setTimeout(() => toastEl.remove(), 4200);
  }

  function endpointNote(error, label) {
    const msg = error?.message || "Request failed.";
    return msg === "Not available yet." ? `${label} is not available yet.` : `${label}: ${msg}`;
  }

  function dateValue(study) {
    const value = Date.parse(study.study_date || "");
    return Number.isFinite(value) ? value : 0;
  }

  function clamp01(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(1, number));
  }

  function formatScore(score) {
    const number = Number(score);
    return Number.isFinite(number) ? number.toFixed(3) : "n/a";
  }
}());
