(() => {
  "use strict";

  const STORAGE_KEY = "github_radar_reader_v1";
  const SAFE_REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  const VALID_BUCKETS = new Set(["all", "trending", "radar", "community", "saved"]);
  const VALID_SORTS = new Set(["newest", "delta", "breakout", "stars"]);

  const LANG_COLORS = {
    Python: "#3572A5",
    TypeScript: "#3178C6",
    JavaScript: "#D9B62A",
    Rust: "#CE422B",
    Go: "#00ADD8",
    C: "#6E7887",
    "C++": "#F34B7D",
    "C#": "#178600",
    Java: "#B07219",
    Swift: "#F05138",
    Kotlin: "#A97BFF",
    HTML: "#E34C26",
    CSS: "#6F4E9E",
    Shell: "#4E9A06",
    PowerShell: "#2B6CB0",
    Ruby: "#A62629",
    PHP: "#5E6FB2",
    Dart: "#00B4AB",
    Vue: "#41B883",
    Zig: "#EC915C",
    Lua: "#3B5BDB",
    TeX: "#3D6117",
  };

  const BUCKET_META = {
    trending: { label: "Trending", cls: "bucket-trending" },
    radar: { label: "黑马雷达", cls: "bucket-radar" },
    community: { label: "社区发现", cls: "bucket-community" },
  };

  const CHANNEL_DEFS = {
    github_trending: {
      label: "Trending",
      match: (s) => s.startsWith("GitHub Trending"),
    },
    github_search: {
      label: "雷达检索",
      match: (s) => s.startsWith("雷达:") || s === "GitHub Search",
    },
    hackernews_show: {
      label: "Show HN",
      match: (s) => s === "Show HN",
    },
    hellogithub: {
      label: "HelloGitHub",
      match: (s) => s === "HelloGitHub",
    },
    huggingface_papers: {
      label: "HF 论文",
      match: (s) => s === "HuggingFace Papers",
    },
    github_releases: {
      label: "版本发布",
      match: (s) => s === "GitHub Releases",
    },
    reddit_rss: {
      label: "Reddit / RSS",
      match: (s) =>
        s.startsWith("Reddit:") ||
        s === "自定义RSS" ||
        s.startsWith("自定义RSS:") ||
        s.startsWith("RSS:"),
    },
    starred_users: {
      label: "关注开发者",
      match: (s) => s.startsWith("关注开发者:"),
    },
  };

  const CHANNEL_ALIASES = {
    starred_developers: "starred_users",
  };

  function normalizeChannelKey(raw) {
    const key = String(raw || "").trim();
    return CHANNEL_ALIASES[key] || key;
  }

  const state = {
    items: [],
    status: {},
    feedsBase: "./feeds",
    llmsHref: "./llms.txt",
    bucket: "all",
    category: "",
    language: "",
    sourceChannel: "",
    search: "",
    sort: "newest",
    multiOnly: false,
    hideRead: false,
    mobileFiltersOpen: false,
    saved: new Set(),
    read: new Set(),
    hidden: new Set(),
    theme: "light",
    drawerKey: null,
    drawerListKeys: [],
    returnFocusEl: null,
    returnFocusKey: null,
    readmeCache: new Map(),
  };

  let suppressUrlSync = false;
  let toastTimer = null;

  const els = {
    sheetFrame: document.querySelector(".sheet-frame"),
    commandMatrix: document.querySelector(".command-matrix"),
    rssAltLink: document.getElementById("rssAltLink"),
    jsonAltLink: document.getElementById("jsonAltLink"),
    issueMeta: document.getElementById("issueMeta"),
    sourceHealthPill: document.getElementById("sourceHealthPill"),
    mobileSourceStrip: document.getElementById("mobileSourceStrip"),
    rivalryKeptVal: document.getElementById("rivalryKeptVal"),
    rivalryKeptCaption: document.getElementById("rivalryKeptCaption"),
    rivalryDroppedVal: document.getElementById("rivalryDroppedVal"),
    rivalryDroppedCaption: document.getElementById("rivalryDroppedCaption"),
    noiseRivalBlock: document.querySelector(".rival-block.is-noise"),
    rivalryNewBadge: document.getElementById("rivalryNewBadge"),
    rivalryRawMeta: document.getElementById("rivalryRawMeta"),
    rivalryVernier: document.getElementById("rivalryVernier"),
    editorialBrief: document.getElementById("editorialBrief"),
    editorialBriefText: document.getElementById("editorialBriefText"),
    bucketTabs: document.getElementById("bucketTabs"),
    searchInput: document.getElementById("searchInput"),
    btnMobileFilters: document.getElementById("btnMobileFilters"),
    mobileFilterBadge: document.getElementById("mobileFilterBadge"),
    langSelect: document.getElementById("langSelect"),
    sortSelect: document.getElementById("sortSelect"),
    btnMultiSource: document.getElementById("btnMultiSource"),
    btnHideRead: document.getElementById("btnHideRead"),
    categoryRibbon: document.getElementById("categoryRibbon"),
    filterStatus: document.getElementById("filterStatus"),
    filterStatusText: document.getElementById("filterStatusText"),
    btnCopyFilterUrl: document.getElementById("btnCopyFilterUrl"),
    btnResetFilters: document.getElementById("btnResetFilters"),
    signalStream: document.getElementById("signalStream"),
    emptyState: document.getElementById("emptyState"),
    emptyTitle: document.getElementById("emptyTitle"),
    emptyDesc: document.getElementById("emptyDesc"),
    btnEmptyReset: document.getElementById("btnEmptyReset"),
    footerLlmsLink: document.getElementById("footerLlmsLink"),
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

  function safeHttpUrl(raw) {
    if (!raw) return "";
    try {
      const u = new URL(String(raw).trim());
      if (u.protocol === "http:" || u.protocol === "https:") {
        return u.href;
      }
    } catch {
      // ignore invalid URL
    }
    return "";
  }

  function padRank(n) {
    return String(n).padStart(2, "0");
  }

  function formatStars(n) {
    const num = Number(n) || 0;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}k`;
    return String(num);
  }

  function formatBreakout(score) {
    const val = Number(score) || 0;
    if (val >= 100) return `${Math.round(val)}`;
    return val.toFixed(1);
  }

  const dtfCache = new Map();

  function getBizTimeZone() {
    return (
      String((state.status && state.status.timezone) || "Asia/Shanghai").trim() ||
      "Asia/Shanghai"
    );
  }

  function getBizFormatter(tz) {
    if (!dtfCache.has(tz)) {
      dtfCache.set(
        tz,
        new Intl.DateTimeFormat("en-CA", {
          timeZone: tz,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        })
      );
    }
    return dtfCache.get(tz);
  }

  function parseBizParts(iso) {
    const d = iso instanceof Date ? iso : new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    try {
      const parts = getBizFormatter(getBizTimeZone()).formatToParts(d);
      const map = {};
      for (const p of parts) {
        if (p.type !== "literal") map[p.type] = p.value;
      }
      if (map.year && map.month && map.day) {
        const hh = map.hour === "24" ? "00" : map.hour || "00";
        return {
          yyyy: map.year,
          mm: map.month,
          dd: map.day,
          hh,
          mi: map.minute || "00",
        };
      }
    } catch {
      // Fallback to visitor local clock if custom timezone identifier is unrecognized
    }
    return {
      yyyy: String(d.getFullYear()),
      mm: String(d.getMonth() + 1).padStart(2, "0"),
      dd: String(d.getDate()).padStart(2, "0"),
      hh: String(d.getHours()).padStart(2, "0"),
      mi: String(d.getMinutes()).padStart(2, "0"),
    };
  }

  function formatShortDate(iso) {
    if (!iso) return "--/--";
    const parts = parseBizParts(iso);
    if (!parts) return String(iso).slice(5, 10) || "--/--";
    return `${parts.mm}-${parts.dd}`;
  }

  function getLocalDateKey(iso) {
    if (!iso) return "未知日期";
    const parts = parseBizParts(iso);
    if (!parts) return String(iso).slice(0, 10) || "未知日期";
    return `${parts.yyyy}-${parts.mm}-${parts.dd}`;
  }

  function formatDateSectionLabel(dateKey) {
    if (!dateKey || dateKey === "未知日期") return "早期归档";
    const now = new Date();
    const todayKey = getLocalDateKey(now);
    const yestKey = getLocalDateKey(new Date(now.getTime() - 86400000));
    if (dateKey === todayKey) return `${dateKey} · 今日收录`;
    if (dateKey === yestKey) return `${dateKey} · 昨日收录`;
    return `${dateKey} · 往期归档`;
  }

  function formatFullTime(iso) {
    if (!iso) return "";
    const parts = parseBizParts(iso);
    if (!parts) return String(iso);
    return `${parts.yyyy}-${parts.mm}-${parts.dd} ${parts.hh}:${parts.mi}`;
  }

  function itemMatchesChannel(item, channelKey) {
    const normKey = normalizeChannelKey(channelKey);
    if (!normKey) return true;
    const def = CHANNEL_DEFS[normKey];
    const srcs = Array.isArray(item.sources) ? item.sources : [];
    if (def) {
      return srcs.some((s) => def.match(String(s || "")));
    }
    const low = normKey.toLowerCase();
    return srcs.some((s) => String(s || "").toLowerCase().includes(low));
  }

  function getChannelDisplayName(channelKey) {
    const normKey = normalizeChannelKey(channelKey);
    return (CHANNEL_DEFS[normKey] && CHANNEL_DEFS[normKey].label) || normKey;
  }

  function renderSparklineSvg(points, { width = 62, height = 18 } = {}) {
    if (!Array.isArray(points) || points.length < 2) return "";
    const nums = points.map((n) => Number(n) || 0);
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const isFlat = max === min;
    const padX = 2;
    const padY = 2.5;
    const innerW = width - padX * 2;
    const innerH = height - padY * 2;
    const span = max - min || 1;

    const coords = nums.map((val, idx) => {
      const x = padX + (idx / (nums.length - 1)) * innerW;
      const y = isFlat
        ? height / 2
        : padY + innerH - ((val - min) / span) * innerH;
      return [Number(x.toFixed(1)), Number(y.toFixed(1))];
    });

    const linePts = coords.map(([x, y]) => `${x},${y}`).join(" ");
    const [lastX, lastY] = coords[coords.length - 1];
    const areaPts = `${coords[0][0]},${height - 1} ${linePts} ${lastX},${height - 1}`;
    const tip = `7 日星标轨迹：${nums[0].toLocaleString()} → ${nums[nums.length - 1].toLocaleString()}`;

    return `
      <span class="sparkline-wrap ${isFlat ? "is-flat" : ""}" title="${escapeHtml(tip)}" aria-hidden="true">
        <svg class="sparkline-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
          ${!isFlat ? `<polygon points="${areaPts}" fill="currentColor" fill-opacity="0.14"></polygon>` : ""}
          <polyline points="${linePts}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"></polyline>
          <rect x="${(lastX - 1.8).toFixed(1)}" y="${(lastY - 1.8).toFixed(1)}" width="3.6" height="3.6" fill="currentColor"></rect>
        </svg>
      </span>
    `;
  }

  function renderVernierSvg(kept, dropped) {
    const hasSignal = kept > 0 || dropped > 0;
    const total = Math.max(1, kept + dropped);
    const ticks = 36;
    const keptTicks = !hasSignal
      ? 0
      : dropped === 0
        ? ticks
        : Math.max(1, Math.min(ticks - 1, Math.round((kept / total) * ticks)));
    const w = 360;
    const h = 24;
    const step = w / ticks;
    const parts = [];

    parts.push(`<line x1="0" y1="22" x2="${w}" y2="22" stroke="var(--rule-strong)" stroke-width="1" />`);

    for (let i = 0; i < ticks; i += 1) {
      const x = Number((i * step + step / 2).toFixed(1));
      const isMajor = i % 6 === 0;
      if (i < keptTicks) {
        const y1 = isMajor ? 2 : 6;
        parts.push(`<line x1="${x}" y1="${y1}" x2="${x}" y2="21" stroke="var(--cobalt)" stroke-width="${isMajor ? "2" : "1.35"}" />`);
      } else {
        const y1 = isMajor ? 10 : 13;
        parts.push(`<line x1="${x}" y1="${y1}" x2="${x}" y2="21" stroke="var(--rule-strong)" stroke-width="1.15" />`);
      }
    }

    if (hasSignal && dropped > 0) {
      const splitX = Number((keptTicks * step).toFixed(1));
      parts.push(`<line x1="${splitX}" y1="0" x2="${splitX}" y2="24" stroke="var(--ink)" stroke-width="2" />`);
      parts.push(`<polygon points="${splitX - 3.5},0 ${splitX + 3.5},0 ${splitX},5" fill="var(--ink)" />`);
    }

    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="留存与拦截比例尺">${parts.join("")}</svg>`;
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

  /* ---------- URL Deep-Link State Sync ---------- */
  function buildShareableUrl() {
    const u = new URL(window.location.href);
    const p = new URLSearchParams();
    if (state.bucket && state.bucket !== "all") p.set("bucket", state.bucket);
    if (state.category) p.set("cat", state.category);
    if (state.language) p.set("lang", state.language);
    if (state.sourceChannel) p.set("source", state.sourceChannel);
    if (state.search.trim()) p.set("q", state.search.trim());
    if (state.sort && state.sort !== "newest") p.set("sort", state.sort);
    if (state.multiOnly) p.set("multi", "1");
    if (state.drawerKey) {
      const openItem = getItemByKey(state.drawerKey);
      p.set("repo", openItem ? openItem.repo_name : state.drawerKey);
    }
    const qs = p.toString();
    u.search = qs ? `?${qs}` : "";
    return u.toString();
  }

  function syncUrlState() {
    if (suppressUrlSync) return;
    try {
      const nextUrl = buildShareableUrl();
      if (nextUrl !== window.location.href) {
        window.history.replaceState(null, "", nextUrl);
      }
    } catch {
      // ignore file:// or restricted history errors
    }
  }

  function syncControlsFromState() {
    els.bucketTabs.querySelectorAll(".idx-tab").forEach((b) => {
      const active = (b.dataset.bucket || "all") === state.bucket;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-selected", String(active));
    });
    els.searchInput.value = state.search;
    els.langSelect.value = state.language;
    els.sortSelect.value = state.sort;
    els.btnMultiSource.setAttribute("aria-pressed", String(state.multiOnly));
    els.btnHideRead.setAttribute("aria-pressed", String(state.hideRead));

    document.querySelectorAll("button.chan-cell[data-source]").forEach((btn) => {
      const active = btn.dataset.source === state.sourceChannel;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", String(active));
    });

    updateMobileFilterTrigger();
  }

  function updateMobileFilterTrigger() {
    if (!els.btnMobileFilters || !els.mobileFilterBadge) return;
    let activeCount = 0;
    if (state.category) activeCount += 1;
    if (state.language) activeCount += 1;
    if (state.sourceChannel) activeCount += 1;
    if (state.sort !== "newest") activeCount += 1;
    if (state.multiOnly) activeCount += 1;
    if (state.hideRead && state.bucket !== "saved") activeCount += 1;

    els.btnMobileFilters.setAttribute("aria-expanded", String(state.mobileFiltersOpen));
    els.btnMobileFilters.classList.toggle("has-active", activeCount > 0);
    if (activeCount > 0) {
      els.mobileFilterBadge.textContent = String(activeCount);
      els.mobileFilterBadge.hidden = false;
    } else {
      els.mobileFilterBadge.hidden = true;
    }
    if (els.commandMatrix) {
      els.commandMatrix.classList.toggle("is-filters-open", state.mobileFiltersOpen);
    }
  }

  function applyUrlStateToView() {
    suppressUrlSync = true;
    try {
      const params = new URLSearchParams(window.location.search);
      const b = params.get("bucket") || "all";
      state.bucket = VALID_BUCKETS.has(b) ? b : "all";
      state.category = params.get("cat") || "";
      state.language = params.get("lang") || "";
      state.sourceChannel = normalizeChannelKey(params.get("source") || "");
      state.search = params.get("q") || "";
      const srt = params.get("sort") || "newest";
      state.sort = VALID_SORTS.has(srt) ? srt : "newest";
      state.multiOnly = params.get("multi") === "1" || params.get("multi") === "true";

      populateLanguages();
      renderChannelArrays();
      syncControlsFromState();
      renderStream();

      const repoParam = (params.get("repo") || "").trim().toLowerCase();
      if (repoParam) {
        const target = getItemByKey(repoParam);
        if (target) {
          openDrawer(target.key);
        }
      } else if (state.drawerKey) {
        closeDrawer();
      }
    } finally {
      suppressUrlSync = false;
    }
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
    if (els.themeIconSun) els.themeIconSun.toggleAttribute("hidden", isDark);
    if (els.themeIconMoon) els.themeIconMoon.toggleAttribute("hidden", !isDark);
  }

  function toggleTheme() {
    state.theme = state.theme === "dark" ? "light" : "dark";
    applyTheme();
    saveLocalPrefs();
  }

  function renderChannelArrays() {
    const health = state.status.source_health;
    const visible = getVisibleBaseItems();

    let entries = [];
    if (health && typeof health === "object" && Object.keys(health).length > 0) {
      entries = Object.entries(health);
    } else if (visible.length > 0) {
      // Synthesize channel buttons from actual item sources if source_health is empty
      for (const key of Object.keys(CHANNEL_DEFS)) {
        const cnt = visible.filter((it) => itemMatchesChannel(it, key)).length;
        if (cnt > 0) entries.push([key, { ok: true, count: cnt }]);
      }
    }

    if (entries.length === 0) {
      if (els.sourceHealthPill) els.sourceHealthPill.hidden = true;
      if (els.mobileSourceStrip) els.mobileSourceStrip.hidden = true;
      return;
    }

    const html = entries
      .map(([rawKey, val]) => {
        const key = normalizeChannelKey(rawKey);
        const ok = Boolean(val && val.ok);
        const label = getChannelDisplayName(key);
        const rawCount = val && val.count != null ? Number(val.count) : 0;
        const keptCount = visible.filter((it) => itemMatchesChannel(it, key)).length;
        const isActive = state.sourceChannel === key;

        if (!ok && keptCount === 0 && !isActive) {
          const errTip = `${label}：采集异常（${val?.error || "连接受限"}）`;
          return `<span class="chan-cell is-offline" title="${escapeHtml(errTip)}"><span class="chan-dot" aria-hidden="true"></span><span>${escapeHtml(label)}</span><span class="chan-num">离线</span></span>`;
        }

        const durPart = val?.duration_ms ? ` · ${val.duration_ms}ms` : "";
        const tip =
          rawCount > 0
            ? `${label}：本轮抓取 ${rawCount} 仓 · 当前在榜 ${keptCount} 仓${durPart}（点击筛选）`
            : `${label}：当前在榜 ${keptCount} 仓${durPart}（点击筛选）`;

        return `<button type="button" class="chan-cell ${isActive ? "is-active" : ""}" data-source="${escapeHtml(key)}" aria-pressed="${String(isActive)}" title="${escapeHtml(tip)}"><span class="chan-dot" aria-hidden="true"></span><span>${escapeHtml(label)}</span><span class="chan-num">${keptCount}</span></button>`;
      })
      .join("");

    if (els.sourceHealthPill) {
      els.sourceHealthPill.innerHTML = html;
      els.sourceHealthPill.hidden = false;
    }
    if (els.mobileSourceStrip) {
      els.mobileSourceStrip.innerHTML = html;
      els.mobileSourceStrip.hidden = false;
    }
  }

  function renderTelemetryAndBrief() {
    const totalKept = state.items.length;
    const dropped = Math.max(0, Number(state.status.dropped_count) || 0);
    const rawCount = Math.max(0, Number(state.status.raw_count) || 0);
    const uniqueCount = Math.max(0, Number(state.status.unique_count) || 0);
    const explicitRunKept =
      state.status.run_kept_count != null
        ? Math.max(0, Number(state.status.run_kept_count) || 0)
        : null;
    const hasRunActivity =
      (explicitRunKept !== null && (explicitRunKept > 0 || dropped > 0)) ||
      uniqueCount > 0 ||
      rawCount > 0;
    const hasRunStats = hasRunActivity || (explicitRunKept !== null && totalKept === 0);

    let runKept = totalKept;
    if (explicitRunKept !== null) {
      runKept = explicitRunKept;
    } else if (uniqueCount > 0) {
      runKept = Math.max(0, uniqueCount - dropped);
    } else if (rawCount > 0) {
      runKept = Math.max(0, rawCount - dropped);
    }

    const runEvaluated = hasRunStats ? Math.max(runKept + dropped, uniqueCount) : totalKept + dropped;
    const newCount = Math.max(0, Number(state.status.new_count) || 0);
    const purity = runEvaluated > 0 ? Math.round((runKept / runEvaluated) * 100) : 100;
    const gaugeKept = hasRunStats ? runKept : totalKept;

    if (els.rivalryKeptVal) els.rivalryKeptVal.textContent = String(gaugeKept);
    if (els.rivalryKeptCaption) {
      els.rivalryKeptCaption.textContent = hasRunStats ? "本轮留存信号" : "在榜真信号";
    }
    if (els.rivalryDroppedVal) els.rivalryDroppedVal.textContent = String(dropped);
    if (els.rivalryDroppedCaption) {
      els.rivalryDroppedCaption.textContent = hasRunStats ? "本轮拦截噪音" : "拦截刷星与噪音";
    }
    if (els.noiseRivalBlock) {
      els.noiseRivalBlock.classList.toggle("no-strike", dropped === 0);
    }
    if (els.rivalryNewBadge) {
      if (newCount > 0) {
        els.rivalryNewBadge.textContent = `+${newCount} 今日新入`;
        els.rivalryNewBadge.hidden = false;
      } else {
        els.rivalryNewBadge.hidden = true;
      }
    }
    if (els.rivalryRawMeta) {
      if (hasRunStats && (dropped > 0 || rawCount > 0 || uniqueCount > 0)) {
        const captureBase = Math.max(rawCount, runEvaluated);
        const dedupNote =
          captureBase > runEvaluated && runEvaluated > 0 ? `（去重 ${runEvaluated}）` : "";
        const poolNote = totalKept !== runKept ? ` · 累计在榜 ${totalKept}` : "";
        els.rivalryRawMeta.textContent = `本轮捕获 ${captureBase} 仓${dedupNote} · 留存率 ${purity}%${poolNote}`;
      } else {
        els.rivalryRawMeta.textContent = `当前在榜 ${totalKept} 仓有效信号`;
      }
    }
    if (els.rivalryVernier) {
      els.rivalryVernier.innerHTML = renderVernierSvg(gaugeKept, dropped);
    }

    const summary = String(state.status.trend_summary || "").trim();
    if (summary && els.editorialBrief && els.editorialBriefText) {
      els.editorialBriefText.textContent = summary;
      els.editorialBrief.hidden = false;
    } else if (els.editorialBrief) {
      els.editorialBrief.hidden = true;
    }

    renderChannelArrays();
  }

  function renderTelemetryFailure() {
    if (els.rivalryKeptVal) els.rivalryKeptVal.textContent = "0";
    if (els.rivalryDroppedVal) els.rivalryDroppedVal.textContent = "0";
    if (els.noiseRivalBlock) els.noiseRivalBlock.classList.add("no-strike");
    if (els.rivalryNewBadge) els.rivalryNewBadge.hidden = true;
    if (els.rivalryRawMeta) els.rivalryRawMeta.textContent = "本地信号文件未就绪";
    if (els.rivalryVernier) els.rivalryVernier.innerHTML = renderVernierSvg(0, 0);
  }

  function normalizeMacroCategory(tag) {
    const t = String(tag || "").trim();
    if (!t) return "";
    const up = t.toUpperCase();
    if (
      /(?<![A-Z])(AI|LLM|AGENT|RAG|GPT|AIGC)(?![A-Z])/.test(up) ||
      t.includes("智能") ||
      t.includes("模型") ||
      t.includes("深度学习") ||
      t.includes("机器学习") ||
      t.includes("多模态")
    ) {
      return "AI";
    }
    return t;
  }

  function interleaveCategoryQuota(items, maxShare, windowSize = 10) {
    if (!(maxShare > 0 && maxShare < 1) || items.length < 5) return items;
    const nonEmptyMacros = new Set(
      items.map((r) => normalizeMacroCategory(r.category_tag)).filter(Boolean)
    );
    if (nonEmptyMacros.size < 2) return items;

    const maxPerWin = Math.max(1, Math.round(windowSize * maxShare));
    const remaining = [...items];
    const selected = [];
    const deferred = [];

    while (remaining.length > 0) {
      const winStart = Math.floor(selected.length / windowSize) * windowSize;
      const winCounts = new Map();
      for (let i = winStart; i < selected.length; i += 1) {
        const mc = normalizeMacroCategory(selected[i].category_tag);
        if (mc) winCounts.set(mc, (winCounts.get(mc) || 0) + 1);
      }

      let pickedIdx = -1;
      for (let idx = 0; idx < remaining.length; idx += 1) {
        const mc = normalizeMacroCategory(remaining[idx].category_tag);
        if (!mc || (winCounts.get(mc) || 0) < maxPerWin) {
          pickedIdx = idx;
          break;
        }
      }

      if (pickedIdx >= 0) {
        selected.push(remaining.splice(pickedIdx, 1)[0]);
      } else {
        const remMacros = new Set(remaining.map((r) => normalizeMacroCategory(r.category_tag)));
        if (remMacros.size <= 1) {
          selected.push(remaining.shift());
        } else {
          deferred.push(remaining.shift());
        }
      }
    }
    return selected.concat(deferred);
  }

  function getVisibleBaseItems() {
    return state.items.filter((item) => !state.hidden.has(item.key));
  }

  function getBucketPoolItems() {
    const visible = getVisibleBaseItems();
    if (state.bucket === "saved") {
      return visible.filter((it) => state.saved.has(it.key));
    }
    if (state.bucket !== "all") {
      return visible.filter((it) => it.bucket === state.bucket);
    }
    return visible;
  }

  function getFilteredItems() {
    const q = state.search.trim().toLowerCase();
    let list = getBucketPoolItems();

    if (state.sourceChannel) {
      list = list.filter((it) => itemMatchesChannel(it, state.sourceChannel));
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
    } else if (state.sort === "breakout") {
      sorted.sort((a, b) => {
        const br = (b.breakout_score || 0) - (a.breakout_score || 0);
        if (br !== 0) return br;
        const dd = (b.stars_delta_24h || 0) - (a.stars_delta_24h || 0);
        if (dd !== 0) return dd;
        return (b.stars || 0) - (a.stars || 0);
      });
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
        return (a._rank ?? 0) - (b._rank ?? 0);
      });

      const maxShare = Number(state.status.max_category_share ?? 0.4);
      if (!state.category && !q && maxShare > 0 && maxShare < 1 && sorted.length >= 5) {
        const byDay = new Map();
        for (const it of sorted) {
          const dk = getLocalDateKey(it.discovered_at);
          if (!byDay.has(dk)) byDay.set(dk, []);
          byDay.get(dk).push(it);
        }
        const rebalanced = [];
        for (const dayList of byDay.values()) {
          rebalanced.push(...interleaveCategoryQuota(dayList, maxShare));
        }
        return rebalanced;
      }
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
    for (const it of visible) {
      if (it.bucket === "trending") counts.trending += 1;
      else if (it.bucket === "radar") counts.radar += 1;
      else counts.community += 1;
      if (state.saved.has(it.key)) counts.saved += 1;
    }
    for (const [k, v] of Object.entries(counts)) {
      const el = els.bucketTabs.querySelector(`[data-count="${k}"]`);
      if (el) el.textContent = String(v);
    }

    const bucketPool = getBucketPoolItems();
    let multiCount = 0;
    for (const it of bucketPool) {
      if ((it.sources || []).length >= 2) multiCount += 1;
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
    if (current && !freq.has(current)) {
      const extra = document.createElement("option");
      extra.value = current;
      extra.textContent = `${current} (0)`;
      els.langSelect.appendChild(extra);
    }
    els.langSelect.value = current;
  }

  function renderCategoryRibbon() {
    const pool = getBucketPoolItems();
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

    if (state.category && !topCats.some(([tag]) => tag === state.category)) {
      topCats.push([state.category, freq.get(state.category) || 0]);
    }

    if (topCats.length === 0 || (topCats.length === 1 && !state.category)) {
      els.categoryRibbon.innerHTML = "";
      return;
    }

    const chips = [
      `<button type="button" class="cat-chip ${state.category === "" ? "is-active" : ""}" data-cat="">全部主题</button>`,
      ...topCats.map(
        ([tag, count]) =>
          `<button type="button" class="cat-chip ${state.category === tag ? "is-active" : ""}" data-cat="${escapeHtml(tag)}"><span>${escapeHtml(tag)}</span><span class="cat-chip-num">${count}</span></button>`
      ),
    ];
    els.categoryRibbon.innerHTML = chips.join("");
  }

  function renderFilterStatus(shownCount) {
    const activeParts = [];
    if (state.sourceChannel) activeParts.push(`信源「${getChannelDisplayName(state.sourceChannel)}」`);
    if (state.category) activeParts.push(`主题「${state.category}」`);
    if (state.language) activeParts.push(`语言「${state.language}」`);
    if (state.search.trim()) activeParts.push(`检索「${state.search.trim()}」`);
    if (state.sort === "delta") activeParts.push("按 24h 增速排序");
    else if (state.sort === "breakout") activeParts.push("按黑马爆发速率排序");
    else if (state.sort === "stars") activeParts.push("按总星标排序");
    if (state.multiOnly) activeParts.push("仅多源共振");
    if (state.hideRead && state.bucket !== "saved") activeParts.push("仅未读");

    updateMobileFilterTrigger();

    if (activeParts.length === 0) {
      els.filterStatus.hidden = true;
      return;
    }
    els.filterStatus.hidden = false;
    els.filterStatusText.textContent = `筛选生效：${activeParts.join(" · ")} — 命中 ${shownCount} 仓`;
  }

  function pickPrimaryText(item) {
    return item.ai_highlight || item.desc_cn || item.description || "暂无项目导读";
  }

  function pickSecondaryText(item, primaryText) {
    const en = (item.description || "").trim();
    const cn = (item.desc_cn || "").trim();
    if (en && en !== primaryText) return en;
    if (cn && cn !== primaryText) return cn;
    return "";
  }

  function renderStampsHtml(item) {
    const bMeta = BUCKET_META[item.bucket] || BUCKET_META.community;
    const langColor = LANG_COLORS[item.language] || "#6E7B8B";
    const sourceCount = (item.sources || []).length;
    return `
      <div class="stamp-row">
        <span class="stamp ${bMeta.cls}">${bMeta.label}</span>
        ${sourceCount >= 2 ? `<span class="stamp is-multi" title="同时命中：${escapeHtml(item.sources.join(" + "))}">多源×${sourceCount}</span>` : ""}
        ${item.release_tag ? `<span class="stamp is-release">${escapeHtml(item.release_tag)}</span>` : ""}
        ${item.category_tag ? `<span class="cat-tag">${escapeHtml(item.category_tag)}</span>` : ""}
        ${
          item.language
            ? `<span class="lang-tag"><span class="lang-swatch" style="background:${langColor}"></span>${escapeHtml(item.language)}</span>`
            : ""
        }
      </div>
    `;
  }

  function renderOpsHtml(item) {
    const isRead = state.read.has(item.key);
    const isSaved = state.saved.has(item.key);
    const safeRepoUrl = safeHttpUrl(item.url);
    const safeDiscUrl = safeHttpUrl(item.discussion_url);

    return `
      <div class="entry-ops row-ops">
        <button type="button" class="op-btn ${isSaved ? "is-saved" : ""}" data-action="save" data-key="${escapeHtml(item.key)}" title="${isSaved ? "取消收藏" : "收藏该项目"}" aria-label="${isSaved ? "取消收藏" : "收藏"}">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="${isSaved ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V4h14z"/></svg>
        </button>
        <button type="button" class="op-btn ${isRead ? "is-active" : ""}" data-action="read" data-key="${escapeHtml(item.key)}" title="${isRead ? "标为未读" : "标为已读"}" aria-label="${isRead ? "标为未读" : "标为已读"}">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square"><polyline points="20 6 9 17 4 12"/></svg>
        </button>
        <button type="button" class="op-btn" data-action="hide" data-key="${escapeHtml(item.key)}" title="屏蔽该项目" aria-label="屏蔽">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
        ${
          safeDiscUrl
            ? `<a class="op-btn ext-link" href="${escapeHtml(safeDiscUrl)}" target="_blank" rel="noopener noreferrer" data-action="link" title="查看社区讨论或版本详情">讨论</a>`
            : ""
        }
        ${
          safeRepoUrl
            ? `<a class="op-btn ext-link" href="${escapeHtml(safeRepoUrl)}" target="_blank" rel="noopener noreferrer" data-action="link" title="在新标签页打开 GitHub 仓库">仓库 ↗</a>`
            : ""
        }
      </div>
    `;
  }

  function getDuelRoleLabel(item, slotIdx, isSingle = false) {
    if (isSingle) {
      if ((item.sources || []).length >= 2) return "本期焦点 · 多源共振";
      return "本期唯一焦点信号";
    }
    const prefix = slotIdx === 0 ? "榜首焦点" : "次席对决";
    if (state.sort === "delta") {
      return `${prefix} · 24h 暴涨`;
    }
    if (state.sort === "breakout") {
      return `${prefix} · 新锐爆发`;
    }
    if (state.sort === "stars") {
      return `${prefix} · 星标领跑`;
    }
    if ((item.sources || []).length >= 2) {
      return `${prefix} · 多源共振`;
    }
    return prefix;
  }

  function renderDuelCellHtml(item, rankNum, slotIdx, isSingle = false) {
    const isRead = state.read.has(item.key);
    const isActiveDrawer = state.drawerKey === item.key;
    const primaryText = pickPrimaryText(item);
    const secondaryText = pickSecondaryText(item, primaryText);
    const delta24 = Number(item.stars_delta_24h || 0);
    const breakout = Number(item.breakout_score || 0);
    const ageDays = item.age_days != null ? Number(item.age_days) : null;
    const showBreakout =
      (state.sort === "breakout" && breakout > 0) ||
      (ageDays !== null && ageDays <= 21 && breakout >= 18) ||
      (delta24 === 0 && breakout >= 40);

    const sparkSvg =
      Array.isArray(item.sparkline) && item.sparkline.length >= 2
        ? renderSparklineSvg(item.sparkline, { width: 76, height: 20 })
        : "";

    const classes = [
      "signal-entry",
      "duel-cell",
      isRead ? "is-read" : "",
      isActiveDrawer ? "is-active-drawer" : "",
    ]
      .filter(Boolean)
      .join(" ");

    return `
      <article class="${classes}" data-key="${escapeHtml(item.key)}" tabindex="0">
        <div>
          <div class="duel-top">
            <div class="duel-meta-stack">
              <span class="duel-role">${escapeHtml(getDuelRoleLabel(item, slotIdx, isSingle))}</span>
              ${renderStampsHtml(item)}
            </div>
            <span class="duel-rank">${padRank(rankNum)}</span>
          </div>

          <h2 class="duel-title">
            ${item.owner ? `<span class="entry-owner">${escapeHtml(item.owner)} /</span>` : ""}
            <span class="duel-name">${escapeHtml(item.name || item.repo_name)}</span>
          </h2>

          <div class="duel-copy">
            <p class="entry-lead">${escapeHtml(primaryText)}</p>
            ${secondaryText ? `<p class="entry-sub">${escapeHtml(secondaryText)}</p>` : ""}
          </div>
        </div>

        <div class="duel-deck">
          <div class="telemetry-cluster">
            <span class="state-diode-line" title="收录于 ${escapeHtml(formatFullTime(item.discovered_at))}">
              <span class="state-diode" aria-hidden="true"></span>
              <span>${formatShortDate(item.discovered_at)}</span>
            </span>
            <span class="metric-stars" title="${(item.stars || 0).toLocaleString()} 星标">★ ${formatStars(item.stars)}</span>
            ${delta24 > 0 ? `<span class="metric-surge" title="近 24h 新增 ${delta24.toLocaleString()} 星标">+${formatStars(delta24)}</span>` : ""}
            ${showBreakout ? `<span class="metric-breakout" title="日均爆发 ${breakout.toFixed(1)} 星/天${ageDays !== null ? ` · 创仓 ${Math.max(1, Math.round(ageDays))} 天` : ""}">↗ ${formatBreakout(breakout)}/天</span>` : ""}
            ${sparkSvg}
          </div>
          ${renderOpsHtml(item)}
        </div>
      </article>
    `;
  }

  function renderLedgerRowHtml(item, rankNum, scaleBounds) {
    const isRead = state.read.has(item.key);
    const isActiveDrawer = state.drawerKey === item.key;
    const primaryText = pickPrimaryText(item);
    const secondaryText = pickSecondaryText(item, primaryText);
    const delta24 = Number(item.stars_delta_24h || 0);
    const breakout = Number(item.breakout_score || 0);
    const ageDays = item.age_days != null ? Number(item.age_days) : null;
    const isYoungSurge = ageDays !== null && ageDays <= 21 && breakout >= 18;
    const showBreakout = (state.sort === "breakout" && breakout > 0) || isYoungSurge;

    let fillPct = 8;
    let isBreakoutFill = false;
    if (state.sort === "breakout" && scaleBounds.maxBreakout > 0) {
      fillPct = Math.max(6, Math.min(100, Math.round((breakout / scaleBounds.maxBreakout) * 100)));
      isBreakoutFill = true;
    } else if (delta24 > 0 && scaleBounds.maxDelta > 0) {
      fillPct = Math.max(8, Math.min(100, Math.round((delta24 / scaleBounds.maxDelta) * 100)));
    } else if (isYoungSurge && scaleBounds.maxBreakout > 0) {
      fillPct = Math.max(8, Math.min(100, Math.round((breakout / scaleBounds.maxBreakout) * 100)));
      isBreakoutFill = true;
    } else if (scaleBounds.maxStars > 0) {
      fillPct = Math.max(5, Math.min(88, Math.round((Math.log10((item.stars || 1) + 1) / Math.log10(scaleBounds.maxStars + 1)) * 88)));
    }

    const sparkSvg =
      Array.isArray(item.sparkline) && item.sparkline.length >= 2
        ? renderSparklineSvg(item.sparkline, { width: 56, height: 16 })
        : "";

    const classes = [
      "signal-entry",
      "ledger-row",
      isRead ? "is-read" : "",
      isActiveDrawer ? "is-active-drawer" : "",
    ]
      .filter(Boolean)
      .join(" ");

    return `
      <article class="${classes}" data-key="${escapeHtml(item.key)}" tabindex="0">
        <div class="row-idx">
          <span class="row-rank">${padRank(rankNum)}</span>
          <span class="state-diode-line" title="收录于 ${escapeHtml(formatFullTime(item.discovered_at))}">
            <span class="state-diode" aria-hidden="true"></span>
            <span>${formatShortDate(item.discovered_at)}</span>
          </span>
        </div>

        <div class="row-identity">
          <h2 class="row-title">
            ${item.owner ? `<span class="entry-owner">${escapeHtml(item.owner)} /</span>` : ""}
            <span class="row-name">${escapeHtml(item.name || item.repo_name)}</span>
          </h2>
          ${renderStampsHtml(item)}
        </div>

        <div class="row-copy">
          <p class="entry-lead">${escapeHtml(primaryText)}</p>
          ${secondaryText ? `<p class="entry-sub">${escapeHtml(secondaryText)}</p>` : ""}
        </div>

        <div class="row-telemetry">
          <div class="row-metrics-top">
            <span class="metric-stars" title="${(item.stars || 0).toLocaleString()} 星标">★ ${formatStars(item.stars)}</span>
            ${delta24 > 0 ? `<span class="metric-surge" title="近 24h 新增 ${delta24.toLocaleString()} 星标">+${formatStars(delta24)}</span>` : ""}
            ${showBreakout ? `<span class="metric-breakout" title="日均爆发 ${breakout.toFixed(1)} 星/天${ageDays !== null ? ` · 创仓 ${Math.max(1, Math.round(ageDays))} 天` : ""}">↗ ${formatBreakout(breakout)}/天</span>` : ""}
          </div>
          <div class="row-metrics-sub">
            <span class="velocity-track" aria-hidden="true">
              <span class="velocity-fill ${isBreakoutFill ? "is-breakout" : ""}" style="width:${fillPct}%"></span>
            </span>
            ${sparkSvg}
          </div>
        </div>

        ${renderOpsHtml(item)}
      </article>
    `;
  }

  function renderStream() {
    updateTabCounts();
    renderChannelArrays();
    renderCategoryRibbon();

    const filtered = getFilteredItems();
    renderFilterStatus(filtered.length);
    syncUrlState();

    if (filtered.length === 0) {
      els.signalStream.innerHTML = "";
      els.emptyState.hidden = false;
      if (state.bucket === "saved" && state.saved.size === 0) {
        els.emptyTitle.textContent = "收藏夹目前为空";
        els.emptyDesc.textContent = "点击任意条目右侧的书签按钮，或在详情档案中按 S 键即可收入此处。";
      } else {
        els.emptyTitle.textContent = "当前筛选下没有匹配的开源项目";
        els.emptyDesc.textContent = "可放宽检索词、切换上方栏目与信源，或关闭「隐藏已读」。";
      }
      return;
    }

    els.emptyState.hidden = true;

    const scaleBounds = {
      maxDelta: Math.max(0, ...filtered.map((it) => Number(it.stars_delta_24h) || 0)),
      maxBreakout: Math.max(0, ...filtered.map((it) => Number(it.breakout_score) || 0)),
      maxStars: Math.max(1, ...filtered.map((it) => Number(it.stars) || 0)),
    };

    if (state.sort === "newest") {
      const groups = new Map();
      for (const item of filtered) {
        const dk = getLocalDateKey(item.discovered_at);
        if (!groups.has(dk)) groups.set(dk, []);
        groups.get(dk).push(item);
      }

      const blocks = [];
      let rankCounter = 1;
      let groupIdx = 0;

      for (const [dateKey, groupItems] of groups.entries()) {
        blocks.push(`
          <div class="issue-divider">
            <span class="issue-divider-title">${escapeHtml(formatDateSectionLabel(dateKey))}</span>
            <span class="issue-divider-count">共 ${groupItems.length} 仓</span>
          </div>
        `);

        let startIdx = 0;
        // Only promote the lead date group (groupIdx === 0) into the hero stage
        if (groupIdx === 0) {
          if (groupItems.length >= 2) {
            blocks.push(`
              <div class="duel-stage">
                ${renderDuelCellHtml(groupItems[0], rankCounter, 0, false)}
                ${renderDuelCellHtml(groupItems[1], rankCounter + 1, 1, false)}
              </div>
            `);
            rankCounter += 2;
            startIdx = 2;
          } else if (groupItems.length === 1) {
            blocks.push(`
              <div class="duel-stage is-single">
                ${renderDuelCellHtml(groupItems[0], rankCounter, 0, true)}
              </div>
            `);
            rankCounter += 1;
            startIdx = 1;
          }
        }

        if (startIdx < groupItems.length) {
          const rows = [];
          for (let i = startIdx; i < groupItems.length; i += 1) {
            rows.push(renderLedgerRowHtml(groupItems[i], rankCounter, scaleBounds));
            rankCounter += 1;
          }
          blocks.push(`<div class="ledger-sheet">${rows.join("")}</div>`);
        }

        groupIdx += 1;
      }

      els.signalStream.innerHTML = blocks.join("");
    } else {
      const blocks = [];
      let startIdx = 0;
      if (filtered.length >= 2) {
        blocks.push(`
          <div class="duel-stage">
            ${renderDuelCellHtml(filtered[0], 1, 0, false)}
            ${renderDuelCellHtml(filtered[1], 2, 1, false)}
          </div>
        `);
        startIdx = 2;
      } else if (filtered.length === 1) {
        blocks.push(`
          <div class="duel-stage is-single">
            ${renderDuelCellHtml(filtered[0], 1, 0, true)}
          </div>
        `);
        startIdx = 1;
      }

      if (startIdx < filtered.length) {
        const rows = [];
        for (let i = startIdx; i < filtered.length; i += 1) {
          rows.push(renderLedgerRowHtml(filtered[i], i + 1, scaleBounds));
        }
        blocks.push(`<div class="ledger-sheet">${rows.join("")}</div>`);
      }
      els.signalStream.innerHTML = blocks.join("");
    }
  }

  function getItemByKey(key) {
    const k = String(key || "").toLowerCase();
    return state.items.find((it) => it.key === k) || null;
  }

  function syncModalInert() {
    if (!els.sheetFrame) return;
    const anyModalOpen =
      Boolean(state.drawerKey) || !els.rssBackdrop.hidden || !els.digestBackdrop.hidden;
    els.sheetFrame.inert = anyModalOpen;
  }

  function restorePreviousFocus() {
    if (state.returnFocusKey) {
      const entry = els.signalStream.querySelector(`[data-key="${CSS.escape(state.returnFocusKey)}"]`);
      if (entry && typeof entry.focus === "function") {
        entry.focus({ preventScroll: true });
        state.returnFocusKey = null;
        state.returnFocusEl = null;
        return;
      }
    }
    if (state.returnFocusEl && document.contains(state.returnFocusEl) && typeof state.returnFocusEl.focus === "function") {
      state.returnFocusEl.focus({ preventScroll: true });
    }
    state.returnFocusKey = null;
    state.returnFocusEl = null;
  }

  /* ---------- Inspector Sheet Logic ---------- */
  function openDrawer(key) {
    const active = document.activeElement;
    if (!state.drawerKey && active && active !== document.body) {
      state.returnFocusEl = active;
      const entry = active.closest ? active.closest(".signal-entry") : null;
      state.returnFocusKey = (entry && entry.dataset.key) || key;
    }

    const filtered = getFilteredItems();
    state.drawerListKeys = filtered.map((it) => it.key);
    if (!state.drawerListKeys.includes(key)) {
      state.drawerListKeys = [key];
    }
    showDrawerItem(key);

    els.drawerBackdrop.hidden = false;
    els.detailDrawer.setAttribute("aria-hidden", "false");
    syncModalInert();
    // Flush initial off-screen transform, then open & move keyboard focus into dialog
    void els.detailDrawer.offsetWidth;
    els.drawerBackdrop.classList.add("is-open");
    els.detailDrawer.classList.add("is-open");
    void els.detailDrawer.offsetWidth;
    if (els.btnCloseDrawer) {
      els.btnCloseDrawer.focus({ preventScroll: true });
    }
    document.body.style.overflow = "hidden";
  }

  function closeDrawer() {
    if (!state.drawerKey) return;
    state.drawerKey = null;
    els.drawerBackdrop.classList.remove("is-open");
    els.detailDrawer.classList.remove("is-open");
    els.detailDrawer.setAttribute("aria-hidden", "true");
    syncModalInert();
    document.body.style.overflow = "";
    setTimeout(() => {
      if (!state.drawerKey) {
        els.drawerBackdrop.hidden = true;
      }
    }, 220);
    renderStream();
    restorePreviousFocus();
  }

  function syncDrawerReadButton(itemKey) {
    const btn = document.getElementById("btnDrawerReadToggle");
    if (!btn) return;
    const isRead = state.read.has(itemKey);
    btn.textContent = isRead ? "标为未读" : "标为已读";
    btn.classList.toggle("is-toggled", !isRead);
  }

  function showDrawerItem(key) {
    const item = getItemByKey(key);
    if (!item) return;

    state.drawerKey = item.key;
    state.returnFocusKey = item.key;
    syncUrlState();

    if (!state.read.has(item.key)) {
      state.read.add(item.key);
      saveLocalPrefs();
      const entryEl = els.signalStream.querySelector(`[data-key="${CSS.escape(item.key)}"]`);
      if (entryEl) entryEl.classList.add("is-read");
    }

    els.signalStream
      .querySelectorAll(".signal-entry.is-active-drawer")
      .forEach((el) => el.classList.remove("is-active-drawer"));
    const activeEntry = els.signalStream.querySelector(`[data-key="${CSS.escape(item.key)}"]`);
    if (activeEntry) activeEntry.classList.add("is-active-drawer");

    const idx = state.drawerListKeys.indexOf(item.key);
    els.drawerPos.textContent = `${padRank(idx + 1)} / ${padRank(state.drawerListKeys.length)}`;
    els.btnPrevItem.disabled = idx <= 0;
    els.btnNextItem.disabled = idx < 0 || idx >= state.drawerListKeys.length - 1;

    const bMeta = BUCKET_META[item.bucket] || BUCKET_META.community;
    const langColor = LANG_COLORS[item.language] || "#6E7B8B";
    const isSaved = state.saved.has(item.key);
    const cachedReadme = state.readmeCache.get(item.key);
    const delta24 = Number(item.stars_delta_24h || 0);
    const delta7d = Number(item.stars_delta_7d || 0);
    const breakout = Number(item.breakout_score || 0);
    const ageDays = item.age_days != null ? Number(item.age_days) : null;
    const topics = Array.isArray(item.topics) ? item.topics : [];
    const safeRepoUrl = safeHttpUrl(item.url);
    const safeDiscUrl = safeHttpUrl(item.discussion_url);

    const ogSeed = encodeURIComponent(state.status.last_run_date || "1");
    const ogCardUrl = SAFE_REPO_RE.test(item.repo_name)
      ? `https://opengraph.githubassets.com/${ogSeed}/${item.repo_name}`
      : "";

    const sparkPoints = Array.isArray(item.sparkline) ? item.sparkline : [];
    const drawerSparkSvg =
      sparkPoints.length >= 2 ? renderSparklineSvg(sparkPoints, { width: 152, height: 32 }) : "";

    els.drawerBody.innerHTML = `
      <div class="dossier-stamps">
        <span class="stamp ${bMeta.cls}">${bMeta.label}</span>
        ${(item.sources || []).length >= 2 ? `<span class="stamp is-multi">多源共振×${item.sources.length}</span>` : ""}
        ${item.release_tag ? `<span class="stamp is-release">${escapeHtml(item.release_tag)}</span>` : ""}
        ${item.category_tag ? `<span class="cat-tag">${escapeHtml(item.category_tag)}</span>` : ""}
        ${(item.sources || []).map((s) => `<span class="stamp">${escapeHtml(s)}</span>`).join("")}
      </div>

      ${item.owner ? `<div class="dossier-owner">${escapeHtml(item.owner)} /</div>` : ""}
      <h2 class="dossier-title">${escapeHtml(item.name || item.repo_name)}</h2>

      ${
        item.ai_highlight
          ? `
        <div class="dossier-lead-box">
          <div class="section-kicker">一句话导读</div>
          <p class="dossier-lead-text">${escapeHtml(item.ai_highlight)}</p>
        </div>`
          : ""
      }

      <div class="dossier-telemetry-grid">
        <div class="tele-cell">
          <span class="tele-label">总星标</span>
          <span class="tele-val">★ ${(item.stars || 0).toLocaleString()}</span>
        </div>
        <div class="tele-cell">
          <span class="tele-label">24h / 7d 增速</span>
          <span class="tele-val">${
            delta24 > 0
              ? `+${delta24.toLocaleString()}${delta7d > delta24 ? ` / +${delta7d.toLocaleString()}` : ""}`
              : delta7d > 0
                ? `7d +${delta7d.toLocaleString()}`
                : "—"
          }</span>
        </div>
        <div class="tele-cell">
          <span class="tele-label">分支 / 议题</span>
          <span class="tele-val">${(item.forks || 0).toLocaleString()} / ${(item.open_issues || 0).toLocaleString()}</span>
        </div>
        <div class="tele-cell">
          <span class="tele-label">主要语言</span>
          <span class="tele-val">${
            item.language
              ? `<span class="lang-tag"><span class="lang-swatch" style="background:${langColor}"></span>${escapeHtml(item.language)}</span>`
              : "—"
          }</span>
        </div>
        <div class="tele-cell">
          <span class="tele-label">开源协议</span>
          <span class="tele-val">${item.license_name ? escapeHtml(item.license_name) : "—"}</span>
        </div>
        <div class="tele-cell">
          <span class="tele-label">创仓年龄</span>
          <span class="tele-val">${ageDays !== null ? `${Math.max(1, Math.round(ageDays))} 天` : "—"}</span>
        </div>
        <div class="tele-cell">
          <span class="tele-label">最近提交</span>
          <span class="tele-val">${item.pushed_at ? escapeHtml(formatShortDate(item.pushed_at)) : "—"}</span>
        </div>
        <div class="tele-cell">
          <span class="tele-label">收录时间</span>
          <span class="tele-val">${escapeHtml(formatShortDate(item.discovered_at))}</span>
        </div>
      </div>

      ${
        drawerSparkSvg || breakout > 0
          ? `<div class="dossier-trajectory">
              <div class="traj-col">
                <span class="traj-label">7 日星标轨迹</span>
                <span class="traj-val">${
                  sparkPoints.length >= 2
                    ? `${Number(sparkPoints[0]).toLocaleString()} → ${Number(sparkPoints[sparkPoints.length - 1]).toLocaleString()} ★`
                    : `当前 ${(item.stars || 0).toLocaleString()} ★`
                }</span>
              </div>
              ${
                breakout > 0
                  ? `<div class="traj-col">
                      <span class="traj-label">日均爆发速率</span>
                      <span class="traj-val">↗ ${breakout.toFixed(1)} ★/天</span>
                    </div>`
                  : ""
              }
              ${drawerSparkSvg}
            </div>`
          : ""
      }

      ${
        topics.length
          ? `<div class="topic-bank">${topics
              .map((t) => `<button type="button" class="topic-tag" data-topic="${escapeHtml(t)}" title="按该 Topic 检索">#${escapeHtml(t)}</button>`)
              .join("")}</div>`
          : ""
      }

      <div class="dossier-descs">
        ${
          item.desc_cn
            ? `<div>
                <div class="section-kicker">中文摘要</div>
                <p class="dossier-desc-p">${escapeHtml(item.desc_cn)}</p>
              </div>`
            : ""
        }
        ${
          item.description && item.description !== item.desc_cn
            ? `<div>
                <div class="section-kicker">仓库原始描述</div>
                <p class="dossier-desc-p">${escapeHtml(item.description)}</p>
              </div>`
            : ""
        }
      </div>

      ${
        ogCardUrl
          ? `<div class="dossier-og-frame" id="drawerOgWrap" hidden>
              <img class="dossier-og-img" id="drawerOgImg" src="${escapeHtml(ogCardUrl)}" alt="${escapeHtml(item.repo_name)} 预览图" decoding="async" />
            </div>`
          : ""
      }

      <section class="readme-panel">
        <div class="readme-bar">
          <div class="section-kicker" style="margin:0">README 在线预览（直连免限流优先）</div>
          <button type="button" class="machined-btn" id="btnLoadReadme">
            ${cachedReadme ? "已加载 README" : "拉取 README 文档"}
          </button>
        </div>
        <div class="readme-body" id="readmeContent">
          ${cachedReadme || `<p style="margin:0;">点击右侧按钮直接在当前档案面板加载仓库 README，无需跳出页面。</p>`}
        </div>
      </section>
    `;

    els.drawerFooter.innerHTML = `
      <div class="inspect-foot-group">
        ${
          safeRepoUrl
            ? `<a class="machined-btn solid" href="${escapeHtml(safeRepoUrl)}" target="_blank" rel="noopener noreferrer">前往 GitHub ↗</a>`
            : ""
        }
        ${
          safeDiscUrl
            ? `<a class="machined-btn" href="${escapeHtml(safeDiscUrl)}" target="_blank" rel="noopener noreferrer">社区讨论 ↗</a>`
            : ""
        }
        <button type="button" class="machined-btn" id="btnCopyClone">复制 clone</button>
        <button type="button" class="machined-btn" id="btnShareRepo" title="复制指向该项目的深链">分享深链</button>
      </div>
      <div class="inspect-foot-group">
        <button type="button" class="machined-btn ${isSaved ? "is-toggled" : ""}" id="btnDrawerSave">
          ${isSaved ? "★ 已收藏" : "☆ 收藏"}
        </button>
        <button type="button" class="machined-btn" id="btnDrawerReadToggle">标为未读</button>
        <button type="button" class="machined-btn" id="btnDrawerHide">屏蔽</button>
      </div>
    `;

    syncDrawerReadButton(item.key);
    els.drawerBody.scrollTop = 0;

    // Eagerly load OG preview image for the single inspected repo without lazy-inside-hidden deadlock
    const ogImg = document.getElementById("drawerOgImg");
    const ogWrap = document.getElementById("drawerOgWrap");
    if (ogImg && ogWrap) {
      if (ogImg.complete && ogImg.naturalWidth > 0) {
        ogWrap.hidden = false;
      } else {
        ogImg.addEventListener("load", () => {
          if (ogImg.naturalWidth > 0) ogWrap.hidden = false;
        });
        ogImg.addEventListener("error", () => {
          ogWrap.hidden = true;
        });
      }
    }

    const btnLoadReadme = document.getElementById("btnLoadReadme");
    if (btnLoadReadme && !cachedReadme) {
      btnLoadReadme.addEventListener("click", () => loadReadmePreview(item));
    }

    els.drawerBody.querySelectorAll("[data-topic]").forEach((chip) => {
      chip.addEventListener("click", () => {
        state.search = chip.dataset.topic || "";
        els.searchInput.value = state.search;
        closeDrawer();
        showToast(`已按 Topic「${state.search}」检索`);
      });
    });

    document.getElementById("btnCopyClone").addEventListener("click", () => {
      const cloneBase = SAFE_REPO_RE.test(item.repo_name)
        ? `https://github.com/${item.repo_name}.git`
        : `${safeRepoUrl}.git`;
      copyText(`git clone ${cloneBase}`, "已复制 git clone 命令");
    });

    document.getElementById("btnShareRepo").addEventListener("click", () => {
      copyText(buildShareableUrl(), "已复制仓库档案深链");
    });

    document.getElementById("btnDrawerSave").addEventListener("click", () => {
      toggleSaved(item.key);
      showDrawerItem(item.key);
      updateTabCounts();
    });

    document.getElementById("btnDrawerReadToggle").addEventListener("click", () => {
      toggleRead(item.key);
      const entryEl = els.signalStream.querySelector(`[data-key="${CSS.escape(item.key)}"]`);
      if (entryEl) entryEl.classList.toggle("is-read", state.read.has(item.key));
      syncDrawerReadButton(item.key);
    });

    document.getElementById("btnDrawerHide").addEventListener("click", () => {
      state.hidden.add(item.key);
      saveLocalPrefs();
      showToast(`已屏蔽 ${item.repo_name}`);
      state.returnFocusKey = null;
      closeDrawer();
    });
  }

  async function fetchReadmeTextWithFallback(repoName) {
    if (!SAFE_REPO_RE.test(repoName)) {
      throw new Error("非法的仓库标识符");
    }
    const cdnCandidates = [
      `https://raw.githubusercontent.com/${repoName}/HEAD/README.md`,
      `https://raw.githubusercontent.com/${repoName}/HEAD/readme.md`,
    ];
    for (const cdnUrl of cdnCandidates) {
      try {
        const r = await fetch(cdnUrl);
        if (r.ok) {
          return await r.text();
        }
      } catch {
        // try next candidate
      }
    }

    const apiResp = await fetch(`https://api.github.com/repos/${repoName}/readme`, {
      headers: { Accept: "application/vnd.github.raw+json" },
    });
    if (!apiResp.ok) {
      throw new Error(`HTTP ${apiResp.status}`);
    }
    return await apiResp.text();
  }

  async function loadReadmePreview(item) {
    const btn = document.getElementById("btnLoadReadme");
    const box = document.getElementById("readmeContent");
    if (!btn || !box) return;

    btn.disabled = true;
    btn.textContent = "拉取中…";
    try {
      const md = await fetchReadmeTextWithFallback(item.repo_name);
      const rendered = renderSafeMarkdown(md.slice(0, 12000), item.repo_name);
      state.readmeCache.set(item.key, rendered);
      if (state.drawerKey === item.key) {
        box.innerHTML = rendered;
        btn.textContent = "已加载 README";
      }
    } catch (err) {
      if (state.drawerKey === item.key) {
        box.innerHTML = `<p style="margin:0;">README 拉取失败（${escapeHtml(err.message)}，可能受限于网络或仓库无 README），可点击下方「前往 GitHub」查看。</p>`;
        btn.disabled = false;
        btn.textContent = "重试拉取";
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
      const tokens = [];
      const putToken = (htmlSnippet) => {
        const key = `\x00TOK_${tokens.length}\x00`;
        tokens.push([key, htmlSnippet]);
        return key;
      };

      let working = String(raw ?? "")
        .replace(/\x00/g, "")
        .replace(/`([^`]+)`/g, (_, code) => putToken(`<code>${escapeHtml(code)}</code>`));

      working = working.replace(
        /!\[([^\]]*)\]\(((?:[^\s()]+|\([^\s()]*\))+)\)/g,
        (_, alt, rawSrc) => {
          const cleanUrl = resolveGithubAssetUrl(rawSrc, repoName);
          if (!cleanUrl) return "";
          return putToken(
            `<img alt="${escapeHtml(alt)}" src="${escapeHtml(cleanUrl)}" loading="lazy" />`
          );
        }
      );

      working = working.replace(
        /\[([^\]]+)\]\(((?:[^\s()]+|\([^\s()]*\))+)\)/g,
        (_, label, rawHref) => {
          const cleanHref = resolveGithubDocUrl(rawHref, repoName);
          if (!cleanHref) return label;
          return putToken(
            `<a href="${escapeHtml(cleanHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
          );
        }
      );

      let safe = escapeHtml(working);
      safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

      for (let i = tokens.length - 1; i >= 0; i -= 1) {
        const [key, htmlSnippet] = tokens[i];
        safe = safe.split(key).join(htmlSnippet);
      }
      return safe;
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
    return out.join("\n") || "<p>README 内容为空或仅包含 HTML 标签。</p>";
  }

  function resolveGithubAssetUrl(src, repoName) {
    if (!src) return "";
    if (/^https?:\/\//i.test(src)) return safeHttpUrl(src);
    if (src.startsWith("#") || /^[a-zA-Z][a-zA-Z0-9+.-]*:/i.test(src)) return "";
    if (!SAFE_REPO_RE.test(repoName)) return "";
    const clean = src.replace(/^\.?\//, "");
    return safeHttpUrl(`https://raw.githubusercontent.com/${repoName}/HEAD/${clean}`);
  }

  function resolveGithubDocUrl(href, repoName) {
    if (!href) return "";
    if (/^https?:\/\//i.test(href)) return safeHttpUrl(href);
    if (href.startsWith("#") || /^[a-zA-Z][a-zA-Z0-9+.-]*:/i.test(href)) return "";
    if (!SAFE_REPO_RE.test(repoName)) return "";
    const clean = href.replace(/^\.?\//, "");
    return safeHttpUrl(`https://github.com/${repoName}/blob/HEAD/${clean}`);
  }

  /* ---------- Actions ---------- */
  function toggleSaved(key) {
    if (state.saved.has(key)) {
      state.saved.delete(key);
      showToast("已移出收藏夹");
    } else {
      state.saved.add(key);
      showToast("已收入收藏夹");
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
    showToast("已屏蔽该项目（可在右上角「订阅与备份」恢复）");
  }

  function resetAllFilters() {
    state.category = "";
    state.language = "";
    state.sourceChannel = "";
    state.search = "";
    state.sort = "newest";
    state.multiOnly = false;
    state.hideRead = false;
    syncControlsFromState();
    saveLocalPrefs();
    renderStream();
  }

  /* ---------- RSS & Backup Modal ---------- */
  function openRssModal() {
    state.returnFocusEl = document.activeElement;
    state.returnFocusKey = null;
    const fb = state.feedsBase;
    const feeds = [
      { name: "全源聚合精选 (RSS 2.0)", rel: `${fb}/all_discoveries.xml` },
      { name: "JSON Feed 1.1（阅读器 / 自动化脚本）", rel: `${fb}/feed.json` },
      { name: "GitHub Trending 分榜 (RSS)", rel: `${fb}/github_trending.xml` },
      { name: "GitHub 黑马雷达与版本追踪 (RSS)", rel: `${fb}/github_radar.xml` },
      { name: "开源社区发现（Show HN / HelloGitHub / HF）", rel: `${fb}/community_picks.xml` },
      { name: "OPML 全分榜一键导入包", rel: `${fb}/feeds.opml` },
      { name: "AI Agent 直读入口 (/llms.txt)", rel: state.llmsHref },
    ];
    els.rssList.innerHTML = feeds
      .map((f) => {
        const full = new URL(f.rel, window.location.href).href;
        return `
          <div class="feed-row">
            <div class="feed-row-info">
              <div class="feed-row-name">${escapeHtml(f.name)}</div>
              <div class="feed-row-url">${escapeHtml(full)}</div>
            </div>
            <button type="button" class="machined-btn" data-copy-url="${escapeHtml(full)}">复制</button>
          </div>
        `;
      })
      .join("");

    els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 ${state.read.size} · 已屏蔽 ${state.hidden.size}`;
    els.rssBackdrop.hidden = false;
    syncModalInert();
    requestAnimationFrame(() => {
      if (els.btnCloseRss) els.btnCloseRss.focus({ preventScroll: true });
    });
  }

  function closeRssModal() {
    els.rssBackdrop.hidden = true;
    syncModalInert();
    restorePreviousFocus();
  }

  /* ---------- Daily Digest Modal ---------- */
  function buildMarkdownDigest(items, title) {
    const today = state.status.last_run_date || getLocalDateKey(new Date().toISOString());
    const trendSummary = String(state.status.trend_summary || "").trim();
    const lines = [`# ${title} (${today})`, ""];
    if (trendSummary) {
      lines.push(`> **今日听风**：${trendSummary}`, "");
    }
    items.forEach((it, idx) => {
      const tags = [];
      if ((it.sources || []).length >= 2) tags.push(`多源×${it.sources.length}`);
      if (it.release_tag) tags.push(it.release_tag);
      if (it.category_tag) tags.push(it.category_tag);
      if (it.language) tags.push(it.language);
      let starPart = `★${(it.stars || 0).toLocaleString()}`;
      if ((it.stars_delta_24h || 0) > 0) starPart += ` (+${it.stars_delta_24h.toLocaleString()})`;
      tags.push(starPart);
      const safeUrl = safeHttpUrl(it.url) || `https://github.com/${it.repo_name}`;
      const summary = it.ai_highlight || it.desc_cn || it.description || "";
      lines.push(`${idx + 1}. **[${it.repo_name}](${safeUrl})** · ${tags.join(" · ")}`);
      if (summary) lines.push(`   > ${summary}`);
    });
    return lines.join("\n");
  }

  function openDigestModal() {
    state.returnFocusEl = document.activeElement;
    state.returnFocusKey = null;
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

    els.digestSubtitle.textContent = `已按多源共振、24h 增速与当前筛选整理前 ${topItems.length} 项（共匹配 ${filtered.length} 仓）：`;
    els.digestTextarea.value = buildMarkdownDigest(topItems, "GitHub Radar 每日开源精选");
    els.digestBackdrop.hidden = false;
    syncModalInert();
    requestAnimationFrame(() => {
      if (els.btnCopyDigest) els.btnCopyDigest.focus({ preventScroll: true });
    });
  }

  function closeDigestModal() {
    els.digestBackdrop.hidden = true;
    syncModalInert();
    restorePreviousFocus();
  }

  function handleSourceChannelClick(e) {
    const btn = e.target.closest("button.chan-cell[data-source]");
    if (!btn) return;
    const src = btn.dataset.source || "";
    state.sourceChannel = state.sourceChannel === src ? "" : src;
    syncControlsFromState();
    renderStream();
  }

  /* ---------- Event Wiring ---------- */
  function bindEvents() {
    els.btnTheme.addEventListener("click", toggleTheme);

    if (els.sourceHealthPill) {
      els.sourceHealthPill.addEventListener("click", handleSourceChannelClick);
    }
    if (els.mobileSourceStrip) {
      els.mobileSourceStrip.addEventListener("click", handleSourceChannelClick);
    }

    if (els.btnMobileFilters) {
      els.btnMobileFilters.addEventListener("click", () => {
        state.mobileFiltersOpen = !state.mobileFiltersOpen;
        updateMobileFilterTrigger();
      });
    }

    els.bucketTabs.addEventListener("click", (e) => {
      const btn = e.target.closest(".idx-tab");
      if (!btn) return;
      state.bucket = btn.dataset.bucket || "all";
      state.category = "";
      syncControlsFromState();
      renderStream();
    });

    els.categoryRibbon.addEventListener("click", (e) => {
      const chip = e.target.closest(".cat-chip");
      if (!chip) return;
      const cat = chip.dataset.cat || "";
      state.category = state.category === cat ? "" : cat;
      renderStream();
    });

    els.searchInput.addEventListener("input", (e) => {
      state.search = e.target.value;
      renderStream();
    });

    els.langSelect.addEventListener("change", (e) => {
      state.language = e.target.value;
      renderStream();
    });

    els.sortSelect.addEventListener("change", (e) => {
      state.sort = e.target.value;
      renderStream();
    });

    els.btnMultiSource.addEventListener("click", () => {
      state.multiOnly = !state.multiOnly;
      els.btnMultiSource.setAttribute("aria-pressed", String(state.multiOnly));
      renderStream();
    });

    els.btnHideRead.addEventListener("click", () => {
      state.hideRead = !state.hideRead;
      els.btnHideRead.setAttribute("aria-pressed", String(state.hideRead));
      saveLocalPrefs();
      renderStream();
    });

    if (els.btnCopyFilterUrl) {
      els.btnCopyFilterUrl.addEventListener("click", () => {
        copyText(buildShareableUrl(), "已复制当前筛选视图深链");
      });
    }

    els.btnResetFilters.addEventListener("click", resetAllFilters);
    els.btnEmptyReset.addEventListener("click", () => {
      state.bucket = "all";
      resetAllFilters();
    });

    els.signalStream.addEventListener("click", (e) => {
      const actionEl = e.target.closest("[data-action]");
      if (actionEl) {
        const action = actionEl.dataset.action;
        if (action === "link") {
          const entry = e.target.closest(".signal-entry");
          if (entry && entry.dataset.key) {
            state.read.add(entry.dataset.key);
            saveLocalPrefs();
            entry.classList.add("is-read");
          }
          return;
        }
        e.stopPropagation();
        const key = actionEl.dataset.key;
        if (action === "save") toggleSaved(key);
        else if (action === "read") toggleRead(key);
        else if (action === "hide") hideRepo(key);
        renderStream();
        return;
      }

      const entry = e.target.closest(".signal-entry");
      if (entry && entry.dataset.key) {
        openDrawer(entry.dataset.key);
      }
    });

    els.signalStream.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("signal-entry")) {
        e.preventDefault();
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
      copyText(btn.dataset.copyUrl, "已复制订阅地址");
    });

    els.btnClearHidden.addEventListener("click", () => {
      state.hidden.clear();
      saveLocalPrefs();
      populateLanguages();
      renderStream();
      els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 ${state.read.size} · 已屏蔽 0`;
      showToast("已恢复全部屏蔽条目");
    });

    els.btnClearRead.addEventListener("click", () => {
      state.read.clear();
      saveLocalPrefs();
      renderStream();
      els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 0 · 已屏蔽 ${state.hidden.size}`;
      showToast("已清空全部已读标记");
    });

    els.btnExportSavedMd.addEventListener("click", () => {
      const savedItems = state.items.filter((it) => state.saved.has(it.key));
      if (!savedItems.length) {
        showToast("收藏夹目前为空");
        return;
      }
      const md = buildMarkdownDigest(savedItems, "GitHub Radar 我的开源收藏");
      downloadTextFile(
        `github-radar-saved-${getLocalDateKey(new Date().toISOString())}.md`,
        md,
        "text/markdown;charset=utf-8"
      );
      showToast(`已导出 ${savedItems.length} 项收藏为 Markdown`);
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
      showToast("已导出本地阅读备份 JSON");
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
        renderStream();
        els.localStatsSummary.textContent = `已收藏 ${state.saved.size} · 已读 ${state.read.size} · 已屏蔽 ${state.hidden.size}`;
        showToast("已恢复本地备份状态");
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
      downloadTextFile(
        `github-radar-digest-${day}.md`,
        els.digestTextarea.value,
        "text/markdown;charset=utf-8"
      );
      showToast("已下载简报 Markdown 文件");
    });

    window.addEventListener("popstate", () => {
      if (state.items.length > 0) {
        applyUrlStateToView();
      }
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

  async function fetchRadarDataWithFallback() {
    const ts = Date.now();
    const rootCandidate = {
      url: `./feeds/data.json?t=${ts}`,
      feedsBase: "./feeds",
      llmsHref: "./llms.txt",
    };
    const parentCandidate = {
      url: `../feeds/data.json?t=${ts}`,
      feedsBase: "../feeds",
      llmsHref: "../feeds/llms.txt",
    };
    const inWebSubdir = /\/web\/(?:index\.html)?$/i.test(window.location.pathname || "");
    const candidates = inWebSubdir
      ? [parentCandidate, rootCandidate]
      : [rootCandidate, parentCandidate];
    let lastErr = null;
    for (const c of candidates) {
      try {
        const resp = await fetch(c.url, { cache: "no-store" });
        if (resp.ok) {
          state.feedsBase = c.feedsBase;
          state.llmsHref = c.llmsHref;
          if (els.footerLlmsLink) els.footerLlmsLink.href = c.llmsHref;
          if (els.rssAltLink) els.rssAltLink.href = `${c.feedsBase}/all_discoveries.xml`;
          if (els.jsonAltLink) els.jsonAltLink.href = `${c.feedsBase}/feed.json`;
          return await resp.json();
        }
        lastErr = new Error(`HTTP ${resp.status}`);
      } catch (err) {
        lastErr = err;
      }
    }
    throw lastErr || new Error("读取失败");
  }

  /* ---------- Bootstrap ---------- */
  async function init() {
    loadLocalPrefs();
    bindEvents();

    try {
      const data = await fetchRadarDataWithFallback();
      state.items = (Array.isArray(data.items) ? data.items : []).map((it, idx) => ({
        ...it,
        _rank: idx,
      }));
      state.status = data.status || {};

      const updated = formatFullTime(state.status.last_run_at || data.generated_at);
      els.issueMeta.textContent = `${updated || "刚刚"} 同步`;

      renderTelemetryAndBrief();
      applyUrlStateToView();
    } catch (err) {
      els.issueMeta.textContent = "信号数据读取失败";
      renderTelemetryFailure();
      els.signalStream.innerHTML = "";
      els.emptyState.hidden = false;
      els.emptyTitle.textContent = "未能读取信号台账数据";
      els.emptyDesc.textContent = `请确认 feeds/data.json 已生成（${err.message}）。`;
    }
  }

  init();
})();
