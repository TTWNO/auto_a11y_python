/*
 * Uint8Array ↔ hex / base64 polyfill.
 *
 * PDF.js 5.4 calls `Uint8Array.prototype.toHex()` (document fingerprints),
 * `.toBase64()` (embedded font data URLs) and `Uint8Array.fromBase64()`
 * (signature decompression). Those are the TC39 "Uint8Array to/from
 * base64" methods, which only reached Chrome 140 / Safari 18.4. Anything
 * older throws `TypeError: a.toHex is not a function` from inside PDF.js
 * and the document never loads.
 *
 * That bites us in two places:
 *
 *   - the bundled desktop app, whose Electron 33 ships Chromium ~130;
 *   - any browser older than Chrome 140 hitting the web app, which for an
 *     accessibility tool is not a hypothetical audience.
 *
 * Installed only where missing, so a current engine keeps its native
 * implementation. Loaded as a module by both the page and the PDF.js
 * worker — a worker has its own global scope, so polyfilling the page
 * alone leaves `toHex` (which runs worker-side) still broken.
 *
 * Deliberately not patched into the vendored pdf.min.mjs / worker files:
 * those stay pristine so a PDF.js upgrade is a straight file swap.
 */

const HEX = "0123456789abcdef";
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

function alphabetFor(options) {
    return (options && options.alphabet === "base64url") ? B64URL : B64;
}

function toBase64(bytes, options) {
    const chars = alphabetFor(options);
    const omitPadding = Boolean(options && options.omitPadding);
    let out = "";
    let i = 0;
    for (; i + 2 < bytes.length; i += 3) {
        const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
        out += chars[(n >> 18) & 63] + chars[(n >> 12) & 63]
            + chars[(n >> 6) & 63] + chars[n & 63];
    }
    const remaining = bytes.length - i;
    if (remaining === 1) {
        const n = bytes[i] << 16;
        out += chars[(n >> 18) & 63] + chars[(n >> 12) & 63];
        if (!omitPadding) out += "==";
    } else if (remaining === 2) {
        const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
        out += chars[(n >> 18) & 63] + chars[(n >> 12) & 63] + chars[(n >> 6) & 63];
        if (!omitPadding) out += "=";
    }
    return out;
}

function fromBase64(text, options) {
    const chars = alphabetFor(options);
    const lookup = new Map();
    for (let i = 0; i < chars.length; i += 1) {
        lookup.set(chars[i], i);
    }
    // Accept either alphabet's separators regardless of the requested one:
    // decoding is where being lenient costs nothing and refusing costs a
    // usable document.
    lookup.set("-", 62);
    lookup.set("_", 63);
    lookup.set("+", 62);
    lookup.set("/", 63);

    const cleaned = String(text).replace(/[=\s]/g, "");
    const out = new Uint8Array((cleaned.length * 3) >> 2);
    let bits = 0;
    let acc = 0;
    let written = 0;
    for (const ch of cleaned) {
        const value = lookup.get(ch);
        if (value === undefined) {
            throw new SyntaxError("Invalid base64 character: " + ch);
        }
        acc = (acc << 6) | value;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out[written] = (acc >> bits) & 0xff;
            written += 1;
        }
    }
    return written === out.length ? out : out.subarray(0, written);
}

function toHex(bytes) {
    let out = "";
    for (let i = 0; i < bytes.length; i += 1) {
        out += HEX[bytes[i] >> 4] + HEX[bytes[i] & 15];
    }
    return out;
}

function fromHex(text) {
    const source = String(text);
    if (source.length % 2 !== 0) {
        throw new SyntaxError("Hex string must have an even length");
    }
    const out = new Uint8Array(source.length / 2);
    for (let i = 0; i < out.length; i += 1) {
        const byte = Number.parseInt(source.substr(i * 2, 2), 16);
        if (Number.isNaN(byte)) {
            throw new SyntaxError("Invalid hex string");
        }
        out[i] = byte;
    }
    return out;
}

function define(target, name, value) {
    if (typeof target[name] === "function") {
        return;  // native implementation present — leave it alone
    }
    Object.defineProperty(target, name, {
        value: value,
        writable: true,
        enumerable: false,
        configurable: true,
    });
}

define(Uint8Array.prototype, "toHex", function () {
    return toHex(this);
});
define(Uint8Array.prototype, "toBase64", function (options) {
    return toBase64(this, options);
});
define(Uint8Array, "fromHex", function (text) {
    return fromHex(text);
});
define(Uint8Array, "fromBase64", function (text, options) {
    return fromBase64(text, options);
});
