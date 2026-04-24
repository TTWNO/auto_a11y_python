/**
 * electron-builder afterPack hook
 *
 * Runs after the .app bundle is assembled but BEFORE it is packaged into a
 * DMG.  We ad-hoc codesign every Mach-O binary inside Contents/Resources so
 * that macOS does not silently block them at runtime (Gatekeeper rejects
 * binaries whose original signature was invalidated by being copied into a
 * different bundle).
 *
 * Targets: bundled Chromium (headless shell + full browser), mongod, Python.
 */

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

exports.default = async function afterPack(context) {
  if (process.platform !== 'darwin') return;

  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`,
  );
  const resourcesDir = path.join(appPath, 'Contents', 'Resources');

  if (!fs.existsSync(resourcesDir)) {
    console.warn('[afterPack] Resources dir not found, skipping codesign');
    return;
  }

  console.log('[afterPack] Signing nested Mach-O binaries in', resourcesDir);

  // Find every executable file under Resources and sign it if it is Mach-O
  const output = execSync(
    `find "${resourcesDir}" -type f -perm +111`,
    { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 },
  );

  let signed = 0;
  for (const filePath of output.trim().split('\n').filter(Boolean)) {
    try {
      const fileInfo = execSync(`file "${filePath}"`, { encoding: 'utf8' });
      if (!fileInfo.includes('Mach-O')) continue;

      execSync(`codesign --force --sign - "${filePath}"`, { stdio: 'ignore' });
      signed++;
      console.log(`  Signed: ${path.relative(resourcesDir, filePath)}`);
    } catch {
      // Non-fatal: some helper binaries may already be correctly signed
    }
  }

  console.log(`[afterPack] Signed ${signed} binaries`);
};
