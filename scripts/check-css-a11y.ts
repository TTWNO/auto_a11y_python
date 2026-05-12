#!/usr/bin/env bun
/**
 * CSS accessibility linter for the report-output side of auto_a11y.
 *
 * Why this exists: reports are generated as self-contained static HTML by
 * Python heredocs in `auto_a11y/reporting/*.py`, plus a handful of Jinja2
 * templates and standalone CSS files under `auto_a11y/web/`. Off-the-shelf
 * tools (stylelint, axe, pa11y) can't see colours that live inside Python
 * strings, so we do the extraction here.
 *
 * What it checks:
 *  1. WCAG 1.4.3 contrast (< 4.5:1) for every CSS rule that declares both
 *     a foreground (`color`) and a background (`background` /
 *     `background-color`). Colour values may be:
 *       - hex literals (#abc, #abcdef, #abcdef00)
 *       - rgb() / rgba() — legacy comma syntax AND modern space + slash
 *         syntax, with int or percentage channels
 *       - hsl() / hsla() — hue in deg/rad/grad/turn, optional alpha
 *       - var(--name) / var(--name, fallback) — resolved against
 *         token definitions extracted from :root rules across the
 *         project's CSS (including tokens.css)
 *       - CSS named colours (white, red, transparent, ...)
 *     Values with alpha < 1 are composited (foreground over the rule's
 *     background; background over white) so semi-transparent colours
 *     still produce a real contrast number.
 *  2. Prohibited Bootstrap colour utility classes per CLAUDE.md
 *     ("Bootstrap colour classes are PROHIBITED"). Limited to the set
 *     with no overlapping custom-system equivalent — overlapping names
 *     like `bg-info` or `border-info` are skipped.
 *  3. `outline: none` / `outline: 0` declarations with no replacement
 *     focus style in the same rule (SC 2.4.7).
 *  4. Tiny font-size declarations (< 12px, < 0.75rem, < 0.75em) in
 *     report output. Soft rule but matches project posture.
 *
 * Usage:
 *   bun run scripts/check-css-a11y.ts            # whole repo
 *   bun run scripts/check-css-a11y.ts <paths>    # specific files
 *   bun run scripts/check-css-a11y.ts --json     # machine-readable
 *
 * Exit code is non-zero when any finding is reported.
 */

import { readFileSync } from 'node:fs';
import { Glob } from 'bun';
import { resolve, relative } from 'node:path';

type Severity = 'error' | 'warning';
interface Finding {
    file: string;
    line: number;
    severity: Severity;
    rule: string;
    message: string;
}

const ROOT = resolve(import.meta.dirname, '..');

// File globs scanned by default. tokens.css is the design-system source of
// truth (its colours ARE the answer key) so we skip it; archive/, fixtures,
// node_modules etc. are excluded by being outside these globs.
const DEFAULT_GLOBS = [
    'auto_a11y/reporting/**/*.py',
    'auto_a11y/web/templates/**/*.html',
    'auto_a11y/web/static/**/*.css',
    'auto_a11y/web/static/**/*.js',
];

// Files we never lint:
//   - tokens.css: the design-system source of truth
//   - issue_descriptions_*.py / issue_catalog.py: dictionary-of-issue-templates
//     where many template strings legitimately quote CSS as remediation
//     guidance ("Replace `outline: none` with..."). Not actually rendered as
//     CSS anywhere — false-positive territory.
//   - *.min.* and vendor/: third-party assets we don't own.
const SKIP_FILES = new Set([
    'auto_a11y/web/static/public/css/tokens.css',
    'auto_a11y/reporting/issue_descriptions_enhanced.py',
    'auto_a11y/reporting/issue_descriptions_translated.py',
    'auto_a11y/reporting/issue_catalog.py',
]);

const SKIP_PATTERNS: RegExp[] = [
    /\.min\.(css|js|mjs)$/,
    /(^|\/)vendor\//,
];

function isSkipped(file: string): boolean {
    if (SKIP_FILES.has(file)) return true;
    return SKIP_PATTERNS.some(p => p.test(file));
}

/**
 * Inline suppression: a finding is silenced if `@a11y-ignore` appears
 * anywhere from the finding's line back to the rule's opening `{` (or
 * up to 12 lines back if no `{` is found in that window — covers
 * inline `style="..."` attributes and Python heredocs). The convention
 * mirrors mypy / eslint and lets us mark places where the lint rule
 * has a known false positive (e.g. focus moved to a child element so
 * the host's `outline: none` is intentional, or a hover-tint that
 * composites correctly against a parent we can't statically see).
 */
