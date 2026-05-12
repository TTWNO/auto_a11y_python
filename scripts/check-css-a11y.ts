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
 *  1. WCAG 1.4.3 contrast (text < 4.5:1, non-text UI < 3:1) for every CSS
 *     rule that declares both a foreground (`color`) and a background
 *     (`background` / `background-color`) as hex literals.
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
 * Inline suppression: a finding is silenced if its source line — or the
 * line immediately above it — contains the marker `@a11y-ignore`. The
 * convention mirrors mypy / eslint and lets us mark places where the
 * lint rule has a known false positive (e.g. focus moved to a child
 * element so the host's `outline: none` is intentional).
 */
function isSuppressed(originalContent: string, line: number): boolean {
    const lines = originalContent.split('\n');
    const idx = line - 1;
    if (idx < 0 || idx >= lines.length) return false;
    if ((lines[idx] ?? '').includes('@a11y-ignore')) return true;
    if (idx > 0 && (lines[idx - 1] ?? '').includes('@a11y-ignore')) return true;
    return false;
}

// ---------- WCAG contrast math ----------

function hexToRgb(hex: string): [number, number, number] | null {
    let h = hex.replace('#', '');
    if (h.length === 3) {
        h = h.split('').map(c => c + c).join('');
    }
    if (h.length === 8) {
        h = h.slice(0, 6); // drop alpha
    }
    if (h.length !== 6 || !/^[0-9a-fA-F]+$/.test(h)) {
        return null;
    }
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function srgbToLinear(channel: number): number {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance([r, g, b]: [number, number, number]): number {
    return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}

function contrastRatio(fg: string, bg: string): number | null {
    const fgRgb = hexToRgb(fg);
    const bgRgb = hexToRgb(bg);
    if (!fgRgb || !bgRgb) {
        return null;
    }
    const l1 = luminance(fgRgb);
    const l2 = luminance(bgRgb);
    const [light, dark] = l1 >= l2 ? [l1, l2] : [l2, l1];
    return (light + 0.05) / (dark + 0.05);
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
    declarations: Map<string, { value: string; line: number }>;
}

/**
 * Strip `/​* ... *​/` CSS comments and `<!-- ... -->` HTML comments while
 * preserving newlines, so reported line numbers still match the original
 * file. Without this step the scanner trips over `text-primary` mentions
 * inside doc-comments and false-flags them as Bootstrap colour usage.
 */
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
                // Selectors don't become declarations — discard buffer
                buffer = '';
                bufferStartLine = 0;
                stack.push({ startLine: i + 1, declarations: new Map() });
            } else if (ch === '}') {
                flushDecl();
                const rule = stack.pop();
                if (rule) yield rule;
            } else if (ch === ';' && stack.length > 0) {
                flushDecl();
            } else if (stack.length > 0) {
                // Capture the line of the first non-whitespace character of
                // the declaration so reported line numbers point at the
                // property name, not whatever indentation came before it.
                if (ch.trim() && !buffer.trim()) {
                    bufferStartLine = i + 1;
                }
                buffer += ch;
            }
        }
        // Preserve a space across line breaks so declarations spanning lines
        // don't concatenate (`color` + `red` → `colorred`).
        if (stack.length > 0 && buffer) {
            buffer += ' ';
        }
    }
}

/** Pick the first hex literal out of a CSS value, ignoring URLs etc. */
function firstHex(value: string): string | null {
    const m = value.match(/#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/);
    return m ? '#' + m[1]! : null;
}

// ---------- Individual checks ----------

const CONTRAST_TEXT_MIN = 4.5;
const CONTRAST_UI_MIN = 3.0;

function checkRule(file: string, rule: Rule, findings: Finding[]): void {
    const color = rule.declarations.get('color');
    const bg = rule.declarations.get('background-color') ?? rule.declarations.get('background');

    if (color && bg) {
        const fg = firstHex(color.value);
        const bgHex = firstHex(bg.value);
        if (fg && bgHex) {
            const ratio = contrastRatio(fg, bgHex);
            if (ratio !== null && ratio < CONTRAST_TEXT_MIN) {
                findings.push({
                    file,
                    line: color.line,
                    severity: 'error',
                    rule: 'contrast-text',
                    message: `color ${fg} on background ${bgHex} = ${ratio.toFixed(2)}:1 (needs ${CONTRAST_TEXT_MIN}:1 for body text — SC 1.4.3)`,
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

function checkInlineStyles(file: string, content: string, findings: Finding[]): void {
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
                const fg = firstHex(color);
                const bgHex = firstHex(bg);
                if (fg && bgHex) {
                    const ratio = contrastRatio(fg, bgHex);
                    if (ratio !== null && ratio < CONTRAST_TEXT_MIN) {
                        findings.push({
                            file,
                            line: i + 1,
                            severity: 'error',
                            rule: 'contrast-text',
                            message: `inline style: color ${fg} on background ${bgHex} = ${ratio.toFixed(2)}:1 (needs ${CONTRAST_TEXT_MIN}:1 — SC 1.4.3)`,
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

    const findings: Finding[] = [];
    for (const file of files) {
        if (isSkipped(file)) continue;
        const abs = resolve(ROOT, file);
        const content = readFileSync(abs, 'utf-8');
        const before = findings.length;
        for (const rule of scanRuleBlocks(content)) {
            checkRule(file, rule, findings);
        }
        checkInlineStyles(file, content, findings);
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
