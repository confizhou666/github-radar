(() => {
  "use strict";

  const STORAGE_KEY = "github_radar_reader_v1";

  const LANG_COLORS = {
    Python: "#3572A5",
    TypeScript: "#3178C6",
    JavaScript: "#D9B62A",
    Rust: "#CE422B",
    Go: "#00ADD8",
    C: "#555555",
    "C++": "#F34B7D",
    "C#": "#178600",
    Java: "#B07219",
    Swift: "#F05138",
    Kotlin: "#A97BFF",
    HTML: "#E34C26",
    CSS: "#563D7C",
    Shell: "#4E9A06",
    PowerShell: "#012456",
    Ruby: "#701516",
    PHP: "#4F5D95",
    Dart: "#00B4AB",
    Vue: "#41B883",
    Zig: "#EC915C",
    Lua: "#000080",
    TeX: "#3D6117",
  };

  const BUCKET_META = {
    trending: { label: "Trending", cls: "bucket-trending" },
    radar: { label: "黑马雷达", cls: "bucket-radar" },
    community: { label: "社区发现", cls: "bucket-community" },
  };

  const state = {
    items: [],
    status: {},
    bucket: "all",
    category: "",
    language: "",
    search: "",
    sort: "newest",
    multiOnly: false,
    hideRead: false,
    saved: new Set(),
    read: new Set(),
    hidden: new Set(),
    theme: "light",
    drawerKey: null,
    drawerListKeys: [],
    readmeCache: new Map(),
  };

  const els = {
    issueMeta: document.getElementById("issueMeta"),
    bucketTabs: document.getElementById("bucketTabs"),
    searchInput: document.getElementById("searchInput"),
    langSelect: document.getElementById("langSelect"),
    sortSelect: document.getElementById("sortSelect"),
    btnMultiSource: document.getElementById("btnMultiSource"),
    btnHideRead: document.getElementById("btnHideRead"),
    categoryRibbon: document.getElementById("categoryRibbon"),
    filterStatus: document.getElementById("filterStatus"),
    filterStatusText: document.getElementById("filterStatusText"),
    btnResetFilters: document.getElementById("btnResetFilters"),
    cardGrid: document.getElementById("cardGrid"),
    emptyState: document.getElementById("emptyState"),
    emptyTitle: document.getElementById("emptyTitle"),
    emptyDesc: document.getElementById("emptyDesc"),
    btnEmptyReset: document.getElementById("btnEmptyReset"),
    drawerBackdrop: document.getElementById("drawerBackdrop"),
    detailDrawer: document.getElementById("detailDrawer"),
    drawerBody: document.getElementById("drawerBody"),
    drawerFooter: document.getElementById("drawerFooter"),
    drawerPos: document.getElementById("drawerPos"),
    btnPrevItem: document.getElementById("btnPrevItem"),
    btnNextItem: document.getElementById("btnNextItem"),
    btnCloseDrawer: document.getElementById("btnCloseDrawer"),
    btnRssModal: document.getElementById("btnRssModal"),
    rssBackdrop: document.getElementById("rssBackdrop"),
    btnCloseRss: document.getElementById("btnCloseRss"),
    rssList: document.getElementById("rssList"),
    localStatsSummary: document.getElementById("localStatsSummary"),
    btnClearHidden: document.getElementById("btnClearHidden"),
    btnClearRead: document.getElementById("btnClearRead"),
    btnExportSavedMd: document.getElementById("btnExportSavedMd"),
    btnExportBackup: document.getElementById("btnExportBackup"),
    btnImportBackup: document.getElementById("btnImportBackup"),
    importBackupInput: document.getElementById("importBackupInput"),
    btnDigestModal: document.getElementById("btnDigestModal"),
    digestBackdrop: document.getElementById("digestBackdrop"),
    btnCloseDigest: document.getElementById("btnCloseDigest"),
    digestSubtitle: document.getElementById("digestSubtitle"),
    digestTextarea: document.getElementById("digestTextarea"),
    btnDownloadDigest: document.getElementById("btnDownloadDigest"),
    btnCopyDigest: document.getElementById("btnCopyDigest"),
    btnTheme: document.getElementById("btnTheme"),
    themeIconSun: document.getElementById("themeIconSun"),
    themeIconMoon: document.getElementById("themeIconMoon"),
    toast: document.getElementById("toast"),
  };

  let toastTimer = null;

  function showToast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, 2200);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatStars(n) {
    const num = Number(n) || 0;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}k`;
    return String(num);
  }

  function formatShortDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(5, 10);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${mm}/${dd}`;
  }

  function getLocalDateKey(iso) {
    if (!iso) return "未知日期";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10) || "未知日期";
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }

  function formatDateSectionLabel(dateKey) {
    if (!dateKey || dateKey === "未知日期") return "早期收录";
    const now = new Date();
    const todayKey = getLocalDateKey(now.toISOString());
    const yest = new Date(now.getTime() - 86400000);
    const yestKey = getLocalDateKey(yest.toISOString());
    if (dateKey === todayKey) return `本期 · ${dateKey}（今日）`;
    if (dateKey === yestKey) return `上期 · ${dateKey}（昨天）`;
    return `往期 · ${dateKey}`;
  }

  function formatFullTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
  }

  function copyText(text, successMsg = "已复制到剪贴板") {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(
        () => showToast(successMsg),
        () => fallbackCopy(text, successMsg)
      );
    } else {
      fallbackCopy(text, successMsg);
    }
  }

  function fallbackCopy(text, successMsg) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      showToast(successMsg);
    } catch {
      showToast("复制失败，请手动复制");
    }
    document.body.removeChild(ta);
  }

  function downloadTextFile(filename, content, mime = "text/plain;charset=utf-8") {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function loadLocalPrefs() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed.saved)) state.saved = new Set(parsed.saved);
        if (Array.isArray(parsed.read)) state.read = new Set(parsed.read);
        if (Array.isArray(parsed.hidden)) state.hidden = new Set(parsed.hidden);
        if (typeof parsed.hideRead === "boolean") state.hideRead = parsed.hideRead;
        if (parsed.theme === "dark" || parsed.theme === "light") {
          state.theme = parsed.theme;
        }
      } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
        state.theme = "dark";
      }
    } catch {
      // ignore storage errors
    }
    applyTheme();
    els.btnHideRead.setAttribute("aria-pressed", String(state.hideRead));
  }

  function saveLocalPrefs() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          saved: [...state.saved],
          read: [...state.read],
          hidden: [...state.hidden],
          hideRead: state.hideRead,
          theme: state.theme,
        })
      );
    } catch {
      // ignore storage quota errors
    }
  }

  function applyTheme() {
    document.documentElement.setAttribute("data-theme", state.theme);
    const isDark = state.theme === "dark";
    els.themeIconSun.hidden = isDark;
    els.themeIconMoon.hidden = !isDark;
  }

  function toggleTheme() {
    state.theme = state.theme === "dark" ? "light" : "dark";
    applyTheme();
    saveLocalPrefs();
  }

  function getVisibleBaseItems() {
    return state.items.filter((item) => !state.hidden.has(item.key));
  }

  function getFilteredItems() {
    const q = state.search.trim().toLowerCase();
    let list = getVisibleBaseItems();

    if (state.bucket === "saved") {
      list = list.filter((it) => state.saved.has(it.key));
    } else if (state.bucket !== "all") {
      list = list.filter((it) => it.bucket === state.bucket);
    }

    if (state.category) {
      list = list.filter((it) => it.category_tag === state.category);
    }

    if (state.language) {
      list = list.filter((it) => it.language === state.language);
    }

    if (state.multiOnly) {
      list = list.filter((it) => (it.sources || []).length >= 2);
    }

    if (state.hideRead && state.bucket !== "saved") {
      list = list.filter((it) => !state.read.has(it.key));
    }

    if (q) {
      list = list.filter((it) => {
        const hay = [
          it.repo_name,
          it.ai_highlight,
          it.desc_cn,
          it.description,
          it.category_tag,
          it.language,
          it.license_name,
          it.release_tag,
          ...(it.topics || []),
          ...(it.sources || []),
        ]
          .join(" ")
          .toLowerCase();
        return hay.includes(q);
      });
    }

    const sorted = [...list];
    if (state.sort === "stars") {
      sorted.sort((a, b) => (b.stars || 0) - (a.stars || 0));
    } else if (state.sort === "delta") {
      sorted.sort((a, b) => {
        const dd = (b.stars_delta_24h || 0) - (a.stars_delta_24h || 0);
        if (dd !== 0) return dd;
        const ms = (b.sources || []).length - (a.sources || []).length;
        if (ms !== 0) return ms;
        return (b.stars || 0) - (a.stars || 0);
      });
    } else {
      sorted.sort((a, b) => {
        const dA = getLocalDateKey(a.discovered_at);
        const dB = getLocalDateKey(b.discovered_at);
        const dayCmp = dB.localeCompare(dA);
        if (dayCmp !== 0) return dayCmp;
        const ms = (b.sources || []).length - (a.sources || []).length;
        if (ms !== 0) return ms;
        const dd = (b.stars_delta_24h || 0) - (a.stars_delta_24h || 0);
        if (dd !== 0) return dd;
        return (b.stars || 0) - (a.stars || 0);
      });
    }
    return sorted;
  }

  function updateTabCounts() {
    const visible = getVisibleBaseItems();
    const counts = {
      all: visible.length,
      trending: 0,
      radar: 0,
      community: 0,
      saved: 0,
    };
    let multiCount = 0;
    for (const it of visible) {
      if (it.bucket === "trending") counts.trending += 1;
      else if (it.bucket === "radar") counts.radar += 1;
      else counts.community += 1;
      if (state.saved.has(it.key)) counts.saved += 1;
      if ((it.sources || []).length >= 2) multiCount += 1;
    }
    for (const [k, v] of Object.entries(counts)) {
      const el = els.bucketTabs.querySelector(`[data-count="${k}"]`);
      if (el) el.textContent = String(v);
    }
    els.btnMultiSource.textContent = multiCount > 0 ? `多源共振 (${multiCount})` : "多源共振";
  }

  function populateLanguages() {
    const visible = getVisibleBaseItems();
    const freq = new Map();
    for (const it of visible) {
      if (!it.language) continue;
      freq.set(it.language, (freq.get(it.language) || 0) + 1);
    }
    const sorted = [...freq.entries()].sort((a, b) => b[1] - a[1]);
    const current = state.language;
    els.langSelect.innerHTML = `<option value="">全部语言 (${sorted.length})</option>`;
    for (const [lang, count] of sorted) {
      const opt = document.createElement("option");
      opt.value = lang;
      opt.textContent = `${lang} (${count})`;
      els.langSelect.appendChild(opt);
    }
    els.langSelect.value = current;
  }

  function renderCategoryRibbon() {
    let pool = getVisibleBaseItems();
    if (state.bucket === "saved") {
      pool = pool.filter((it) => state.saved.has(it.key));
    } else if (state.bucket !== "all") {
      pool = pool.filter((it) => it.bucket === state.bucket);
    }

    const freq = new Map();
    for (const it of pool) {
      const tag = (it.category_tag || "").trim();
      if (!tag) continue;
      freq.set(tag, (freq.get(tag) || 0) + 1);
    }
    const topCats = [...freq.entries()]
      .filter(([, count]) => count >= 2)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);

    if (topCats.length <= 1) {
      els.categoryRibbon.innerHTML = "";
      return;
    }

    const chips = [
      `<button type="button" class="cat-chip ${state.category === "" ? "is-active" : ""}" data-cat="">全部主题</button>`,
      ...topCats.map(
        ([tag, count]) =>
          `<button type="button" class="cat-chip ${state.category === tag ? "is-active" : ""}" data-cat="${escapeHtml(tag)}">${escapeHtml(tag)} <span style="opacity:.65">${count}</span></button>`
      ),
    ];
    els.categoryRibbon.innerHTML = chips.join("");
  }

  function renderFilterStatus(shownCount) {
    const activeParts = [];
    if (state.category) activeParts.push(`主题「${state.category}」`);
    if (state.language) activeParts.push(`语言「${state.language}」`);
    if (state.search.trim()) activeParts.push(`关键词「${state.search.trim()}」`);
    if (state.multiOnly) activeParts.push("仅看多源共振");
    if (state.hideRead && state.bucket !== "saved") activeParts.push("仅看未读");

    if (activeParts.length === 0) {
      els.filterStatus.hidden = true;
      return;
    }
    els.filterStatus.hidden = false;
    els.filterStatusText.textContent = `当前筛选：${activeParts.join(" · ")} — 共 ${shownCount} 个项目`;
  }

  function pickCardPrimaryText(item) {
    return item.ai_highlight || item.desc_cn || item.description || "暂无项目简介";
  }

  function pickCardSecondaryText(item, primaryText) {
    const en = (item.description || "").trim();
    const cn = (item.desc_cn || "").trim();
    if (en && en !== primaryText) return en;
    if (cn && cn !== primaryText) return cn;
    return "";
  }

  function renderCardHtml(item, isFeatured) {
    const isRead = state.read.has(item.key);
    const isSaved = state.saved.has(item.key);
    const isActiveDrawer = state.drawerKey === item.key;
    const bMeta = BUCKET_META[item.bucket] || BUCKET_META.community;
    const langColor = LANG_COLORS[item.language] || "#8B949E";
    const primaryText = pickCardPrimaryText(item);
    const secondaryText = pickCardSecondaryText(item, primaryText);
    const sourceCount = (item.sources || []).length;
    const delta24 = Number(item.stars_delta_24h || 0);

    const cardClasses = [
      "repo-card",
      isFeatured ? "is-featured" : "",
      isRead ? "is-read" : "",
      isActiveDrawer ? "is-active-drawer" : "",
    ]
      .filter(Boolean)
      .join(" ");

    return `
      <article class="${cardClasses}" data-key="${escapeHtml(item.key)}" tabindex="0">
        <div class="card-eyebrow">
          <div class="eyebrow-left">
            <span class="bucket-pill ${bMeta.cls}">${bMeta.label}</span>
            ${sourceCount >= 2 ? `<span class="multi-badge" title="同时命中：${escapeHtml(item.sources.join(" + "))}">多源×${sourceCount}</span>` : ""}
            ${item.release_tag ? `<span class="release-badge">${escapeHtml(item.release_tag)}</span>` : ""}
            ${item.category_tag ? `<span class="cat-label">${escapeHtml(item.category_tag)}</span>` : ""}
          </div>
          <div class="eyebrow-right">
            ${
              item.language
                ? `<span class="lang-badge"><span class="lang-dot" style="background:${langColor}"></span>${escapeHtml(item.language)}</span>`
                : ""
            }
            <span class="star-badge" title="${item.stars.toLocaleString()} Stars">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M12 2.6l2.9 5.88 6.5.94-4.7 4.58 1.1 6.47L12 17.4l-5.8 3.07 1.1-6.47-4.7-4.58 6.5-.94L12 2.6z"/></svg>
              ${formatStars(item.stars)}
            </span>
            ${delta24 > 0 ? `<span class="delta-badge" title="近 24h 新增 ${delta24.toLocaleString()} Stars">+${formatStars(delta24)}</span>` : ""}
          </div>
        </div>

        <h2 class="repo-heading">
          ${item.owner ? `<span class="repo-owner">${escapeHtml(item.owner)} /</span>` : ""}
          <span class="repo-title-name">${escapeHtml(item.name || item.repo_name)}</span>
        </h2>

        <div class="card-body">
          <p class="card-highlight">${escapeHtml(primaryText)}</p>
          ${secondaryText ? `<p class="card-subdesc">${escapeHtml(secondaryText)}</p>` : ""}
        </div>

        <div class="card-footer">
          <div class="card-date" title="发现于 ${escapeHtml(formatFullTime(item.discovered_at))}">
            <span class="unread-dot" aria-hidden="true"></span>
            <span>${formatShortDate(item.discovered_at)}</span>
          </div>

          <div class="card-actions">
            <button type="button" class="action-btn ${isSaved ? "is-saved" : ""}" data-action="save" data-key="${escapeHtml(item.key)}" title="${isSaved ? "取消收藏" : "收藏"}">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="${isSaved ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
            </button>
            <button type="button" class="action-btn" data-action="read" data-key="${escapeHtml(item.key)}" title="${isRead ? "标为未读" : "标为已读"}">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            </button>
            <button type="button" class="action-btn" data-action="hide" data-key="${escapeHtml(item.key)}" title="不感兴趣（隐藏）">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
            ${
              item.discussion_url
                ? `<a class="action-btn link-pill" href="${escapeHtml(item.discussion_url)}" target="_blank" rel="noopener noreferrer" data-action="link" title="打开社区讨论 / Release">讨论</a>`
                : ""
            }
            <a class="action-btn link-pill" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer" data-action="link" title="在新标签页打开 GitHub 仓库">GitHub ↗</a>
          </div>
        </div>
      </article>
    `;
  }

  function renderCards() {
    updateTabCounts();
    renderCategoryRibbon();

    const filtered = getFilteredItems();
    renderFilterStatus(filtered.length);

    if (filtered.length === 0) {
      els.cardGrid.innerHTML = "";
      els.emptyState.hidden = false;
      if (state.bucket === "saved" && state.saved.size === 0) {
        els.emptyTitle.textContent = "还没有收藏任何项目";
        els.emptyDesc.textContent = "点击卡片右下角的星标按钮，随时把感兴趣的项目收进这里。";
      } else {
        els.emptyTitle.textContent = "这一栏暂时没有匹配的项目";
        els.emptyDesc.textContent = "试试切换其他栏目、放宽筛选条件，或取消「隐藏已读」。";
      }
      return;
    }

    els.emptyState.hidden = true;

    const allowFeatured =
      state.bucket === "all" &&
      !state.category &&
      !state.language &&
      !state.search.trim() &&
      !state.multiOnly &&
      state.sort === "newest" &&
      filtered.length >= 4;

    if (state.sort === "newest") {
      // Group by discovery date for magazine issue timeline feel
      const groups = new Map();
      for (const item of filtered) {
        const dk = getLocalDateKey(item.discovered_at);
        if (!groups.has(dk)) groups.set(dk, []);
        groups.get(dk).push(item);
      }
      const chunks = [];
      let globalIdx = 0;
      for (const [dateKey, groupItems] of groups.entries()) {
        chunks.push(`
          <div class="date-section-header">
            <span class="date-section-title">${escapeHtml(formatDateSectionLabel(dateKey))}</span>
            <span class="date-section-count">共 ${groupItems.length} 项</span>
          </div>
        `);
        for (const item of groupItems) {
          chunks.push(renderCardHtml(item, allowFeatured && globalIdx === 0));
          globalIdx += 1;
        }
      }
      els.cardGrid.innerHTML = chunks.join("");
    } else {
      els.cardGrid.innerHTML = filtered
        .map((item, idx) => renderCardHtml(item, allowFeatured && idx === 0))
        .join("");
    }
  }

  function getItemByKey(key) {
    return state.items.find((it) => it.key === key) || null;
  }

  /* ---------- Drawer Logic ---------- */
  function openDrawer(key) {
    const filtered = getFilteredItems();
    state.drawerListKeys = filtered.map((it) => it.key);
    if (!state.drawerListKeys.includes(key)) {
      state.drawerListKeys = [key];
    }
    showDrawerItem(key);

    els.drawerBackdrop.hidden = false;
    els.detailDrawer.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => {
      els.drawerBackdrop.classList.add("is-open");
      els.detailDrawer.classList.add("is-open");
    });
    document.body.style.overflow = "hidden";
  }

  function closeDrawer() {
    if (!state.drawerKey) return;
    state.drawerKey = null;
    els.drawerBackdrop.classList.remove("is-open");
    els.detailDrawer.classList.remove("is-open");
    els.detailDrawer.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    setTimeout(() => {
      if (!state.drawerKey) {
        els.drawerBackdrop.hidden = true;
      }
    }, 240);
    renderCards();
  }

  function showDrawerItem(key) {
    const item = getItemByKey(key);
    if (!item) return;

    state.drawerKey = key;
    if (!state.read.has(key)) {
      state.read.add(key);
      saveLocalPrefs();
      const cardEl = els.cardGrid.querySelector(`[data-key="${CSS.escape(key)}"]`);
      if (cardEl) cardEl.classList.add("is-read");
    }

    els.cardGrid.querySelectorAll(".repo-card.is-active-drawer").forEach((el) => el.classList.remove("is-active-drawer"));
    const activeCard = els.cardGrid.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (activeCard) activeCard.classList.add("is-active-drawer");

    const idx = state.drawerListKeys.indexOf(key);
    els.drawerPos.textContent = `${idx + 1} / ${state.drawerListKeys.length}`;
    els.btnPrevItem.disabled = idx <= 0;
    els.btnNextItem.disabled = idx < 0 || idx >= state.drawerListKeys.length - 1;

    const bMeta = BUCKET_META[item.bucket] || BUCKET_META.community;
    const langColor = LANG_COLORS[item.language] || "#8B949E";
    const isSaved = state.saved.has(item.key);
    const cachedReadme = state.readmeCache.get(item.key);
    const delta24 = Number(item.stars_delta_24h || 0);
    const delta7d = Number(item.stars_delta_7d || 0);
    const topics = Array.isArray(item.topics) ? item.topics : [];

    els.drawerBody.innerHTML = `
      <div class="drawer-tags">
        <span class="bucket-pill ${bMeta.cls}">${bMeta.label}</span>
        ${(item.sources || []).length >= 2 ? `<span class="multi-badge">多源共振×${item.sources.length}</span>` : ""}
        ${item.release_tag ? `<span class="release-badge">${escapeHtml(item.release_tag)}</span>` : ""}
        ${item.category_tag ? `<span class="cat-label">${escapeHtml(item.category_tag)}</span>` : ""}
        ${(item.sources || []).map((s) => `<span class="bucket-pill">${escapeHtml(s)}</span>`).join("")}
      </div>

      ${item.owner ? `<div class="drawer-owner">${escapeHtml(item.owner)} /</div>` : ""}
      <h2 class="drawer-title">${escapeHtml(item.name || item.repo_name)}</h2>

      <div class="drawer-metrics">
        <span>★ ${item.stars.toLocaleString()} Stars${delta24 > 0 ? ` <span class="delta-badge">+${delta24.toLocaleString()} / 24h</span>` : ""}</span>
        ${delta7d > delta24 ? `<span>7d +${delta7d.toLocaleString()}</span>` : ""}
        <span>⑂ ${item.forks.toLocaleString()} Forks</span>
        ${item.open_issues > 0 ? `<span>◎ ${item.open_issues.toLocaleString()} Issues</span>` : ""}
        ${
          item.language
            ? `<span class="lang-badge"><span class="lang-dot" style="background:${langColor}"></span>${escapeHtml(item.language)}</span>`
            : ""
        }
        ${item.license_name ? `<span>⚖ ${escapeHtml(item.license_name)}</span>` : ""}
        ${item.pushed_at ? `<span>最近提交 ${escapeHtml(formatShortDate(item.pushed_at))}</span>` : ""}
        <span>收录 ${escapeHtml(formatFullTime(item.discovered_at))}</span>
      </div>

      ${
        topics.length
          ? `<div class="topic-chips">${topics
              .map((t) => `<button type="button" class="topic-chip" data-topic="${escapeHtml(t)}" title="按 Topic 搜索">#${escapeHtml(t)}</button>`)
              .join("")}</div>`
          : ""
      }

      ${
        item.ai_highlight
          ? `
        <div class="drawer-highlight-box">
          <div class="drawer-section-label">AI 一句话导读</div>
          <p class="drawer-highlight-text">${escapeHtml(item.ai_highlight)}</p>
        </div>`
          : ""
      }

      <div class="drawer-desc-group">
        ${
          item.desc_cn
            ? `<div>
                <div class="drawer-section-label">中文简介</div>
                <p class="drawer-desc-p">${escapeHtml(item.desc_cn)}</p>
              </div>`
            : ""
        }
        ${
          item.description && item.description !== item.desc_cn
            ? `<div>
                <div class="drawer-section-label">GitHub 原文简介</div>
                <p class="drawer-desc-p en">${escapeHtml(item.description)}</p>
              </div>`
            : ""
        }
      </div>

      <section class="readme-section">
        <div class="readme-head">
          <div class="drawer-section-label" style="margin:0">README 速览</div>
          <button type="button" class="btn-outline small" id="btnLoadReadme">
            ${cachedReadme ? "已加载" : "在线拉取 README 预览"}
          </button>
        </div>
        <div class="readme-content" id="readmeContent">
          ${cachedReadme || `<p style="color:var(--ink-muted);margin:0;">点击上方按钮可直接在抽屉里预览项目 README，无需跳转标签页。</p>`}
        </div>
      </section>
    `;

    els.drawerFooter.innerHTML = `
      <div class="drawer-footer-left">
        <a class="btn-primary" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">前往 GitHub ↗</a>
        ${
          item.discussion_url
            ? `<a class="btn-outline" style="display:inline-flex;align-items:center;" href="${escapeHtml(item.discussion_url)}" target="_blank" rel="noopener noreferrer">社区讨论 ↗</a>`
            : ""
        }
        <button type="button" class="btn-outline small" id="btnCopyClone">复制 clone</button>
      </div>
      <div class="drawer-footer-right">
        <button type="button" class="btn-outline small" id="btnDrawerSave" style="${isSaved ? "border-color:var(--accent);color:var(--accent);" : ""}">
          ${isSaved ? "★ 已收藏" : "☆ 收藏"}
        </button>
        <button type="button" class="btn-outline small" id="btnDrawerUnread">标为未读</button>
        <button type="button" class="btn-outline small" id="btnDrawerHide">屏蔽</button>
      </div>
    `;

    els.drawerBody.scrollTop = 0;

    const btnLoadReadme = document.getElementById("btnLoadReadme");
    if (btnLoadReadme && !cachedReadme) {
      btnLoadReadme.addEventListener("click", () => loadReadmePreview(item));
    }

    els.drawerBody.querySelectorAll("[data-topic]").forEach((chip) => {
      chip.addEventListener("click", () => {
        state.search = chip.dataset.topic || "";
        els.searchInput.value = state.search;
        closeDrawer();
        showToast(`已按 Topic「${state.search}」筛选`);
      });
    });

    document.getElementById("btnCopyClone").addEventListener("click", () => {
      copyText(`git clone ${item.url}.git`, "已复制 git clone 命令");
    });

    document.getElementById("btnDrawerSave").addEventListener("click", () => {
      toggleSaved(item.key);
      showDrawerItem(item.key);
      updateTabCounts();
    });

    document.getElementById("btnDrawerUnread").addEventListener("click", () => {
      state.read.delete(item.key);
      saveLocalPrefs();
      const cardEl = els.cardGrid.querySelector(`[data-key="${CSS.escape(item.key)}"]`);
      if (cardEl) cardEl.classList.remove("is-read");
      showToast("已重新标为未读");
    });

    document.getElementById("btnDrawerHide").addEventListener("click", () => {
      state.hidden.add(item.key);
      saveLocalPrefs();
      showToast(`已屏蔽 ${item.repo_name}`);
      closeDrawer();
    });
  }

  async function loadReadmePreview(item) {
    const btn = document.getElementById("btnLoadReadme");
    const box = document.getElementById("readmeContent");
    if (!btn || !box) return;

    btn.disabled = true;
    btn.textContent = "拉取中…";
    try {
      const resp = await fetch(`https://api.github.com/repos/${item.repo_name}/readme`, {
        headers: { Accept: "application/vnd.github.raw+json" },
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const md = await resp.text();
      const rendered = renderSafeMarkdown(md.slice(0, 12000), item.repo_name);
      state.readmeCache.set(item.key, rendered);
      if (state.drawerKey === item.key) {
        box.innerHTML = rendered;
        btn.textContent = "已加载";
      }
    } catch (err) {
      if (state.drawerKey === item.key) {
        box.innerHTML = `<p style="color:var(--ink-muted)">在线拉取失败（${escapeHtml(err.message)}，可能是 GitHub 匿名接口限流或网络阻断），可直接点击下方「前往 GitHub」查看完整文档。</p>`;
        btn.disabled = false;
        btn.textContent = "重试预览";
      }
    }
  }

  function renderSafeMarkdown(md, repoName) {
    const lines = md.replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let inCode = false;
    let codeBuf = [];
    let inList = false;

    const flushList = () => {
      if (inList) {
        out.push("</ul>");
        inList = false;
      }
    };

    const inlineFormat = (raw) => {
      let s = escapeHtml(raw);
      s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
      s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, src) => {
        const cleanUrl = resolveGithubAssetUrl(src, repoName);
        if (!cleanUrl) return "";
        return `<img alt="${alt}" src="${escapeHtml(cleanUrl)}" loading="lazy" />`;
      });
      s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      return s;
    };

    for (const line of lines) {
      if (line.trim().startsWith("```")) {
        flushList();
        if (inCode) {
          out.push(`<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
          codeBuf = [];
          inCode = false;
        } else {
          inCode = true;
        }
        continue;
      }
      if (inCode) {
        codeBuf.push(line);
        continue;
      }

      const trimmed = line.trim();
      if (!trimmed) {
        flushList();
        continue;
      }
      if (/^<\/?[a-zA-Z]/.test(trimmed)) {
        continue;
      }
      const hMatch = trimmed.match(/^(#{1,3})\s+(.+)$/);
      if (hMatch) {
        flushList();
        const lvl = hMatch[1].length;
        out.push(`<h${lvl}>${inlineFormat(hMatch[2])}</h${lvl}>`);
        continue;
      }
      const liMatch = trimmed.match(/^[-*+]\s+(.+)$/);
      if (liMatch) {
        if (!inList) {
          out.push("<ul>");
          inList = true;
        }
        out.push(`<li>${inlineFormat(liMatch[1])}</li>`);
        continue;
      }
      flushList();
      out.push(`<p>${inlineFormat(trimmed)}</p>`);
    }
    flushList();
    if (inCode && codeBuf.length) {
      out.push(`<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
    }
    return out.join("\n") || "<p>README 内容为空或全部为 HTML 标签。</p>";
  }

  function resolveGithubAssetUrl(src, repoName) {
    if (/^https?:\/\//i.test(src)) return src;
    if (src.startsWith("#") || src.startsWith("data:")) return "";
    const clean = src.replace(/^\.?\//, "");
    return `https://raw.githubusercontent.com/${repoName}/HEAD/${clean}`;
  }

  /* ---------- Actions ---------- */
  function toggleSaved(key) {
    if (state.saved.has(key)) {
      state.saved.delete(key);
      showToast("已取消收藏");
    } else {
      state.saved.add(key);
      showToast("已加入收藏");
    }
    saveLocalPrefs();
  }

  function toggleRead(key) {
    if (state.read.has(key)) {
      state.read.delete(key);
      showToast("已标为未读");
    } else {
      state.read.add(key);
      showToast("已标为已读");
    }
    saveLocalPrefs();
  }

  function hideRepo(key) {
    state.hidden.add(key);
    saveLocalPrefs();
    showToast("已隐藏该项目（可在右上角「RSS & 管理」恢复）");
  }

  function resetAllFilters() {
    state.category = "";
    state.language = "";
    state.search = "";
    state.multiOnly = false;
    state.hideRead = false;
    els.searchInput.value = "";
    els.langSelect.value = "";
    els.btnMultiSource.setAttribute("aria-pressed", "false");
    els.btnHideRead.setAttribute("aria-pressed", "false");
    saveLocalPrefs();
    renderCards();
  }

  /* ---------- RSS & Management Modal ---------- */
  function openRssModal() {
    const feeds = [
      { name: "全源聚合精选", rel: "./feeds/all_discoveries.xml" },
      { name: "GitHub Trending（全语言精选）", rel: "./feeds/github_trending.xml" },
      { name: "GitHub 定向雷达 & Release 追踪", rel: "./feeds/github_radar.xml" },
      { name: "开源社区发现（Show HN / HelloGitHub / HuggingFace）", rel: "./feeds/community_picks.xml" },
    ];
    els.rssList.innerHTML = feeds
      .map((f) => {
        const full = new URL(f.rel, window.location.href).href;
        return `
          <div class="rss-row">
            <div class="rss-row-info">
              <div class="rss-row-name">${escapeHtml(f.name)}</div>
              <div class="rss-row-url">${escapeHtml(full)}</div>
            </div>
            <button type="button" class="btn-outline small" data-copy-url="${escapeHtml(full)}">复制</button>
          </div>
        `;
      })
      .join("");

    els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 ${state.read.size} · 已屏蔽 ${state.hidden.size}`;
    els.rssBackdrop.hidden = false;
  }

  function closeRssModal() {
    els.rssBackdrop.hidden = true;
  }

  /* ---------- Daily Digest Modal ---------- */
  function buildMarkdownDigest(items, title) {
    const today = state.status.last_run_date || getLocalDateKey(new Date().toISOString());
    const lines = [`# ${title} (${today})`, ""];
    items.forEach((it, idx) => {
      const tags = [];
      if ((it.sources || []).length >= 2) tags.push(`多源×${it.sources.length}`);
      if (it.category_tag) tags.push(it.category_tag);
      if (it.language) tags.push(it.language);
      let starPart = `★${(it.stars || 0).toLocaleString()}`;
      if ((it.stars_delta_24h || 0) > 0) starPart += ` (+${it.stars_delta_24h.toLocaleString()})`;
      tags.push(starPart);
      const summary = it.ai_highlight || it.desc_cn || it.description || "";
      lines.push(`${idx + 1}. **[${it.repo_name}](${it.url})** · ${tags.join(" · ")}`);
      if (summary) lines.push(`   > ${summary}`);
    });
    return lines.join("\n");
  }

  function openDigestModal() {
    const filtered = getFilteredItems();
    const topItems = [...filtered]
      .sort((a, b) => {
        const ms = (b.sources || []).length - (a.sources || []).length;
        if (ms !== 0) return ms;
        const dd = (b.stars_delta_24h || 0) - (a.stars_delta_24h || 0);
        if (dd !== 0) return dd;
        return (b.stars || 0) - (a.stars || 0);
      })
      .slice(0, 12);

    els.digestSubtitle.textContent = `已按多源共振、24h 增速与当前筛选整理前 ${topItems.length} 项（共匹配 ${filtered.length} 项），可直接复制或下载：`;
    els.digestTextarea.value = buildMarkdownDigest(topItems, "GitHub Radar 每日开源精选");
    els.digestBackdrop.hidden = false;
  }

  function closeDigestModal() {
    els.digestBackdrop.hidden = true;
  }

  /* ---------- Event Wiring ---------- */
  function bindEvents() {
    els.btnTheme.addEventListener("click", toggleTheme);

    els.bucketTabs.addEventListener("click", (e) => {
      const btn = e.target.closest(".tab-btn");
      if (!btn) return;
      state.bucket = btn.dataset.bucket || "all";
      state.category = "";
      els.bucketTabs.querySelectorAll(".tab-btn").forEach((b) => {
        const active = b === btn;
        b.classList.toggle("is-active", active);
        b.setAttribute("aria-selected", String(active));
      });
      renderCards();
    });

    els.categoryRibbon.addEventListener("click", (e) => {
      const chip = e.target.closest(".cat-chip");
      if (!chip) return;
      const cat = chip.dataset.cat || "";
      state.category = state.category === cat ? "" : cat;
      renderCards();
    });

    els.searchInput.addEventListener("input", (e) => {
      state.search = e.target.value;
      renderCards();
    });

    els.langSelect.addEventListener("change", (e) => {
      state.language = e.target.value;
      renderCards();
    });

    els.sortSelect.addEventListener("change", (e) => {
      state.sort = e.target.value;
      renderCards();
    });

    els.btnMultiSource.addEventListener("click", () => {
      state.multiOnly = !state.multiOnly;
      els.btnMultiSource.setAttribute("aria-pressed", String(state.multiOnly));
      renderCards();
    });

    els.btnHideRead.addEventListener("click", () => {
      state.hideRead = !state.hideRead;
      els.btnHideRead.setAttribute("aria-pressed", String(state.hideRead));
      saveLocalPrefs();
      renderCards();
    });

    els.btnResetFilters.addEventListener("click", resetAllFilters);
    els.btnEmptyReset.addEventListener("click", () => {
      state.bucket = "all";
      els.bucketTabs.querySelectorAll(".tab-btn").forEach((b) => {
        const active = b.dataset.bucket === "all";
        b.classList.toggle("is-active", active);
        b.setAttribute("aria-selected", String(active));
      });
      resetAllFilters();
    });

    els.cardGrid.addEventListener("click", (e) => {
      const actionEl = e.target.closest("[data-action]");
      if (actionEl) {
        const action = actionEl.dataset.action;
        if (action === "link") {
          const card = e.target.closest(".repo-card");
          if (card && card.dataset.key) {
            state.read.add(card.dataset.key);
            saveLocalPrefs();
            card.classList.add("is-read");
          }
          return;
        }
        e.stopPropagation();
        const key = actionEl.dataset.key;
        if (action === "save") toggleSaved(key);
        else if (action === "read") toggleRead(key);
        else if (action === "hide") hideRepo(key);
        renderCards();
        return;
      }

      const card = e.target.closest(".repo-card");
      if (card && card.dataset.key) {
        openDrawer(card.dataset.key);
      }
    });

    els.cardGrid.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.classList.contains("repo-card")) {
        openDrawer(e.target.dataset.key);
      }
    });

    els.drawerBackdrop.addEventListener("click", closeDrawer);
    els.btnCloseDrawer.addEventListener("click", closeDrawer);

    els.btnPrevItem.addEventListener("click", () => {
      const idx = state.drawerListKeys.indexOf(state.drawerKey);
      if (idx > 0) showDrawerItem(state.drawerListKeys[idx - 1]);
    });

    els.btnNextItem.addEventListener("click", () => {
      const idx = state.drawerListKeys.indexOf(state.drawerKey);
      if (idx >= 0 && idx < state.drawerListKeys.length - 1) {
        showDrawerItem(state.drawerListKeys[idx + 1]);
      }
    });

    els.btnRssModal.addEventListener("click", openRssModal);
    els.btnCloseRss.addEventListener("click", closeRssModal);
    els.rssBackdrop.addEventListener("click", (e) => {
      if (e.target === els.rssBackdrop) closeRssModal();
    });

    els.rssList.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-copy-url]");
      if (!btn) return;
      copyText(btn.dataset.copyUrl, "已复制 RSS 订阅地址");
    });

    els.btnClearHidden.addEventListener("click", () => {
      state.hidden.clear();
      saveLocalPrefs();
      populateLanguages();
      renderCards();
      els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 ${state.read.size} · 已屏蔽 0`;
      showToast("已恢复全部屏蔽项目");
    });

    els.btnClearRead.addEventListener("click", () => {
      state.read.clear();
      saveLocalPrefs();
      renderCards();
      els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 0 · 已屏蔽 ${state.hidden.size}`;
      showToast("已清空全部已读标记");
    });

    els.btnExportSavedMd.addEventListener("click", () => {
      const savedItems = state.items.filter((it) => state.saved.has(it.key));
      if (!savedItems.length) {
        showToast("当前还没有收藏任何项目");
        return;
      }
      const md = buildMarkdownDigest(savedItems, "GitHub Radar 我的开源收藏");
      downloadTextFile(`github-radar-saved-${getLocalDateKey(new Date().toISOString())}.md`, md, "text/markdown;charset=utf-8");
      showToast(`已导出 ${savedItems.length} 个收藏项目为 Markdown`);
    });

    els.btnExportBackup.addEventListener("click", () => {
      const payload = {
        exported_at: new Date().toISOString(),
        saved: [...state.saved],
        read: [...state.read],
        hidden: [...state.hidden],
        hideRead: state.hideRead,
        theme: state.theme,
      };
      downloadTextFile(
        `github-radar-backup-${getLocalDateKey(new Date().toISOString())}.json`,
        JSON.stringify(payload, null, 2),
        "application/json;charset=utf-8"
      );
      showToast("已导出本地阅读记录备份");
    });

    els.btnImportBackup.addEventListener("click", () => {
      els.importBackupInput.click();
    });

    els.importBackupInput.addEventListener("change", async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      try {
        const parsed = JSON.parse(await file.text());
        if (Array.isArray(parsed.saved)) state.saved = new Set(parsed.saved);
        if (Array.isArray(parsed.read)) state.read = new Set(parsed.read);
        if (Array.isArray(parsed.hidden)) state.hidden = new Set(parsed.hidden);
        if (typeof parsed.hideRead === "boolean") state.hideRead = parsed.hideRead;
        if (parsed.theme === "dark" || parsed.theme === "light") state.theme = parsed.theme;
        applyTheme();
        saveLocalPrefs();
        populateLanguages();
        renderCards();
        els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 ${state.read.size} · 已屏蔽 ${state.hidden.size}`;
        showToast("已恢复本地备份记录");
      } catch {
        showToast("备份文件格式无效，导入失败");
      } finally {
        els.importBackupInput.value = "";
      }
    });

    els.btnDigestModal.addEventListener("click", openDigestModal);
    els.btnCloseDigest.addEventListener("click", closeDigestModal);
    els.digestBackdrop.addEventListener("click", (e) => {
      if (e.target === els.digestBackdrop) closeDigestModal();
    });
    els.btnCopyDigest.addEventListener("click", () => {
      copyText(els.digestTextarea.value, "已复制今日精选 Markdown 简报");
    });
    els.btnDownloadDigest.addEventListener("click", () => {
      const day = state.status.last_run_date || getLocalDateKey(new Date().toISOString());
      downloadTextFile(`github-radar-digest-${day}.md`, els.digestTextarea.value, "text/markdown;charset=utf-8");
      showToast("已下载简报 Markdown 文件");
    });

    document.addEventListener("keydown", (e) => {
      const tag = (e.target && e.target.tagName) || "";
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

      if (e.key === "Escape") {
        if (!els.digestBackdrop.hidden) closeDigestModal();
        else if (!els.rssBackdrop.hidden) closeRssModal();
        else if (state.drawerKey) closeDrawer();
        else if (document.activeElement === els.searchInput) els.searchInput.blur();
        return;
      }

      if (typing) return;

      if (e.key === "/") {
        e.preventDefault();
        els.searchInput.focus();
        els.searchInput.select();
      } else if (e.key === "d" || e.key === "D") {
        toggleTheme();
      } else if (state.drawerKey && e.key === "ArrowLeft") {
        e.preventDefault();
        els.btnPrevItem.click();
      } else if (state.drawerKey && e.key === "ArrowRight") {
        e.preventDefault();
        els.btnNextItem.click();
      } else if (state.drawerKey && (e.key === "s" || e.key === "S")) {
        toggleSaved(state.drawerKey);
        showDrawerItem(state.drawerKey);
        updateTabCounts();
      }
    });
  }

  /* ---------- Bootstrap ---------- */
  async function init() {
    loadLocalPrefs();
    bindEvents();

    try {
      const resp = await fetch(`./feeds/data.json?t=${Date.now()}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      state.items = Array.isArray(data.items) ? data.items : [];
      state.status = data.status || {};

      const updated = formatFullTime(state.status.last_run_at || data.generated_at);
      const newCount = Number(state.status.new_count || 0);
      const newBadge = newCount > 0 ? ` · 今日新增 ${newCount}` : "";
      els.issueMeta.textContent = `收录 ${state.items.length} 项${newBadge} · 更新于 ${updated || "刚刚"}`;

      populateLanguages();
      renderCards();
    } catch (err) {
      els.issueMeta.textContent = "数据加载失败";
      els.cardGrid.innerHTML = "";
      els.emptyState.hidden = false;
      els.emptyTitle.textContent = "未能读取项目数据";
      els.emptyDesc.textContent = `请确认 /feeds/data.json 已生成（${err.message}）。`;
    }
  }

  init();
})();