function isSuppressed(originalContent: string, line: number): boolean {
    const lines = originalContent.split('\n');
    const idx = line - 1;
    if (idx < 0 || idx >= lines.length) return false;
    const SCAN_BACK_LIMIT = 12;
    const start = Math.max(0, idx - SCAN_BACK_LIMIT);
    for (let i = idx; i >= start; i--) {
        const text = lines[i] ?? '';
        if (text.includes('@a11y-ignore')) return true;
        // Stop scanning once we leave the current rule block.
        if (text.includes('{') && i < idx) return false;
    }
    return false;
}

// ---------- Colour parsing ----------

interface RGBA {
    r: number;  // 0-255
    g: number;  // 0-255
    b: number;  // 0-255
    a: number;  // 0-1
}

const WHITE: RGBA = { r: 255, g: 255, b: 255, a: 1 };

/**
 * Standard CSS named colours we resolve. Not the full 148 — only the
 * subset likely to appear in this codebase plus `transparent` (alpha 0)
 * and `currentColor` / `inherit` (sentinel: unresolvable).
 */
const NAMED_COLOURS: Record<string, string> = {
    black: '#000000',
    white: '#ffffff',
    red: '#ff0000',
    green: '#008000',
    blue: '#0000ff',
    yellow: '#ffff00',
    cyan: '#00ffff',
    magenta: '#ff00ff',
    silver: '#c0c0c0',
    gray: '#808080',
    grey: '#808080',
    darkgray: '#a9a9a9',
    darkgrey: '#a9a9a9',
    lightgray: '#d3d3d3',
    lightgrey: '#d3d3d3',
    maroon: '#800000',
    olive: '#808000',
    purple: '#800080',
    teal: '#008080',
    navy: '#000080',
    aqua: '#00ffff',
    fuchsia: '#ff00ff',
    lime: '#00ff00',
    orange: '#ffa500',
    pink: '#ffc0cb',
    brown: '#a52a2a',
    crimson: '#dc143c',
    gold: '#ffd700',
    indigo: '#4b0082',
    violet: '#ee82ee',
    turquoise: '#40e0d0',
    transparent: '__transparent__',
    currentcolor: '__unresolved__',
    inherit: '__unresolved__',
    initial: '__unresolved__',
    unset: '__unresolved__',
};

function clampByte(n: number): number {
    return Math.max(0, Math.min(255, Math.round(n)));
}

function clampAlpha(n: number): number {
    return Math.max(0, Math.min(1, n));
}

function parseHex(hex: string): RGBA | null {
    let h = hex.replace('#', '');
    if (h.length === 3) {
        h = h.split('').map(c => c + c).join('');
    }
    if (h.length === 4) {
        // #rgba shorthand
        h = h.split('').map(c => c + c).join('');
    }
    if (!/^[0-9a-fA-F]+$/.test(h)) return null;
    if (h.length === 6) {
        return {
            r: parseInt(h.slice(0, 2), 16),
            g: parseInt(h.slice(2, 4), 16),
            b: parseInt(h.slice(4, 6), 16),
            a: 1,
        };
    }
    if (h.length === 8) {
        return {
            r: parseInt(h.slice(0, 2), 16),
            g: parseInt(h.slice(2, 4), 16),
            b: parseInt(h.slice(4, 6), 16),
            a: parseInt(h.slice(6, 8), 16) / 255,
        };
    }
    return null;
}

/**
 * Parse a numeric channel from an `rgb()` / `rgba()` argument. Accepts:
 *   - integer 0-255 ("128", "0", "255")
 *   - percentage 0-100% ("50%")
 *   - the keyword "none" (treated as 0 per CSS Color 4)
 */
function parseRgbChannel(s: string): number | null {
    const t = s.trim();
    if (t === 'none') return 0;
    if (t.endsWith('%')) {
        const p = parseFloat(t.slice(0, -1));
        if (Number.isNaN(p)) return null;
        return clampByte((p / 100) * 255);
    }
    const n = parseFloat(t);
    if (Number.isNaN(n)) return null;
    return clampByte(n);
}

