/**
 * pdf_viewer_app.js — in-page PDF viewer for /pdfs/<id>.
 *
 * Ports the rendering core of pdfMax's ViewerTab into vanilla JS. Renders
 * the document with PDF.js (canvas + selectable text layer), overlays
 * issue bounding boxes from the cached pdfMax issue_map.json, derives
 * a best-effort screen-reader-only semantic HTML tree from the PDF
 * structure tree, and draws SVG connector lines between page overlays
 * and the right-pane issue cards.
 *
 * Deliberately NOT ported in this pass (each is its own feature area):
 *   TTS, annotation tools, search, thumbnails, bookmarks, layer panel,
 *   reading guide, auto-scroll, reflow, focus mode, signature/print.
 *
 * Activation contract (template-side):
 *   <div data-pdf-viewer-host
 *        data-pdf-url="..."
 *        data-issue-map-url="..."
 *        data-document-lang="...">
 *     ... toolbar markup ...
 *     <div data-pdf-viewer-stage>...</div>
 *     ... right-pane issue cards with data-issue-id attributes ...
 *   </div>
 *
 * No Jinja2 inside this file — translatable strings are read from
 * window.pdfViewerI18n, populated by the template.
 */
(function () {
    "use strict";

    var i18n = (typeof window !== "undefined" && window.pdfViewerI18n) || {};

    function t(key, fallback) {
        return Object.prototype.hasOwnProperty.call(i18n, key) ? i18n[key] : fallback;
    }

    // ---------- PDF.js bootstrap ----------------------------------------

    var pdfjsLibPromise = null;

    function loadPdfJs() {
        if (pdfjsLibPromise) return pdfjsLibPromise;
        pdfjsLibPromise = import("/static/vendor/pdfjs/pdf.min.mjs").then(function (mod) {
            mod.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.mjs";
            return mod;
        });
        return pdfjsLibPromise;
    }

    // ---------- Issue clustering (port of issueOverlapGrouping.ts) ------

    var ISSUE_OVERLAP_THRESHOLD = 0.9;

    function bboxArea(b) {
        return Math.max(0, b.x1 - b.x0) * Math.max(0, b.y1 - b.y0);
    }

    function bboxIntersectionArea(a, b) {
        var x0 = Math.max(a.x0, b.x0);
        var y0 = Math.max(a.y0, b.y0);
        var x1 = Math.min(a.x1, b.x1);
        var y1 = Math.min(a.y1, b.y1);
        return Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
    }

    function overlapRatio(a, b) {
        var minArea = Math.min(bboxArea(a), bboxArea(b));
        if (minArea === 0) return 0;
        return bboxIntersectionArea(a, b) / minArea;
    }

    function mergeBboxes(boxes) {
        var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (var i = 0; i < boxes.length; i++) {
            if (boxes[i].x0 < x0) x0 = boxes[i].x0;
            if (boxes[i].y0 < y0) y0 = boxes[i].y0;
            if (boxes[i].x1 > x1) x1 = boxes[i].x1;
            if (boxes[i].y1 > y1) y1 = boxes[i].y1;
        }
        return { x0: x0, y0: y0, x1: x1, y1: y1 };
    }

    function clusterOverlappingIssues(issues, threshold) {
        var t = (typeof threshold === "number") ? threshold : ISSUE_OVERLAP_THRESHOLD;
        var n = issues.length;
        if (n === 0) return [];
        var parent = [];
        var rank = [];
        for (var i = 0; i < n; i++) { parent.push(i); rank.push(0); }
        function find(x) {
            while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
            return x;
        }
        function union(a, b) {
            var ra = find(a), rb = find(b);
            if (ra === rb) return;
            if (rank[ra] < rank[rb]) parent[ra] = rb;
            else if (rank[ra] > rank[rb]) parent[rb] = ra;
            else { parent[rb] = ra; rank[ra]++; }
        }
        for (var i2 = 0; i2 < n; i2++) {
            for (var j = i2 + 1; j < n; j++) {
                if (overlapRatio(issues[i2].bbox, issues[j].bbox) >= t) union(i2, j);
            }
        }
        var groups = new Map();
        for (var k = 0; k < n; k++) {
            var root = find(k);
            var arr = groups.get(root);
            if (arr) arr.push(k); else groups.set(root, [k]);
        }
        var result = [];
        groups.forEach(function (indices) {
            var members = indices.map(function (idx) { return issues[idx]; });
            result.push({
                issues: members,
                mergedBbox: mergeBboxes(members.map(function (m) { return m.bbox; })),
                hasFail: members.some(function (m) { return m.check_result === "FAIL"; }),
            });
        });
        return result;
    }

    // ---------- Structure tree → semantic HTML (best-effort) -----------

    var ROLE_TO_HTML = {
        Document: "article", Part: "section", Art: "article", Sect: "section",
        Div: "div", Aside: "aside",
        H: "h1", H1: "h1", H2: "h2", H3: "h3", H4: "h4", H5: "h5", H6: "h6",
        P: "p", BlockQuote: "blockquote", Quote: "blockquote",
        Note: "aside", Span: "span", Code: "code", Em: "em", Strong: "strong",
        Link: "a",
        L: "ul", LI: "li", Lbl: "span", LBody: "span",
        Table: "table", TR: "tr", TH: "th", TD: "td",
        THead: "thead", TBody: "tbody", TFoot: "tfoot",
        Figure: "figure", Caption: "figcaption",
        TOC: "nav", TOCI: "li", NonStruct: "span",
    };

    function getHeadingLevel(role) {
        if (role === "H" || role === "H1") return 1;
        if (role === "H2") return 2;
        if (role === "H3") return 3;
        if (role === "H4") return 4;
        if (role === "H5") return 5;
        if (role === "H6") return 6;
        return null;
    }

    /**
     * Build a {mcid → text} index from a TextContent.items[] sequence that
     * includes type:'beginMarkedContentProps' / 'endMarkedContent' markers
     * (PDF.js emits these when getTextContent is called with
     * includeMarkedContent: true). Text items between begin/end belong
     * to the most recent open MCID.
     */
    function indexTextByMcid(items) {
        var idx = new Map();
        var stack = [];
        for (var i = 0; i < items.length; i++) {
            var it = items[i];
            if (it.type === "beginMarkedContentProps" || it.type === "beginMarkedContent") {
                if (it.id !== undefined && it.id !== null) stack.push(it.id);
                else stack.push(null);
            } else if (it.type === "endMarkedContent") {
                stack.pop();
            } else if (typeof it.str === "string" && it.str.length > 0) {
                var top = stack.length > 0 ? stack[stack.length - 1] : null;
                if (top) {
                    var prev = idx.get(top) || "";
                    var sep = (prev && !/\s$/.test(prev) && !/^\s/.test(it.str)) ? " " : "";
                    idx.set(top, prev + sep + it.str);
                }
            }
        }
        return idx;
    }

    /**
     * Walk PDF.js's StructTreeNode → SemanticBlock-like tree.
     * Returns an array of {tag, level?, ariaLabel?, lang?, text?, children?}.
     */
    function walkStructTree(node, textIndex) {
        if (!node) return [];
        var role = node.role || "";
        if (role === "Artifact") return [];

        var tag = ROLE_TO_HTML[role] || "span";
        var block = { tag: tag, children: [] };

        var level = getHeadingLevel(role);
        if (level !== null) block.level = level;
        if (node.alt) block.ariaLabel = node.alt;
        if (node.lang) block.lang = node.lang;

        var pendingText = "";
        var hasStructChildren = false;

        if (Array.isArray(node.children)) {
            for (var i = 0; i < node.children.length; i++) {
                var child = node.children[i];
                if (child.type === "content" || child.type === "object") {
                    var text = textIndex.get(child.id);
                    if (text) pendingText += text;
                } else if (child.role) {
                    hasStructChildren = true;
                    if (pendingText.trim()) {
                        block.children.push({ tag: "span", text: pendingText });
                        pendingText = "";
                    }
                    var childBlocks = walkStructTree(child, textIndex);
                    for (var c = 0; c < childBlocks.length; c++) {
                        block.children.push(childBlocks[c]);
                    }
                }
            }
        }

        if (pendingText.trim()) {
            if (hasStructChildren) block.children.push({ tag: "span", text: pendingText });
            else block.text = pendingText;
        }

        if (block.text || block.children.length > 0) return [block];
        return [];
    }

    /**
     * Render a SemanticBlock tree into a target container. The container
     * is the .pdf-viewer-semantic-layer absolute overlay sitting on top of
     * the canvas — transparent visually, but exposes proper HTML structure
     * to screen readers and caret browsing (same approach as pdfMax).
     */
    function renderSemanticBlocks(blocks, container) {
        container.innerHTML = "";
        for (var i = 0; i < blocks.length; i++) {
            container.appendChild(renderBlock(blocks[i]));
        }
    }

    function renderBlock(block) {
        var el = document.createElement(block.tag);
        if (block.ariaLabel) el.setAttribute("aria-label", block.ariaLabel);
        if (block.lang) el.setAttribute("lang", block.lang);
        if (block.level && /^h[1-6]$/.test(block.tag)) {
            el.setAttribute("aria-level", String(block.level));
        }
        if (block.text) el.textContent = block.text;
        if (block.children) {
            for (var i = 0; i < block.children.length; i++) {
                el.appendChild(renderBlock(block.children[i]));
            }
        }
        return el;
    }

    // ---------- Viewer App ---------------------------------------------

    /**
     * Construct a viewer instance. One viewer per host element on the page.
     */
    function PdfViewer(host) {
        this.host = host;
        this.pdfUrl = host.getAttribute("data-pdf-url");
        this.issueMapUrl = host.getAttribute("data-issue-map-url");
        this.documentLang = host.getAttribute("data-document-lang") || null;

        this.stage = host.querySelector("[data-pdf-viewer-stage]");
        this.canvas = host.querySelector("[data-pdf-viewer-canvas]");
        this.textLayerEl = host.querySelector("[data-pdf-viewer-text-layer]");
        this.overlayEl = host.querySelector("[data-pdf-viewer-overlay-layer]");
        this.semanticEl = host.querySelector("[data-pdf-viewer-semantic-layer]");
        this.connectorEl = host.querySelector("[data-pdf-viewer-connector-layer]");
        this.statusEl = host.querySelector("[data-pdf-viewer-status]");
        this.issueListEl = host.querySelector("[data-pdf-viewer-issue-list]");
        this.issueListEmptyEl = host.querySelector("[data-pdf-viewer-issue-list-empty]");
        this.pageInputEl = host.querySelector("[data-pdf-viewer-page-input]");
        this.pageTotalEl = host.querySelector("[data-pdf-viewer-page-total]");
        this.zoomLabelEl = host.querySelector("[data-pdf-viewer-zoom-label]");
        this.prevBtn = host.querySelector("[data-pdf-viewer-prev]");
        this.nextBtn = host.querySelector("[data-pdf-viewer-next]");
        this.zoomInBtn = host.querySelector("[data-pdf-viewer-zoom-in]");
        this.zoomOutBtn = host.querySelector("[data-pdf-viewer-zoom-out]");
        this.zoomResetBtn = host.querySelector("[data-pdf-viewer-zoom-reset]");

        this.pdfjs = null;
        this.pdfDoc = null;
        this.pageNum = 1;
        this.numPages = 0;
        this.scale = 1.5;
        this.minScale = 0.5;
        this.maxScale = 3.0;
        this.zoomStep = 0.25;
        this.viewport = null;          // viewport at current scale
        this.pageDimensions = null;    // {pageNum: [w, h]} from issue_map (PDF user-space units)
        this.issuesByPage = new Map(); // page → [issue]
        this.allIssues = [];
        this.selectedIssueId = null;
        this.renderTask = null;
        this.connectorRaf = 0;
        this.boundUpdateConnectors = this._scheduleConnectorRedraw.bind(this);
    }

    PdfViewer.prototype.init = function () {
        var self = this;
        this._wireToolbar();
        this._wireScrollListeners();
        this._wireSidebarHandlers();
        this._setStatus(t("loading", "Loading PDF…"));

        return loadPdfJs().then(function (lib) {
            self.pdfjs = lib;
            // The overlays are an enhancement; the document is the point.
            // Loading them together in a Promise.all meant an issue-map
            // failure surfaced as "Failed to load the PDF" — the one
            // message guaranteed to send you looking in the wrong place.
            return self._loadDocument();
        }).then(function () {
            return self._loadIssueMap().catch(function (err) {
                console.warn("[pdf_viewer_app] issue map unavailable", err);
            });
        }).then(function () {
            return self.renderPage(1);
        }).then(function () {
            self._setStatus("");
            // Deferred redraw: rAF can fire before web fonts settle their
            // metrics, which shifts card rects. pdfMax's React version uses
            // a 100ms setTimeout for the same reason.
            setTimeout(function () { self._scheduleConnectorRedraw(); }, 250);
        }).catch(function (err) {
            console.error("[pdf_viewer_app] failed to initialise", err);
            // Name the cause. PDF.js raises typed errors — a password, a
            // truncated file, an HTTP status — and a bare "failed to load"
            // throws that away, leaving the user nothing to act on.
            self._setStatus(
                t("error-load", "Failed to load the PDF.") + " " + describeError(err)
            );
        });
    };

    /* A human-readable reason for a PDF.js failure.
     *
     * PDF.js reports the useful part in `name` (PasswordException,
     * InvalidPDFException, MissingPDFException, UnexpectedResponseException)
     * and often a status on the latter. Surfacing both turns an unactionable
     * "it failed" into something a user can either fix or report. */
    function describeError(err) {
        if (!err) return "";
        var name = err.name || "";
        var status = err.status ? " (HTTP " + err.status + ")" : "";
        if (name === "PasswordException") {
            return "The PDF is password-protected.";
        }
        if (name === "InvalidPDFException") {
            return "The file is not a readable PDF, or it is damaged.";
        }
        if (name === "MissingPDFException") {
            return "The PDF could not be fetched from the server" + status + ".";
        }
        if (name === "UnexpectedResponseException") {
            return "The server did not return the PDF" + status + ".";
        }
        return (name ? name + ": " : "") + (err.message || String(err));
    }

    PdfViewer.prototype._loadDocument = function () {
        var self = this;
        return this.pdfjs.getDocument({ url: this.pdfUrl }).promise.then(function (doc) {
            self.pdfDoc = doc;
            self.numPages = doc.numPages;
            if (self.pageInputEl) {
                self.pageInputEl.max = String(self.numPages);
                self.pageInputEl.value = "1";
            }
            if (self.pageTotalEl) self.pageTotalEl.textContent = String(self.numPages);
        });
    };

    PdfViewer.prototype._loadIssueMap = function () {
        var self = this;
        if (!this.issueMapUrl) return Promise.resolve();
        return fetch(this.issueMapUrl, { credentials: "same-origin" }).then(function (r) {
            if (!r.ok) return null;
            return r.json();
        }).then(function (data) {
            if (!data || !Array.isArray(data.issues)) return;
            self.pageDimensions = data.page_dimensions || {};
            for (var i = 0; i < data.issues.length; i++) {
                var raw = data.issues[i];
                // Full record — preserves element_index/element_tag and
                // document-level issues (page=null, bbox=null) for the
                // sidebar. Bbox is normalised to {x0,y0,x1,y1} when
                // present so overlay code keeps its current shape.
                var issue = {
                    id: raw.id,
                    check_name: raw.check_name,
                    check_result: raw.check_result,
                    element_index: (raw.element_index === undefined) ? null : raw.element_index,
                    element_tag: raw.element_tag || null,
                    detail: raw.detail || "",
                    page: raw.page || null,
                    bbox: null,
                };
                if (raw.bbox && raw.bbox.length === 4) {
                    issue.bbox = { x0: raw.bbox[0], y0: raw.bbox[1], x1: raw.bbox[2], y1: raw.bbox[3] };
                }
                self.allIssues.push(issue);
                // Only issues with both a page and a bbox get an overlay.
                if (issue.page && issue.bbox) {
                    var arr = self.issuesByPage.get(issue.page);
                    if (!arr) { arr = []; self.issuesByPage.set(issue.page, arr); }
                    arr.push(issue);
                }
            }
        }).then(function () {
            self._renderIssueList();
        }).catch(function (err) {
            // Silent — overlays just won't render. Audit may not have run.
            console.info("[pdf_viewer_app] no issue-map available:", err);
            self._renderIssueList();
        });
    };

    /**
     * Render the right-pane issue list from issue_map.json. Cards mirror
     * pdfMax's ViewerSidebar 1:1 — single-element groups render as flat
     * cards with element-tag/index lines; multi-element groups collapse
     * into an accordion summary. Cards keep data-issue-id matching the
     * overlay rect on the canvas so the connector-line code resolves
     * both ends.
     *
     * The auto_a11y rich-report violation list below this panel comes
     * from a different audit engine and can disagree; we deliberately
     * don't merge.
     */
    PdfViewer.prototype._renderIssueList = function () {
        if (!this.issueListEl) return;
        this.issueListEl.innerHTML = "";

        if (this.allIssues.length === 0) {
            if (this.issueListEmptyEl) this.issueListEmptyEl.hidden = false;
            return;
        }
        if (this.issueListEmptyEl) this.issueListEmptyEl.hidden = true;

        var groups = groupIssues(this.allIssues);
        for (var g = 0; g < groups.length; g++) {
            var grp = groups[g];
            var node = grp.isMulti
                ? this._buildIssueGroupCard(grp)
                : this._buildIssueCard(grp.issues[0]);
            this.issueListEl.appendChild(node);
        }
    };

    /**
     * Render the small subset of Markdown that pdfMax's audit details emit
     * (paragraphs separated by blank lines, ``-``/``*`` bullet lists, inline
     * ``**bold**``, ``*italic*``, and ``` `code` ```) into ``container`` as
     * real DOM nodes. Plain text is appended via ``textContent``, so any
     * user-controlled metadata in the audit string is auto-escaped — no
     * ``innerHTML`` is ever assigned.
     */
    function renderDetailMarkdown(text, container) {
        var blocks = String(text).split(/\n\s*\n/);
        for (var b = 0; b < blocks.length; b++) {
            var block = blocks[b].replace(/\s+$/, "");
            if (!block.trim()) continue;
            var lines = block.split("\n");
            var isList = lines.length > 0 && lines.every(function (l) {
                return /^\s*[-*]\s+/.test(l);
            });
            if (isList) {
                var ul = document.createElement("ul");
                ul.className = "pdf-viewer-issue-card-detail-list";
                for (var i = 0; i < lines.length; i++) {
                    var item = lines[i].replace(/^\s*[-*]\s+/, "");
                    var li = document.createElement("li");
                    appendInlineMarkdown(item, li);
                    ul.appendChild(li);
                }
                container.appendChild(ul);
            } else {
                var p = document.createElement("p");
                p.className = "pdf-viewer-issue-card-detail";
                for (var l = 0; l < lines.length; l++) {
                    if (l > 0) p.appendChild(document.createElement("br"));
                    appendInlineMarkdown(lines[l], p);
                }
                container.appendChild(p);
            }
        }
    }

    /**
     * Inline-pass tokeniser: matches `code`, **bold**, *italic* in left-to-
     * right order and appends the corresponding DOM nodes; everything else
     * is appended as plain text. Non-greedy so a single ``*`` inside a
     * ``**...**`` pair binds to the strong.
     */
    function appendInlineMarkdown(text, parent) {
        var re = /(`([^`]+)`)|(\*\*([^*]+?)\*\*)|(\*([^*]+?)\*)/g;
        var lastIndex = 0;
        var m;
        while ((m = re.exec(text)) !== null) {
            if (m.index > lastIndex) {
                parent.appendChild(document.createTextNode(text.slice(lastIndex, m.index)));
            }
            var node;
            if (m[1]) {
                node = document.createElement("code");
                node.textContent = m[2];
            } else if (m[3]) {
                node = document.createElement("strong");
                node.textContent = m[4];
            } else {
                node = document.createElement("em");
                node.textContent = m[6];
            }
            parent.appendChild(node);
            lastIndex = re.lastIndex;
        }
        if (lastIndex < text.length) {
            parent.appendChild(document.createTextNode(text.slice(lastIndex)));
        }
    }

    // ---------- Issue grouping (port of pdfMax/src/utils/issueGrouping.ts) ---

    /**
     * Bucket issues by (check_name, detail) so multi-element problems
     * collapse into a single accordion card. Returns an array of
     * {groupKey, checkName, checkResult, detail, issues, isMulti} in
     * insertion order — the first occurrence of each (check, detail)
     * pair anchors the group's position in the rendered list, which
     * matches pdfMax's ViewerSidebar behaviour.
     */
    function groupIssues(issues) {
        var map = new Map();
        var order = [];
        for (var i = 0; i < issues.length; i++) {
            var issue = issues[i];
            var key = (issue.check_name || "") + "\x00" + (issue.detail || "");
            var arr = map.get(key);
            if (arr) {
                arr.push(issue);
            } else {
                arr = [issue];
                map.set(key, arr);
                order.push(key);
            }
        }
        var out = [];
        for (var j = 0; j < order.length; j++) {
            var k = order[j];
            var groupArr = map.get(k);
            out.push({
                groupKey: k,
                checkName: groupArr[0].check_name,
                checkResult: groupArr[0].check_result,
                detail: groupArr[0].detail,
                issues: groupArr,
                isMulti: groupArr.length > 1,
            });
        }
        return out;
    }

    /**
     * Resolve the report URL once per card render via the data-attribute
     * on the issue-list container. Returns "" when not set, in which
     * case the "View in report" button is suppressed.
     */
    PdfViewer.prototype._reportUrl = function () {
        if (!this.issueListEl) return "";
        return this.issueListEl.getAttribute("data-pdfmax-report-url") || "";
    };

    /**
     * Build the "View in report" button. Returns null when no report
     * URL is available (e.g. test fixtures, or audits that pre-date
     * the pdfmax-report cache).
     */
    PdfViewer.prototype._buildViewInReportButton = function (checkName) {
        var reportUrl = this._reportUrl();
        if (!reportUrl) return null;
        var anchor = document.createElement("a");
        anchor.className = "btn btn-outline-brand btn-sm pdf-viewer-issue-view-in-report";
        anchor.href = reportUrl + "#check=" + encodeURIComponent(checkName);
        anchor.textContent = t("issue-view-in-report", "View in report");
        var ariaTpl = t("issue-view-in-report-aria", 'View "{CHECK}" in the pdfMax report');
        anchor.setAttribute("aria-label", ariaTpl.replace("{CHECK}", checkName));
        return anchor;
    };

    /**
     * Build the `[<index>] <tag>` line for an issue. Returns null when
     * either field is missing (the line is suppressed in that case).
     */
    PdfViewer.prototype._buildElementLine = function (issue) {
        if (issue.element_index == null || !issue.element_tag) return null;
        var span = document.createElement("span");
        span.className = "pdf-viewer-issue-card-element";
        var tpl = t("issue-element", "[{INDEX}] {TAG}");
        span.textContent = tpl
            .replace("{INDEX}", String(issue.element_index))
            .replace("{TAG}", String(issue.element_tag));
        return span;
    };

    PdfViewer.prototype._buildIssueCard = function (issue) {
        var isFail = (issue.check_result === "FAIL");

        var li = document.createElement("li");
        li.className = "pdf-viewer-issue-card " + (isFail ? "is-fail" : "is-warn");
        li.setAttribute("data-issue-id", issue.id);

        var details = document.createElement("details");
        details.className = "pdf-viewer-issue-details";
        details.setAttribute("data-check-result", issue.check_result);
        if (issue.check_name) details.setAttribute("data-check-name", issue.check_name);

        var summary = document.createElement("summary");
        summary.className = "pdf-viewer-issue-summary";

        var resultBadge = document.createElement("span");
        resultBadge.className = "badge " + (isFail ? "badge-high" : "badge-medium");
        resultBadge.textContent = isFail
            ? t("issue-result-fail", "Fail")
            : t("issue-result-warn", "Warn");
        summary.appendChild(resultBadge);

        var name = document.createElement("span");
        name.className = "pdf-viewer-issue-card-name";
        name.textContent = issue.check_name;
        summary.appendChild(name);

        if (issue.page) {
            var pageTag = document.createElement("span");
            pageTag.className = "pdf-viewer-issue-card-page";
            pageTag.textContent = "p." + issue.page;
            summary.appendChild(pageTag);
        }

        details.appendChild(summary);

        var body = document.createElement("div");
        body.className = "pdf-viewer-issue-card-body";

        // Element-tag line OR document-level marker — never both.
        var elementLine = this._buildElementLine(issue);
        if (elementLine) {
            body.appendChild(elementLine);
        } else if (issue.page == null && issue.bbox == null) {
            var docBadge = document.createElement("span");
            docBadge.className = "badge badge-neutral pdf-viewer-issue-card-document-level";
            docBadge.textContent = t("issue-document-level", "Document-level");
            body.appendChild(docBadge);
        }

        if (issue.detail) {
            renderDetailMarkdown(issue.detail, body);
        }

        var viewBtn = this._buildViewInReportButton(issue.check_name);
        if (viewBtn) body.appendChild(viewBtn);

        details.appendChild(body);
        li.appendChild(details);

        return li;
    };

    /**
     * Multi-element accordion card. Header shows severity badge, check
     * name, and "{COUNT} elements". Expanded body shows each child's
     * `[<index>] <tag>` and `p.<page>` on its own row.
     */
    PdfViewer.prototype._buildIssueGroupCard = function (group) {
        var isFail = (group.checkResult === "FAIL");

        var li = document.createElement("li");
        li.className = "pdf-viewer-issue-card pdf-viewer-issue-card-group "
            + (isFail ? "is-fail" : "is-warn");
        li.setAttribute("data-group-key", group.groupKey);

        var details = document.createElement("details");
        details.className = "pdf-viewer-issue-details";
        details.setAttribute("data-check-result", group.checkResult);
        if (group.checkName) details.setAttribute("data-check-name", group.checkName);

        var summary = document.createElement("summary");
        summary.className = "pdf-viewer-issue-summary";

        var resultBadge = document.createElement("span");
        resultBadge.className = "badge " + (isFail ? "badge-high" : "badge-medium");
        resultBadge.textContent = isFail
            ? t("issue-result-fail", "Fail")
            : t("issue-result-warn", "Warn");
        summary.appendChild(resultBadge);

        var name = document.createElement("span");
        name.className = "pdf-viewer-issue-card-name";
        name.textContent = group.checkName;
        summary.appendChild(name);

        var countSpan = document.createElement("span");
        countSpan.className = "pdf-viewer-issue-card-count";
        var countTpl = t("issue-group-count", "{COUNT} elements");
        countSpan.textContent = countTpl.replace("{COUNT}", String(group.issues.length));
        summary.appendChild(countSpan);

        details.appendChild(summary);

        var body = document.createElement("div");
        body.className = "pdf-viewer-issue-card-body";

        // Detail text once at the top of the group (it's identical
        // across all members, since "detail" is part of the group key).
        if (group.detail) {
            renderDetailMarkdown(group.detail, body);
        }

        var viewBtn = this._buildViewInReportButton(group.checkName);
        if (viewBtn) body.appendChild(viewBtn);

        // Per-element rows.
        var ul = document.createElement("ul");
        ul.className = "pdf-viewer-issue-group-children list-unstyled mb-0";
        for (var i = 0; i < group.issues.length; i++) {
            var child = group.issues[i];
            var row = document.createElement("li");
            row.className = "pdf-viewer-issue-group-child";
            row.setAttribute("data-issue-id", child.id);

            var elementLine = this._buildElementLine(child);
            if (elementLine) {
                row.appendChild(elementLine);
            } else if (child.page == null && child.bbox == null) {
                var docBadge = document.createElement("span");
                docBadge.className = "badge badge-neutral pdf-viewer-issue-card-document-level";
                docBadge.textContent = t("issue-document-level", "Document-level");
                row.appendChild(docBadge);
            }

            if (child.page) {
                var pageTag = document.createElement("span");
                pageTag.className = "pdf-viewer-issue-card-page";
                pageTag.textContent = "p." + child.page;
                row.appendChild(pageTag);
            }

            ul.appendChild(row);
        }
        body.appendChild(ul);

        details.appendChild(body);
        li.appendChild(details);

        return li;
    };

    PdfViewer.prototype.renderPage = function (pageNum) {
        var self = this;
        if (!this.pdfDoc) return Promise.resolve();
        if (pageNum < 1) pageNum = 1;
        if (pageNum > this.numPages) pageNum = this.numPages;
        this.pageNum = pageNum;
        if (this.pageInputEl) this.pageInputEl.value = String(pageNum);
        this._updateNavButtons();

        if (this.renderTask) {
            this.renderTask.cancel();
            this.renderTask = null;
        }

        return this.pdfDoc.getPage(pageNum).then(function (page) {
            var viewport = page.getViewport({ scale: self.scale });
            self.viewport = viewport;
            self._sizeStage(viewport);
            return self._renderCanvas(page, viewport).then(function () {
                return Promise.all([
                    self._renderTextLayer(page, viewport),
                    self._renderOverlay(page, viewport),
                    self._renderSemanticLayer(page),
                ]);
            });
        }).then(function () {
            self._scheduleConnectorRedraw();
        });
    };

    PdfViewer.prototype._sizeStage = function (viewport) {
        var w = Math.floor(viewport.width);
        var h = Math.floor(viewport.height);
        this.stage.style.width = w + "px";
        this.stage.style.height = h + "px";

        var dpr = window.devicePixelRatio || 1;
        this.canvas.width = Math.floor(w * dpr);
        this.canvas.height = Math.floor(h * dpr);
        this.canvas.style.width = w + "px";
        this.canvas.style.height = h + "px";

        if (this.textLayerEl) {
            this.textLayerEl.style.width = w + "px";
            this.textLayerEl.style.height = h + "px";
            this.textLayerEl.innerHTML = "";
        }
        if (this.overlayEl) {
            this.overlayEl.setAttribute("viewBox", "0 0 " + w + " " + h);
            this.overlayEl.setAttribute("width", String(w));
            this.overlayEl.setAttribute("height", String(h));
            while (this.overlayEl.firstChild) this.overlayEl.removeChild(this.overlayEl.firstChild);
        }
        if (this.semanticEl) this.semanticEl.innerHTML = "";
    };

    PdfViewer.prototype._renderCanvas = function (page, viewport) {
        var ctx = this.canvas.getContext("2d");
        var dpr = window.devicePixelRatio || 1;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.renderTask = page.render({ canvasContext: ctx, viewport: viewport });
        return this.renderTask.promise.then(function () { /* ok */ }, function (err) {
            // Silence cancellation errors from rapid scale/page changes.
            if (err && err.name !== "RenderingCancelledException") throw err;
        });
    };

    PdfViewer.prototype._renderTextLayer = function (page, viewport) {
        var self = this;
        if (!this.textLayerEl) return Promise.resolve();
        return page.getTextContent({ includeMarkedContent: false }).then(function (textContent) {
            self.textLayerEl.innerHTML = "";
            var TextLayer = self.pdfjs.TextLayer;
            if (TextLayer) {
                var tl = new TextLayer({
                    textContentSource: textContent,
                    container: self.textLayerEl,
                    viewport: viewport,
                });
                return tl.render();
            }
            // Older pdf.js fallback path
            if (typeof self.pdfjs.renderTextLayer === "function") {
                var task = self.pdfjs.renderTextLayer({
                    textContent: textContent,
                    container: self.textLayerEl,
                    viewport: viewport,
                    textDivs: [],
                });
                return task.promise || Promise.resolve();
            }
            return Promise.resolve();
        }).catch(function (err) {
            console.warn("[pdf_viewer_app] text layer render failed:", err);
        });
    };

    /**
     * Convert a PDF-coordinates issue bbox to viewport pixel rect.
     * issue_map.json carries PDF user-space units (bottom-left origin)
     * keyed against page_dimensions[page] = [width, height]. PDF.js's
     * viewport.convertToViewportRectangle handles the flip + scale.
     */
    PdfViewer.prototype._bboxToViewportRect = function (bbox, viewport) {
        var rect = viewport.convertToViewportRectangle([bbox.x0, bbox.y0, bbox.x1, bbox.y1]);
        var x0 = Math.min(rect[0], rect[2]);
        var y0 = Math.min(rect[1], rect[3]);
        var x1 = Math.max(rect[0], rect[2]);
        var y1 = Math.max(rect[1], rect[3]);
        return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    };

    PdfViewer.prototype._renderOverlay = function (page, viewport) {
        if (!this.overlayEl) return Promise.resolve();
        var issues = this.issuesByPage.get(this.pageNum) || [];
        if (issues.length === 0) return Promise.resolve();

        var clusters = clusterOverlappingIssues(issues);
        var ns = "http://www.w3.org/2000/svg";
        for (var i = 0; i < clusters.length; i++) {
            var cluster = clusters[i];
            var rect = this._bboxToViewportRect(cluster.mergedBbox, viewport);
            var isCluster = cluster.issues.length > 1;
            var node = document.createElementNS(ns, "rect");
            node.setAttribute("x", String(rect.x));
            node.setAttribute("y", String(rect.y));
            node.setAttribute("width", String(rect.w));
            node.setAttribute("height", String(rect.h));
            node.setAttribute("class",
                "pdf-issue-overlay" +
                (isCluster ? " pdf-issue-cluster" : "") +
                (cluster.hasFail ? " is-fail" : " is-warn")
            );
            if (isCluster) {
                var ids = cluster.issues.map(function (m) { return m.id; });
                node.setAttribute("data-cluster-ids", ids.join(","));
                var label = t("overlay-cluster-aria", "{count} issues at this location");
                node.setAttribute("aria-label", label.replace("{count}", String(cluster.issues.length)));
            } else {
                node.setAttribute("data-issue-id", cluster.issues[0].id);
                node.setAttribute("aria-label",
                    cluster.issues[0].check_name +
                    " (" + cluster.issues[0].check_result + ")"
                );
            }
            node.setAttribute("tabindex", "0");
            this._wireOverlayNode(node, cluster);
            this.overlayEl.appendChild(node);
        }
        this._reflectSelection();
        return Promise.resolve();
    };

    PdfViewer.prototype._wireOverlayNode = function (node, cluster) {
        var self = this;
        var firstId = cluster.issues[0].id;
        var handler = function (ev) {
            ev.preventDefault();
            // Cluster: select the first issue and let the user pick from the
            // sidebar (in this MVP we don't ship a cluster picker modal —
            // sidebar selection is sufficient because every cluster member
            // is also a sidebar card).
            self.selectIssue(firstId, { scrollSidebar: true });
        };
        node.addEventListener("click", handler);
        node.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter" || ev.key === " ") handler(ev);
        });
    };

    PdfViewer.prototype._renderSemanticLayer = function (page) {
        var self = this;
        if (!this.semanticEl) return Promise.resolve();
        return Promise.all([
            page.getStructTree(),
            page.getTextContent({ includeMarkedContent: true }),
        ]).then(function (results) {
            var structTree = results[0];
            var textContent = results[1];
            self.semanticEl.innerHTML = "";
            self.semanticEl.setAttribute("role", "document");
            var label = t("semantic-layer-aria", "Page {page} content");
            self.semanticEl.setAttribute("aria-label", label.replace("{page}", String(self.pageNum)));
            if (self.documentLang) self.semanticEl.setAttribute("lang", self.documentLang);

            if (!structTree) {
                // Untagged PDF — best-effort fallback: emit each text run as
                // its own <p>. Better than nothing; doesn't try to infer headings.
                var p = document.createElement("p");
                p.textContent = (textContent.items || []).map(function (it) {
                    return it.str || "";
                }).join(" ").replace(/\s+/g, " ").trim();
                if (p.textContent) self.semanticEl.appendChild(p);
                return;
            }

            var textIndex = indexTextByMcid(textContent.items || []);
            var blocks = walkStructTree(structTree, textIndex);
            renderSemanticBlocks(blocks, self.semanticEl);
        }).catch(function (err) {
            console.warn("[pdf_viewer_app] semantic layer render failed:", err);
        });
    };

    // ---------- Selection + connector lines ----------------------------

    PdfViewer.prototype.selectIssue = function (issueId, options) {
        options = options || {};
        var prev = this.selectedIssueId;
        this.selectedIssueId = issueId;
        this._reflectSelection();

        // If the issue lives on a different page, jump there first.
        if (issueId) {
            var found = this._findIssueById(issueId);
            if (found && found.page && found.page !== this.pageNum) {
                this.renderPage(found.page);
            }
        }

        if (options.scrollSidebar && issueId) {
            var card = this._findCard(issueId);
            if (card && typeof card.scrollIntoView === "function") {
                card.scrollIntoView({ block: "nearest", behavior: "smooth" });
                // The card LI is non-focusable; focus the inner <summary>
                // so keyboard users land on something they can act on.
                var summary = card.querySelector("summary");
                if (summary && typeof summary.focus === "function") summary.focus();
            }
        } else if (options.scrollPage && issueId) {
            var overlay = this._findOverlay(issueId);
            if (overlay && typeof overlay.scrollIntoView === "function") {
                overlay.scrollIntoView({ block: "center", behavior: "smooth" });
            }
        }

        if (prev !== issueId) this._scheduleConnectorRedraw();
    };

    PdfViewer.prototype._reflectSelection = function () {
        // Overlays
        var overlays = this.overlayEl ? this.overlayEl.querySelectorAll(".pdf-issue-overlay") : [];
        for (var i = 0; i < overlays.length; i++) {
            var el = overlays[i];
            var id = el.getAttribute("data-issue-id");
            var clusterIds = el.getAttribute("data-cluster-ids");
            var matched = (id && id === this.selectedIssueId) ||
                (clusterIds && clusterIds.split(",").indexOf(this.selectedIssueId) !== -1);
            el.classList.toggle("is-selected", !!matched);
        }
        // Cards
        var cards = this.host.querySelectorAll("[data-issue-id]");
        for (var c = 0; c < cards.length; c++) {
            var card = cards[c];
            if (card.classList.contains("pdf-issue-overlay")) continue;
            if (card.classList.contains("pdf-issue-cluster")) continue;
            var matched = card.getAttribute("data-issue-id") === this.selectedIssueId;
            card.classList.toggle("is-selected", matched);
            // Auto-open the selected card's <details> so the body is visible
            // even when the user reached it via overlay click rather than
            // by clicking the summary directly.
            if (matched) {
                var detailsEl = card.querySelector("details.pdf-viewer-issue-details");
                if (detailsEl && !detailsEl.open) detailsEl.open = true;
            }
        }
    };

    PdfViewer.prototype._findIssueById = function (id) {
        var iter = this.issuesByPage.values();
        for (var entry = iter.next(); !entry.done; entry = iter.next()) {
            for (var i = 0; i < entry.value.length; i++) {
                if (entry.value[i].id === id) return entry.value[i];
            }
        }
        return null;
    };

    PdfViewer.prototype._findCard = function (id) {
        // Sidebar cards are tagged with data-issue-id; overlays carry the
        // same attribute, so exclude them with the negative class selectors.
        return this.host.querySelector(
            "[data-issue-id=\"" + cssEscape(id) + "\"]:not(.pdf-issue-overlay):not(.pdf-issue-cluster)"
        );
    };

    PdfViewer.prototype._findOverlay = function (id) {
        if (!this.overlayEl) return null;
        var direct = this.overlayEl.querySelector(
            ".pdf-issue-overlay[data-issue-id=\"" + cssEscape(id) + "\"]"
        );
        if (direct) return direct;
        var clusters = this.overlayEl.querySelectorAll(".pdf-issue-cluster");
        for (var i = 0; i < clusters.length; i++) {
            var ids = clusters[i].getAttribute("data-cluster-ids");
            if (ids && ids.split(",").indexOf(id) !== -1) return clusters[i];
        }
        return null;
    };

    PdfViewer.prototype._scheduleConnectorRedraw = function () {
        var self = this;
        if (this.connectorRaf) cancelAnimationFrame(this.connectorRaf);
        this.connectorRaf = requestAnimationFrame(function () {
            self._drawConnectors();
        });
    };

    PdfViewer.prototype._drawConnectors = function () {
        if (!this.connectorEl) return;
        while (this.connectorEl.firstChild) this.connectorEl.removeChild(this.connectorEl.firstChild);

        var hostRect = this.host.getBoundingClientRect();
        this.connectorEl.setAttribute("width", String(Math.floor(hostRect.width)));
        this.connectorEl.setAttribute("height", String(Math.floor(hostRect.height)));
        this.connectorEl.setAttribute("viewBox",
            "0 0 " + Math.floor(hostRect.width) + " " + Math.floor(hostRect.height));

        var issuesOnPage = this.issuesByPage.get(this.pageNum) || [];
        if (issuesOnPage.length === 0) return;

        // Stage-wrap is the PDF page's scrollable viewport. Lines are dropped
        // when the overlay leaves it. Lines for off-screen cards keep their
        // full trajectory, but an SVG <clipPath> hides any portion that would
        // render above the stage-wrap top or below its bottom — so the line
        // appears to "run off" the edge of the PDF view in its true direction.
        var stageWrapRect = this.stage && this.stage.parentElement
            ? this.stage.parentElement.getBoundingClientRect()
            : hostRect;

        var ns = "http://www.w3.org/2000/svg";

        // Build a clipPath whose rect spans the full host width but only the
        // stage-wrap's vertical band (in host coords). Lines are appended to
        // a <g> that references this clip, so any segment whose y is outside
        // [stageTop, stageBottom] is invisible — but the line's geometry
        // (and therefore its visible angle) is unchanged.
        var clipId = "pdf-viewer-connector-clip";
        var defs = document.createElementNS(ns, "defs");
        var clipPath = document.createElementNS(ns, "clipPath");
        clipPath.setAttribute("id", clipId);
        var clipRect = document.createElementNS(ns, "rect");
        clipRect.setAttribute("x", "0");
        clipRect.setAttribute("y", String(stageWrapRect.top - hostRect.top));
        clipRect.setAttribute("width", String(Math.floor(hostRect.width)));
        clipRect.setAttribute("height", String(stageWrapRect.height));
        clipPath.appendChild(clipRect);
        defs.appendChild(clipPath);
        this.connectorEl.appendChild(defs);

        var lineGroup = document.createElementNS(ns, "g");
        lineGroup.setAttribute("clip-path", "url(#" + clipId + ")");
        this.connectorEl.appendChild(lineGroup);
        for (var i = 0; i < issuesOnPage.length; i++) {
            var issue = issuesOnPage[i];
            var overlay = this._findOverlay(issue.id);
            var card = this._findCard(issue.id);
            if (!overlay || !card) continue;

            var orect = overlay.getBoundingClientRect();
            var crect = card.getBoundingClientRect();

            // Visibility gate: only the overlay endpoint is clipped. If the
            // overlay is outside the PDF's stage-wrap viewport, drop the line.
            // The card endpoint is unrestricted so every visible-on-PDF issue
            // still draws, even when its card is scrolled out of the sidebar.
            if (orect.bottom < stageWrapRect.top || orect.top > stageWrapRect.bottom) continue;
            var isSelected = (this.selectedIssueId === issue.id);

            var x1 = (orect.right - hostRect.left);
            var y1 = (orect.top + orect.bottom) / 2 - hostRect.top;
            var x2 = (crect.left - hostRect.left);
            var y2 = (crect.top + crect.bottom) / 2 - hostRect.top;
            // If overlay extends past the card (overlapping the right pane),
            // use overlay's left edge instead — same heuristic as pdfMax.
            if (orect.right > crect.left) {
                x1 = orect.left - hostRect.left;
            }

            var isFail = (issue.check_result === "FAIL");
            var opacity = isSelected ? 0.9
                : (this.selectedIssueId ? 0.25 : 0.5);
            var className = "pdf-connector-line"
                + (isFail ? " is-fail" : " is-warn")
                + (isSelected ? " is-selected" : "");

            // Three stacked lines for a high-contrast outline (matches pdfMax).
            this._appendLine(lineGroup, ns, x1, y1, x2, y2, isSelected ? 7 : 5, className + " is-outer", opacity);
            this._appendLine(lineGroup, ns, x1, y1, x2, y2, isSelected ? 5 : 3, className + " is-mid",   opacity);
            this._appendLine(lineGroup, ns, x1, y1, x2, y2, isSelected ? 3 : 1, className + " is-inner", opacity);
        }
    };

    PdfViewer.prototype._appendLine = function (parent, ns, x1, y1, x2, y2, w, cls, opacity) {
        var line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(x1));
        line.setAttribute("y1", String(y1));
        line.setAttribute("x2", String(x2));
        line.setAttribute("y2", String(y2));
        line.setAttribute("class", cls);
        line.setAttribute("stroke-width", String(w));
        line.setAttribute("stroke-linecap", "round");
        line.setAttribute("opacity", String(opacity));
        parent.appendChild(line);
    };

    // ---------- Toolbar + sidebar wiring -------------------------------

    PdfViewer.prototype._wireToolbar = function () {
        var self = this;
        if (this.prevBtn) this.prevBtn.addEventListener("click", function () {
            self.renderPage(self.pageNum - 1);
        });
        if (this.nextBtn) this.nextBtn.addEventListener("click", function () {
            self.renderPage(self.pageNum + 1);
        });
        if (this.pageInputEl) this.pageInputEl.addEventListener("change", function () {
            var v = parseInt(self.pageInputEl.value, 10);
            if (Number.isFinite(v)) self.renderPage(v);
        });
        if (this.zoomInBtn) this.zoomInBtn.addEventListener("click", function () {
            self._setScale(self.scale + self.zoomStep);
        });
        if (this.zoomOutBtn) this.zoomOutBtn.addEventListener("click", function () {
            self._setScale(self.scale - self.zoomStep);
        });
        if (this.zoomResetBtn) this.zoomResetBtn.addEventListener("click", function () {
            self._setScale(1.5);
        });
    };

    PdfViewer.prototype._setScale = function (newScale) {
        if (newScale < this.minScale) newScale = this.minScale;
        if (newScale > this.maxScale) newScale = this.maxScale;
        if (newScale === this.scale) return;
        this.scale = newScale;
        if (this.zoomLabelEl) this.zoomLabelEl.textContent = Math.round(newScale * 100) + "%";
        this.renderPage(this.pageNum);
    };

    PdfViewer.prototype._updateNavButtons = function () {
        if (this.prevBtn) this.prevBtn.disabled = (this.pageNum <= 1);
        if (this.nextBtn) this.nextBtn.disabled = (this.pageNum >= this.numPages);
    };

    PdfViewer.prototype._wireScrollListeners = function () {
        var self = this;
        var handler = function () { self._scheduleConnectorRedraw(); };
        // Scroll on host, the right-pane card list, and the window itself.
        this.host.addEventListener("scroll", handler, true);
        window.addEventListener("scroll", handler, { passive: true });
        window.addEventListener("resize", handler, { passive: true });
    };

    PdfViewer.prototype._wireSidebarHandlers = function () {
        var self = this;
        // Click anywhere on a sidebar card → select that issue.
        this.host.addEventListener("click", function (ev) {
            var target = ev.target;
            if (!(target instanceof Element)) return;
            // Existing data-pdf-page jump-to-page buttons take precedence.
            var pageBtn = target.closest("[data-pdf-page]");
            if (pageBtn) {
                var n = parseInt(pageBtn.getAttribute("data-pdf-page") || "0", 10);
                if (Number.isFinite(n) && n > 0) {
                    ev.preventDefault();
                    self.renderPage(n);
                    return;
                }
            }
            // Card click (but not on an interactive control inside the card).
            var card = target.closest("[data-issue-id]");
            if (!card) return;
            if (card.classList.contains("pdf-issue-overlay")) return;
            if (card.classList.contains("pdf-issue-cluster")) return;
            // Don't hijack clicks on links / buttons inside the card.
            if (target.closest("a, button, input, textarea, select, [role=button]")) return;
            self.selectIssue(card.getAttribute("data-issue-id"), { scrollPage: true });
        });
    };

    PdfViewer.prototype._setStatus = function (msg) {
        if (this.statusEl) this.statusEl.textContent = msg || "";
    };

    // ---------- helpers -------------------------------------------------

    function cssEscape(s) {
        if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
            return CSS.escape(s);
        }
        return String(s).replace(/["\\]/g, "\\$&");
    }

    // ---------- DOM ready bootstrap -------------------------------------

    function bootAll() {
        var hosts = document.querySelectorAll("[data-pdf-viewer-host]");
        for (var i = 0; i < hosts.length; i++) {
            var host = hosts[i];
            if (!host.getAttribute("data-pdf-url")) continue; // detail.html may be in a no-viewer state
            if (host.__pdfViewerBooted) continue;
            host.__pdfViewerBooted = true;
            var v = new PdfViewer(host);
            host.__pdfViewer = v;
            v.init();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bootAll);
    } else {
        bootAll();
    }

    // Expose for templates / tests.
    window.PdfViewerApp = { boot: bootAll };
})();
