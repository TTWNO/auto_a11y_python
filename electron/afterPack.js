/**
 * electron-builder afterPack hook
 *
 * Runs after the .app bundle is assembled but BEFORE it is packaged into a
 * DMG.  We ad-hoc codesign every Mach-O file inside Contents/Resources so
 * that macOS does not silently block them at runtime: `install_name_tool`
 * (run by build-mac.sh on the bundled WeasyPrint dylibs) invalidates each
 * dylib's existing signature, and on Apple Silicon a missing/invalid
 * signature is a hard `dlopen` failure.
 *
 * Targets: bundled Chromium (headless shell + full browser), mongod, the
 * portable Python interpreter, every C-extension `.so` in the standard
 * library / site-packages, and the WeasyPrint `.dylib` set.
 *
 * IMPORTANT: do NOT filter by execute permission. Many `.dylib` and `.so`
 * files arrive at mode 0644 (Homebrew dylibs preserved by `cp`, Python
 * extensions installed by pip), so a `find -perm +111` selector silently
 * skips them and they ship unsigned.
 *
 * After signing nested Mach-Os, we re-sign the outer .app top-down with
 * `--deep` so that the parent signature reflects the new nested signatures.
 * Without this final pass the bundle's signature is stale and the hardened
 * runtime rejects the whole app on first launch.
 */

const { execSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

function isMachO(filePath) {
  // Read first 4 bytes; cheaper and more reliable than spawning `file` for
  // every entry under Resources/ (tens of thousands of files).
  let fd;
  try {
    fd = fs.openSync(filePath, 'r');
    const buf = Buffer.alloc(4);
    const bytes = fs.readSync(fd, buf, 0, 4, 0);
    if (bytes < 4) return false;
    const magic = buf.readUInt32BE(0);
    // 32-bit + 64-bit Mach-O, big- and little-endian, plus FAT (universal).
    return (
      magic === 0xfeedface || // MH_MAGIC
      magic === 0xfeedfacf || // MH_MAGIC_64
      magic === 0xcefaedfe || // MH_CIGAM
      magic === 0xcffaedfe || // MH_CIGAM_64
      magic === 0xcafebabe || // FAT_MAGIC
      magic === 0xbebafeca    // FAT_CIGAM
    );
  } catch {
    return false;
  } finally {
    if (fd !== undefined) {
      try { fs.closeSync(fd); } catch { /* ignore */ }
    }
  }
}

exports.default = async function afterPack(context) {
  if (process.platform !== 'darwin') return;

  // When the build is using a real Developer ID + notarization,
  // electron-builder's own signing pass runs after afterPack and would
  // be clobbered by ad-hoc signatures here. build-mac.sh sets this env
  // var in that case to make afterPack a no-op.
  if (process.env.AUTO_A11Y_SKIP_ADHOC_SIGN === '1') {
    console.log('[afterPack] AUTO_A11Y_SKIP_ADHOC_SIGN=1; deferring to electron-builder');
    return;
  }

  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`,
  );
  const resourcesDir = path.join(appPath, 'Contents', 'Resources');

  if (!fs.existsSync(resourcesDir)) {
    console.warn('[afterPack] Resources dir not found, skipping codesign');
    return;
  }

  console.log('[afterPack] Scanning Mach-O files in', resourcesDir);

  // Enumerate every regular file under Resources/. No perm filter — we'll
  // identify Mach-Os by their magic bytes below.
  const output = execSync(
    `find "${resourcesDir}" -type f`,
    { encoding: 'utf8', maxBuffer: 200 * 1024 * 1024 },
  );

  const candidates = output.trim().split('\n').filter(Boolean);
  console.log(`[afterPack] ${candidates.length} candidate files`);

  let signed = 0;
  let failed = 0;
  const failures = [];

  for (const filePath of candidates) {
    if (!isMachO(filePath)) continue;

    // Ad-hoc signature, no TSA, no hardened runtime. We deliberately do
    // NOT pass `--options=runtime`: that would require entitlements
    // (e.g. `com.apple.security.cs.disable-library-validation`) we don't
    // ship, and would block dlopen of nested dylibs at runtime.
    const result = spawnSync(
      'codesign',
      ['--force', '--sign', '-', '--timestamp=none', filePath],
      { stdio: ['ignore', 'ignore', 'pipe'], encoding: 'utf8' },
    );

    if (result.status === 0) {
      signed++;
    } else {
      failed++;
      failures.push({ filePath, stderr: (result.stderr || '').trim() });
    }
  }

  console.log(`[afterPack] Signed ${signed} Mach-O files (${failed} failures)`);

  if (failures.length > 0) {
    // Surface up to 20 failures so CI logs make the cause obvious.
    console.error('[afterPack] Codesign failures (first 20):');
    for (const f of failures.slice(0, 20)) {
      console.error(`  ${path.relative(resourcesDir, f.filePath)}: ${f.stderr}`);
    }
    throw new Error(
      `[afterPack] ${failures.length} Mach-O file(s) failed to codesign — `
      + `the resulting DMG would be silently broken on macOS. Aborting build.`,
    );
  }

  // Sign every nested .app / .framework as a bundle, deepest-first. The
  // per-file pass above produced bare ad-hoc Mach-O signatures, but for
  // bundle main executables that is not enough: macOS validates them
  // against the parent bundle's _CodeSignature/CodeResources seal, which
  // only exists if the bundle itself has been signed. Signing the bundle
  // path here rewrites the main executable signature AND writes the seal.
  // Without this step Playwright's bundled "Google Chrome for Testing.app"
  // and its "Google Chrome for Testing Framework.framework" land in the
  // DMG with stale/missing bundle signatures and the audit step fails.
  const bundlesOutput = execSync(
    `find "${resourcesDir}" \\( -name '*.app' -o -name '*.framework' \\) -type d`,
    { encoding: 'utf8', maxBuffer: 50 * 1024 * 1024 },
  );
  const nestedBundles = bundlesOutput.trim().split('\n').filter(Boolean);
  // Longest path first ensures inner frameworks are sealed before the
  // app that contains them, so the outer seal captures the inner seals.
  nestedBundles.sort((a, b) => b.length - a.length);
  console.log(`[afterPack] Signing ${nestedBundles.length} nested bundles (deepest-first)`);
  for (const bundlePath of nestedBundles) {
    const bundleResult = spawnSync(
      'codesign',
      ['--force', '--sign', '-', '--timestamp=none', bundlePath],
      { stdio: ['ignore', 'ignore', 'pipe'], encoding: 'utf8' },
    );
    if (bundleResult.status !== 0) {
      throw new Error(
        `[afterPack] Failed to sign nested bundle ${bundlePath}: `
        + `${(bundleResult.stderr || '').trim()}`,
      );
    }
  }

  // Re-sign the outer .app top-down so its signature reflects the new
  // nested signatures. Without `--deep` here the parent is stale and the
  // hardened runtime rejects the whole bundle on first launch.
  console.log('[afterPack] Re-signing outer .app with --deep');
  execSync(
    `codesign --force --deep --sign - --timestamp=none "${appPath}"`,
    { stdio: 'inherit' },
  );

  // Verify the bundle is internally consistent before electron-builder
  // packs it into the DMG. `--strict` catches most "0 valid identities"-
  // class issues at build time instead of at the user's first launch.
  console.log('[afterPack] Verifying bundle signature');
  execSync(
    `codesign --verify --verbose=2 --strict "${appPath}"`,
    { stdio: 'inherit' },
  );
};