/** Parse the alpha argument: number 0-1, percentage 0-100%, or "none". */
function parseAlphaArg(s: string | undefined): number {
    if (s === undefined) return 1;
    const t = s.trim();
    if (t === 'none') return 0;
    if (t.endsWith('%')) {
        const p = parseFloat(t.slice(0, -1));
        return Number.isNaN(p) ? 1 : clampAlpha(p / 100);
    }
    const n = parseFloat(t);
    return Number.isNaN(n) ? 1 : clampAlpha(n);
}

/**
 * Split the contents of a colour-function's parens by commas at top level,
 * OR by whitespace (and a `/` for alpha) if no commas appear. Handles both
 * legacy `rgb(255, 0, 0, .5)` and modern `rgb(255 0 0 / .5)`.
 */
function splitFunctionArgs(inner: string): string[] {
    const trimmed = inner.trim();
    if (trimmed.includes(',')) {
        return trimmed.split(',').map(s => s.trim());
    }
    // Modern syntax: split on whitespace and `/` so alpha lands as its own arg
    return trimmed
        .replace(/\s*\/\s*/g, ' / ')
        .split(/\s+/)
        .filter(s => s.length > 0 && s !== '/');
}

function parseRgbFunc(inner: string): RGBA | null {
    const args = splitFunctionArgs(inner);
    if (args.length < 3 || args.length > 4) return null;
    const r = parseRgbChannel(args[0]!);
    const g = parseRgbChannel(args[1]!);
    const b = parseRgbChannel(args[2]!);
    if (r === null || g === null || b === null) return null;
    return { r, g, b, a: parseAlphaArg(args[3]) };
}

/** Convert HSL (h in degrees, s/l in 0-1) to RGB (0-255). */
function hslToRgb(h: number, s: number, l: number): { r: number; g: number; b: number } {
    h = ((h % 360) + 360) % 360;
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = l - c / 2;
    let r1 = 0, g1 = 0, b1 = 0;
    if (h < 60) { r1 = c; g1 = x; b1 = 0; }
    else if (h < 120) { r1 = x; g1 = c; b1 = 0; }
    else if (h < 180) { r1 = 0; g1 = c; b1 = x; }
    else if (h < 240) { r1 = 0; g1 = x; b1 = c; }
    else if (h < 300) { r1 = x; g1 = 0; b1 = c; }
    else { r1 = c; g1 = 0; b1 = x; }
    return {
        r: clampByte((r1 + m) * 255),
        g: clampByte((g1 + m) * 255),
        b: clampByte((b1 + m) * 255),
    };
}

/** Parse a hue argument with optional unit (deg/rad/grad/turn) into degrees. */
function parseHue(s: string): number | null {
    const t = s.trim();
    if (t === 'none') return 0;
    const m = /^(-?[0-9]*\.?[0-9]+)(deg|rad|grad|turn)?$/.exec(t);
    if (!m) return null;
    const n = parseFloat(m[1]!);
    if (Number.isNaN(n)) return null;
    switch (m[2]) {
        case 'rad': return (n * 180) / Math.PI;
        case 'grad': return n * 0.9;
        case 'turn': return n * 360;
        default: return n;
    }
}

function parsePercent(s: string): number | null {
    const t = s.trim();
    if (t === 'none') return 0;
    if (!t.endsWith('%')) {
        // CSS Color 4 also allows raw numbers for S/L, but only inside
        // a relative-colour syntax we don't otherwise support. Treat as
        // unparseable rather than guess.
        return null;
    }
    const n = parseFloat(t.slice(0, -1));
    return Number.isNaN(n) ? null : Math.max(0, Math.min(100, n)) / 100;
}

function parseHslFunc(inner: string): RGBA | null {
    const args = splitFunctionArgs(inner);
    if (args.length < 3 || args.length > 4) return null;
    const h = parseHue(args[0]!);
    const s = parsePercent(args[1]!);
    const l = parsePercent(args[2]!);
    if (h === null || s === null || l === null) return null;
    const { r, g, b } = hslToRgb(h, s, l);
    return { r, g, b, a: parseAlphaArg(args[3]) };
}

/**
 * Pull out a `var(--name [, fallback])` expression starting at the given
 * position in a value, returning the parsed parts. Uses balanced-parens
 * matching so a fallback like `var(--a, rgb(0,0,0))` parses correctly.
 */
