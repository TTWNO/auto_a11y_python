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
     * Render a SemanticBlock tree into a target container as visually-hidden
     * but screen-reader-accessible DOM.
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
            return Promise.all([self._loadDocument(), self._loadIssueMap()]);
        }).then(function () {
            return self.renderPage(1);
        }).then(function () {
            self._setStatus("");
        }).catch(function (err) {
            console.error("[pdf_viewer_app] failed to initialise", err);
            self._setStatus(t("error-load", "Failed to load PDF"));
        });
    };

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
                var issue = data.issues[i];
                if (!issue.page || !issue.bbox) continue;
                var arr = self.issuesByPage.get(issue.page);
                if (!arr) { arr = []; self.issuesByPage.set(issue.page, arr); }
                arr.push({
                    id: issue.id,
                    check_name: issue.check_name,
                    check_result: issue.check_result,
                    detail: issue.detail,
                    page: issue.page,
                    bbox: { x0: issue.bbox[0], y0: issue.bbox[1], x1: issue.bbox[2], y1: issue.bbox[3] },
                });
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
     * Render the right-pane issue list from issue_map.json. Each card
     * carries data-issue-id matching the overlay rect on the canvas, so
     * connector lines can resolve both ends. We don't try to merge with
     * the existing auto_a11y violation list below — the two come from
     * different audit engines and can disagree; users want to see both
     * and triangulate.
     */
    PdfViewer.prototype._renderIssueList = function () {
        if (!this.issueListEl) return;
        this.issueListEl.innerHTML = "";

        var totalIssues = 0;
        this.issuesByPage.forEach(function (arr) { totalIssues += arr.length; });

        if (totalIssues === 0) {
            if (this.issueListEmptyEl) this.issueListEmptyEl.hidden = false;
            return;
        }
        if (this.issueListEmptyEl) this.issueListEmptyEl.hidden = true;

        // Flatten + group by page for predictable visual order.
        var pages = Array.from(this.issuesByPage.keys()).sort(function (a, b) { return a - b; });
        for (var p = 0; p < pages.length; p++) {
            var pageNum = pages[p];
            var arr = this.issuesByPage.get(pageNum);
            for (var i = 0; i < arr.length; i++) {
                this.issueListEl.appendChild(this._buildIssueCard(arr[i]));
            }
        }
    };

    PdfViewer.prototype._buildIssueCard = function (issue) {
        var li = document.createElement("li");
        li.className = "pdf-viewer-issue-card "
            + (issue.check_result === "FAIL" ? "is-fail" : "is-warn");
        li.setAttribute("data-issue-id", issue.id);
        li.setAttribute("tabindex", "0");

        var head = document.createElement("div");
        head.className = "pdf-viewer-issue-card-head";

        var resultBadge = document.createElement("span");
        var resultClass = (issue.check_result === "FAIL")
            ? "badge badge-high" : "badge badge-medium";
        resultBadge.className = resultClass;
        resultBadge.textContent = (issue.check_result === "FAIL")
            ? t("issue-result-fail", "Fail")
            : t("issue-result-warn", "Warn");
        head.appendChild(resultBadge);

        if (issue.page) {
            var pageBtn = document.createElement("button");
            pageBtn.type = "button";
            pageBtn.className = "btn btn-outline-brand btn-sm pdf-viewer-issue-page-btn";
            pageBtn.setAttribute("data-pdf-page", String(issue.page));
            var label = t("jump-to-page", "Page {page}");
            pageBtn.textContent = label.replace("{page}", String(issue.page));
            head.appendChild(pageBtn);
        }

        li.appendChild(head);

        var title = document.createElement("h4");
        title.className = "pdf-viewer-issue-card-title";
        title.textContent = issue.check_name;
        li.appendChild(title);

        if (issue.detail) {
            var p = document.createElement("p");
            p.className = "pdf-viewer-issue-card-detail";
            p.textContent = issue.detail;
            li.appendChild(p);
        }

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
                if (typeof card.focus === "function") card.focus();
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
            card.classList.toggle("is-selected",
                card.getAttribute("data-issue-id") === this.selectedIssueId);
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

        var ns = "http://www.w3.org/2000/svg";
        for (var i = 0; i < issuesOnPage.length; i++) {
            var issue = issuesOnPage[i];
            var overlay = this._findOverlay(issue.id);
            var card = this._findCard(issue.id);
            if (!overlay || !card) continue;

            var orect = overlay.getBoundingClientRect();
            var crect = card.getBoundingClientRect();

            // Vertical visibility gate (skip if overlay is entirely off-screen
            // and the line isn't the selected one — keeps the SVG quiet).
            var isSelected = (this.selectedIssueId === issue.id);
            if (!isSelected) {
                if (orect.bottom < hostRect.top || orect.top > hostRect.bottom) continue;
                if (crect.bottom < hostRect.top || crect.top > hostRect.bottom) continue;
            }

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
            this._appendLine(ns, x1, y1, x2, y2, isSelected ? 7 : 5, className + " is-outer", opacity);
            this._appendLine(ns, x1, y1, x2, y2, isSelected ? 5 : 3, className + " is-mid",   opacity);
            this._appendLine(ns, x1, y1, x2, y2, isSelected ? 3 : 1, className + " is-inner", opacity);
        }
    };

    PdfViewer.prototype._appendLine = function (ns, x1, y1, x2, y2, w, cls, opacity) {
        var line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(x1));
        line.setAttribute("y1", String(y1));
        line.setAttribute("x2", String(x2));
        line.setAttribute("y2", String(y2));
        line.setAttribute("class", cls);
        line.setAttribute("stroke-width", String(w));
        line.setAttribute("stroke-linecap", "round");
        line.setAttribute("opacity", String(opacity));
        this.connectorEl.appendChild(line);
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