function extractVar(value: string): { name: string; fallback?: string } | null {
    const lead = /^var\s*\(/i.exec(value);
    if (!lead) return null;
    let depth = 1;
    let i = lead[0].length;
    while (i < value.length && depth > 0) {
        const c = value[i];
        if (c === '(') depth++;
        else if (c === ')') depth--;
        if (depth === 0) break;
        i++;
    }
    if (depth !== 0) return null;
    const inner = value.slice(lead[0].length, i);
    // Find the top-level comma (var() takes at most one fallback arg).
    let cdepth = 0;
    let commaIdx = -1;
    for (let j = 0; j < inner.length; j++) {
        if (inner[j] === '(') cdepth++;
        else if (inner[j] === ')') cdepth--;
        else if (inner[j] === ',' && cdepth === 0) { commaIdx = j; break; }
    }
    const name = (commaIdx === -1 ? inner : inner.slice(0, commaIdx)).trim();
    const fallback = commaIdx === -1 ? undefined : inner.slice(commaIdx + 1).trim();
    return { name, fallback };
}

/**
 * Resolve a CSS value to an RGBA. Returns null when the value can't be
 * statically resolved (currentColor, gradients, system colours, an
 * unknown var() with no fallback). Recursion is bounded so cyclical var
 * graphs don't hang.
 */
function parseColor(rawValue: string, vars: Map<string, string>, depth = 0): RGBA | null {
    if (depth > 16) return null;
    const value = rawValue.trim();
    if (!value) return null;
    // Gradients have no single colour — refuse rather than guess.
    if (/^[a-z-]*gradient\s*\(/i.test(value)) return null;
    // Modern colour spaces we don't (yet) decode.
    if (/^(oklch|oklab|lab|lch|color)\s*\(/i.test(value)) return null;

    if (value.startsWith('#')) {
        const hex = /^#[0-9a-fA-F]{3,8}\b/.exec(value);
        return hex ? parseHex(hex[0]) : null;
    }
    if (/^rgba?\s*\(/i.test(value)) {
        const m = /^rgba?\s*\(([^)]*)\)/i.exec(value);
        return m ? parseRgbFunc(m[1]!) : null;
    }
    if (/^hsla?\s*\(/i.test(value)) {
        const m = /^hsla?\s*\(([^)]*)\)/i.exec(value);
        return m ? parseHslFunc(m[1]!) : null;
    }
    if (/^var\s*\(/i.test(value)) {
        const v = extractVar(value);
        if (!v) return null;
        const resolved = vars.get(v.name);
        if (resolved !== undefined) return parseColor(resolved, vars, depth + 1);
        if (v.fallback !== undefined) return parseColor(v.fallback, vars, depth + 1);
        return null;
    }
    // Named colour — match only the leading identifier so values like
    // `red !important` still resolve.
    const ident = /^[a-zA-Z]+/.exec(value);
    if (ident) {
        const name = ident[0].toLowerCase();
        const mapped = NAMED_COLOURS[name];
        if (mapped === '__transparent__') {
            return { r: 0, g: 0, b: 0, a: 0 };
        }
        if (mapped === '__unresolved__') return null;
        if (mapped) return parseColor(mapped, vars, depth + 1);
    }
    return null;
}

// ---------- WCAG contrast math ----------

function srgbToLinear(channel: number): number {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(c: RGBA): number {
    return 0.2126 * srgbToLinear(c.r) + 0.7152 * srgbToLinear(c.g) + 0.0722 * srgbToLinear(c.b);
}

/**
 * Composite `fg` over `bg` using the standard Porter-Duff "source over"
 * operator. Used when either the foreground or the background carries
 * alpha < 1 — without this, semi-transparent colours can't be reduced to
 * a single contrast number.
 */
function compositeOver(fg: RGBA, bg: RGBA): RGBA {
    const a = fg.a + bg.a * (1 - fg.a);
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
    return {
        r: clampByte((fg.r * fg.a + bg.r * bg.a * (1 - fg.a)) / a),
        g: clampByte((fg.g * fg.a + bg.g * bg.a * (1 - fg.a)) / a),
        b: clampByte((fg.b * fg.a + bg.b * bg.a * (1 - fg.a)) / a),
        a,
    };
}

/**
 * Contrast between two parsed colours. If alpha is involved, we
 * composite first: bg is laid over white (the page's assumed canvas)
 * and then fg is laid over the resulting bg. The same logic the browser
 * effectively applies when rendering typical content.
 */
function contrast(fgRaw: RGBA, bgRaw: RGBA): number {
    const bg = bgRaw.a < 1 ? compositeOver(bgRaw, WHITE) : bgRaw;
    const fg = fgRaw.a < 1 ? compositeOver(fgRaw, bg) : fgRaw;
    const lf = luminance(fg);
    const lb = luminance(bg);
    const [light, dark] = lf >= lb ? [lf, lb] : [lb, lf];
    return (light + 0.05) / (dark + 0.05);
}

/** Human-readable form for an RGBA used in finding messages. */
function formatColor(c: RGBA): string {
    if (c.a === 1) {
        const hex = (n: number): string => n.toString(16).padStart(2, '0');
        return `#${hex(c.r)}${hex(c.g)}${hex(c.b)}`;
    }
    return `rgba(${c.r}, ${c.g}, ${c.b}, ${c.a.toFixed(2)})`;
}

// ---------- CSS rule scanner ----------

/**
 * Walk a file as if it were a stream of CSS declarations grouped into rule
 * blocks delimited by `{` / `}`. Works equally for real CSS files and for
 * CSS embedded inside Python heredocs or HTML <style> blocks — we just
 * track brace depth and don't care about the surrounding language.
 *
 * Inline `style="..."` attributes are handled separately; they have no
 * braces.
 */
interface Rule {
    startLine: number;
    /** Raw selector text captured between the previous `}` (or start of file) and this rule's `{`. */
    selector: string;
    /**
     * 0 = top-level rule. 1+ = nested inside an at-rule (`@media`,
     * `@supports`, etc.). Variable extraction prefers nestingDepth=0 so
     * dark-mode overrides inside `@media (prefers-color-scheme: dark)`
     * don't clobber the default light-mode token values.
     */
    nestingDepth: number;
    declarations: Map<string, { value: string; line: number }>;
}

/**
 * Strip `/​* ... *​/` CSS comments and `<!-- ... -->` HTML comments while
 * preserving newlines, so reported line numbers still match the original
 * file. Without this step the scanner trips over `text-primary` mentions
 * inside doc-comments and false-flags them as Bootstrap colour usage.
 */
/**
 * Strip embedded HTML tags (`<style>`, `</style>`, etc.) from a string —
 * used when capturing CSS selectors that get accidentally prefixed with
 * the `<style>` tag inside HTML templates. CSS selectors never legally
 * contain `<`, so anywhere we find one we know it's an HTML tag.
 */
function stripHtmlTags(s: string): string {
    return s.replace(/<[^<>]*>/g, ' ');
}

function stripComments(content: string): string {
    // CSS / JS block comments
    let out = content.replace(/\/\*[\s\S]*?\*\//g, m =>
        m.replace(/[^\n]/g, ' '),
    );
    // HTML comments
    out = out.replace(/<!--[\s\S]*?-->/g, m =>
        m.replace(/[^\n]/g, ' '),
    );
    return out;
}

function* scanRuleBlocks(content: string): Generator<Rule> {
    const lines = stripComments(content).split('\n');
    // Stack of partially-built rules: each `{` pushes a frame, each `}`
    // pops and yields. This is what makes nested at-rules like
    // `@media (...) { .foo { color: red } }` not bleed declarations across
    // their inner sibling rules.
    const stack: Rule[] = [];
    let buffer = '';
    let bufferStartLine = 0;

    const flushDecl = (): void => {
        const trimmed = buffer.trim();
        buffer = '';
        const startLine = bufferStartLine;
        bufferStartLine = 0;
        if (!trimmed || stack.length === 0) return;
        const colon = trimmed.indexOf(':');
        if (colon <= 0) return;
        // Skip pseudo-class selectors masquerading as declarations: when a
        // ruleset like `a:hover { ... }` has its `{` on the *next* line,
        // the selector text accumulates into `buffer` until we hit `{`,
        // where it's discarded. But declarations only ever come AFTER a
        // `{`, so being inside `stack` is enough — the check above. The
        // colon-position guard below catches valid `--var: value` (where
        // colon is at position 2).
        const prop = trimmed.slice(0, colon).trim().toLowerCase();
        const value = trimmed.slice(colon + 1).trim();
        if (prop && value) {
            stack[stack.length - 1]!.declarations.set(prop, { value, line: startLine });
        }
    };

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i] ?? '';
        for (let j = 0; j < line.length; j++) {
            const ch = line[j]!;
            if (ch === '{') {
                // Selector text was accumulating in `buffer`; capture it on
                // the new rule frame before discarding for declarations.
                // Strip any embedded HTML tags so a `<style>:root { ... }`
                // in a Jinja2 template yields the selector `:root` rather
                // than `<style> :root`.
                const selector = stripHtmlTags(buffer).trim();
                buffer = '';
                bufferStartLine = 0;
                stack.push({
                    startLine: i + 1,
                    selector,
                    nestingDepth: stack.length,
                    declarations: new Map(),
                });
            } else if (ch === '}') {
                flushDecl();
                const rule = stack.pop();
                if (rule) yield rule;
            } else if (ch === ';' && stack.length > 0) {
                flushDecl();
            } else {
                // Capture buffer in BOTH selector context (depth 0+, between
                // `}` and next `{`) and declaration context (inside `{...}`).
                // The `{` handler trims and stashes for selectors; flushDecl
                // parses for declarations. Track first-non-whitespace line
                // so finding line numbers point at the property name itself.
                if (ch.trim() && !buffer.trim()) {
                    bufferStartLine = i + 1;
                }
                buffer += ch;
            }
        }
        // Preserve a space across line breaks so declarations / selectors
        // spanning lines don't concatenate (`color` + `red` → `colorred`).
        if (buffer) {
            buffer += ' ';
        }
    }
}

// ---------- Variable extraction ----------

/**
 * Build a global `--token-name → raw-value` map by scanning tokens.css and
 * every other project CSS file for `:root { --foo: ... }` declarations at
 * top-level nesting (i.e. NOT inside `@media (prefers-color-scheme: dark)`
 * etc.). Each value is stored raw — `parseColor` resolves chains and
 * fallbacks recursively at lookup time.
 */
async function buildVariableMap(): Promise<Map<string, string>> {
    const vars = new Map<string, string>();
    // The default skip list keeps tokens.css out of the lint pass, but
    // for variable extraction we explicitly want it.
    const cssFiles = new Set<string>(['auto_a11y/web/static/public/css/tokens.css']);
    const cssGlob = new Glob('auto_a11y/web/static/**/*.css');
    for await (const file of cssGlob.scan({ cwd: ROOT })) {
        if (SKIP_PATTERNS.some(p => p.test(file))) continue;
        cssFiles.add(file);
    }
    for (const file of cssFiles) {
        const abs = resolve(ROOT, file);
        let content: string;
        try {
            content = readFileSync(abs, 'utf-8');
        } catch {
            continue;
        }
        for (const rule of scanRuleBlocks(content)) {
            if (rule.nestingDepth !== 0) continue;
            if (rule.selector.trim() !== ':root') continue;
            for (const [prop, decl] of rule.declarations) {
                if (prop.startsWith('--')) {
                    vars.set(prop, decl.value);
                }
            }
        }
    }
    return vars;
}

// ---------- Individual checks ----------

const CONTRAST_TEXT_MIN = 4.5;

function checkRule(
    file: string,
    rule: Rule,
    vars: Map<string, string>,
    findings: Finding[],
): void {
    const color = rule.declarations.get('color');
    const bg = rule.declarations.get('background-color') ?? rule.declarations.get('background');

    if (color && bg) {
        const fg = parseColor(color.value, vars);
        const bgC = parseColor(bg.value, vars);
        if (fg && bgC && fg.a > 0 && bgC.a > 0) {
            const ratio = contrast(fg, bgC);
            if (ratio < CONTRAST_TEXT_MIN) {
                findings.push({
                    file,
                    line: color.line,
                    severity: 'error',
                    rule: 'contrast-text',
                    message: `color ${formatColor(fg)} on background ${formatColor(bgC)} = ${ratio.toFixed(2)}:1 (needs ${CONTRAST_TEXT_MIN}:1 for body text — SC 1.4.3)`,
                });
            }
        }
    }

    const outline = rule.declarations.get('outline');
    if (outline && /^\s*(none|0|0px|0pt)\b/.test(outline.value)) {
        // Valid replacements include box-shadow / border (CSS focus rings),
        // outline-offset (still leaves an outline), and SVG visual changes
        // (fill / stroke / fill-opacity / stroke-width) which are how SVG
        // overlays signal focus when a CSS outline would clip badly.
        const hasReplacement =
            rule.declarations.has('box-shadow') ||
            rule.declarations.has('border') ||
            rule.declarations.has('outline-offset') ||
            rule.declarations.has('fill') ||
            rule.declarations.has('fill-opacity') ||
            rule.declarations.has('stroke') ||
            rule.declarations.has('stroke-width') ||
            rule.declarations.has('stroke-dasharray');
        if (!hasReplacement) {
            findings.push({
                file,
                line: outline.line,
                severity: 'error',
                rule: 'outline-removed',
                message: `outline: ${outline.value} with no replacement focus style — SC 2.4.7`,
            });
        }
    }

    const fontSize = rule.declarations.get('font-size');
    if (fontSize) {
        const finding = checkFontSize(fontSize.value);
        if (finding) {
            findings.push({
                file,
                line: fontSize.line,
                severity: 'warning',
                rule: 'tiny-font',
                message: `font-size: ${fontSize.value} (${finding})`,
            });
        }
    }
}

function checkFontSize(value: string): string | null {
    const px = value.match(/^([0-9]*\.?[0-9]+)\s*px\b/);
    if (px) {
        const v = parseFloat(px[1]!);
        return v < 12 ? `${v}px below 12px floor` : null;
    }
    const rem = value.match(/^([0-9]*\.?[0-9]+)\s*rem\b/);
    if (rem) {
        const v = parseFloat(rem[1]!);
        return v < 0.75 ? `${v}rem below 0.75rem floor` : null;
    }
    const em = value.match(/^([0-9]*\.?[0-9]+)\s*em\b/);
    if (em) {
        const v = parseFloat(em[1]!);
        return v < 0.75 ? `${v}em below 0.75em floor` : null;
    }
    return null;
}

// ---------- Inline style="..." scan ----------

function checkInlineStyles(
    file: string,
    content: string,
    vars: Map<string, string>,
    findings: Finding[],
): void {
    const lines = content.split('\n');
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i]!;
        const re = /style\s*=\s*["']([^"']+)["']/g;
        let match: RegExpExecArray | null;
        while ((match = re.exec(line)) !== null) {
            const decls = parseInlineStyle(match[1]!);
            const color = decls.get('color');
            const bg = decls.get('background-color') ?? decls.get('background');
            if (color && bg) {
                const fg = parseColor(color, vars);
                const bgC = parseColor(bg, vars);
                if (fg && bgC && fg.a > 0 && bgC.a > 0) {
                    const ratio = contrast(fg, bgC);
                    if (ratio < CONTRAST_TEXT_MIN) {
                        findings.push({
                            file,
                            line: i + 1,
                            severity: 'error',
                            rule: 'contrast-text',
                            message: `inline style: color ${formatColor(fg)} on background ${formatColor(bgC)} = ${ratio.toFixed(2)}:1 (needs ${CONTRAST_TEXT_MIN}:1 — SC 1.4.3)`,
                        });
                    }
                }
            }
            const outline = decls.get('outline');
            if (outline && /^\s*(none|0|0px|0pt)\b/.test(outline)) {
                findings.push({
                    file,
                    line: i + 1,
                    severity: 'error',
                    rule: 'outline-removed',
                    message: `inline style removes outline (${outline}) — SC 2.4.7`,
                });
            }
            const fontSize = decls.get('font-size');
            if (fontSize) {
                const note = checkFontSize(fontSize);
                if (note) {
                    findings.push({
                        file,
                        line: i + 1,
                        severity: 'warning',
                        rule: 'tiny-font',
                        message: `inline style: font-size ${fontSize} (${note})`,
                    });
                }
            }
        }
    }
}

function parseInlineStyle(style: string): Map<string, string> {
    const out = new Map<string, string>();
    for (const decl of style.split(';')) {
        const colon = decl.indexOf(':');
        if (colon < 0) continue;
        const prop = decl.slice(0, colon).trim().toLowerCase();
        const val = decl.slice(colon + 1).trim();
        if (prop && val) out.set(prop, val);
    }
    return out;
}

// ---------- Bootstrap colour classes ----------

/**
 * Bootstrap classes whose names DO NOT collide with the project's custom
 * colour system (CLAUDE.md "Colour System"). Collisions like `bg-info`,
 * `border-info`, `alert-info`, `btn-info`, `btn-dark`, `text-muted` are
 * intentionally not in this list — those names exist in the custom system
 * too and we can't tell statically which one a class= attribute means.
 */
const PROHIBITED_CLASS_PATTERNS: RegExp[] = [
    /\btext-bg-(primary|secondary|success|danger|warning|info|light|dark)\b/g,
    /\bbtn-(primary|secondary|success|danger|warning|light)\b/g,
    /\bbtn-outline-(primary|secondary|success|danger|warning|light)\b/g,
    /\balert-(success|danger|warning|secondary)\b/g,
    /\bbg-(primary|secondary|success|danger|warning)\b/g,
    /\btext-(primary|secondary|success|danger|warning|light|white|black|body)\b/g,
    /\bborder-(primary|secondary|success|danger|warning)\b/g,
    /\btable-(secondary|success|danger|warning|light)\b/g,
    /\bbadge\s+bg-(primary|secondary|success|danger|warning|info|light|dark)\b/g,
];

function checkBootstrapClasses(file: string, content: string, findings: Finding[]): void {
    // The prohibition is on USING these classes in HTML / JS attribute
    // values. CSS files legitimately define rules for them (e.g. to
    // override Bootstrap's stock colour with a token-driven value), and
    // template/Python files reference them only when they're being
    // applied to elements. We skip pure CSS files entirely; for other
    // file types we strip comments before matching to avoid the
    // doc-comment in style.css that lists these classes as bad examples.
    if (/\.css$/.test(file)) return;
    const stripped = stripComments(content);
    const lines = stripped.split('\n');
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i]!;
        for (const pattern of PROHIBITED_CLASS_PATTERNS) {
            pattern.lastIndex = 0;
            let m: RegExpExecArray | null;
            while ((m = pattern.exec(line)) !== null) {
                findings.push({
                    file,
                    line: i + 1,
                    severity: 'error',
                    rule: 'bootstrap-colour-class',
                    message: `prohibited Bootstrap colour class "${m[0]}" — use the custom token-backed class from CLAUDE.md "Colour System"`,
                });
            }
        }
    }
}

// ---------- Entry point ----------

async function collectFiles(args: string[]): Promise<string[]> {
    if (args.length > 0) {
        return args.map(a => relative(ROOT, resolve(a)));
    }
    const out: string[] = [];
    for (const pattern of DEFAULT_GLOBS) {
        const glob = new Glob(pattern);
        for await (const file of glob.scan({ cwd: ROOT })) {
            if (!isSkipped(file)) {
                out.push(file);
            }
        }
    }
    return out.sort();
}

async function main(): Promise<number> {
    const argv = process.argv.slice(2);
    const json = argv.includes('--json');
    const files = await collectFiles(argv.filter(a => !a.startsWith('--')));

    // Build the design-token map once before we start checking. Every
    // `var(--foo)` encountered by parseColor resolves through this map.
    const globalVars = await buildVariableMap();

    const findings: Finding[] = [];
    for (const file of files) {
        if (isSkipped(file)) continue;
        const abs = resolve(ROOT, file);
        const content = readFileSync(abs, 'utf-8');
        const before = findings.length;
        // Scan rules once and reuse: collect them so we can both lift
        // file-local `:root` token definitions into the var map AND
        // run the rule checks without re-parsing.
        const ruleList = Array.from(scanRuleBlocks(content));
        const vars = new Map(globalVars);
        for (const rule of ruleList) {
            if (rule.nestingDepth === 0 && rule.selector.trim() === ':root') {
                for (const [prop, decl] of rule.declarations) {
                    if (prop.startsWith('--')) {
                        vars.set(prop, decl.value);
                    }
                }
            }
        }
        for (const rule of ruleList) {
            checkRule(file, rule, vars, findings);
        }
        checkInlineStyles(file, content, vars, findings);
        checkBootstrapClasses(file, content, findings);
        // Apply inline suppressions to anything added for this file.
        for (let i = findings.length - 1; i >= before; i--) {
            if (isSuppressed(content, findings[i]!.line)) {
                findings.splice(i, 1);
            }
        }
    }

    findings.sort((a, b) =>
        a.file === b.file ? a.line - b.line : a.file.localeCompare(b.file),
    );

    if (json) {
        process.stdout.write(JSON.stringify({ findings }, null, 2) + '\n');
    } else {
        for (const f of findings) {
            const tag = f.severity === 'error' ? 'error' : 'warn ';
            process.stdout.write(`${f.file}:${f.line}: ${tag} [${f.rule}] ${f.message}\n`);
        }
        const errors = findings.filter(f => f.severity === 'error').length;
        const warnings = findings.length - errors;
        process.stdout.write(
            `\n${errors} error${errors === 1 ? '' : 's'}, ${warnings} warning${warnings === 1 ? '' : 's'} ` +
            `across ${files.length} file${files.length === 1 ? '' : 's'}\n`,
        );
    }

    return findings.some(f => f.severity === 'error') ? 1 : 0;
}

process.exit(await main());
