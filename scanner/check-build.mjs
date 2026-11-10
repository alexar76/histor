// Verify the pinned local archive and the installed scanner without a network dependency.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildDigest } from './quality/check-quality-gate.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const json = (path) => JSON.parse(readFileSync(path, 'utf8'));

export function verifyBuild(scannerDir = here) {
  const metadata = json(join(scannerDir, 'vendor', 'warden-build.json'));
  assert.equal(metadata.package, '@aimarket/warden', 'Unexpected scanner package');
  assert.match(metadata.tarball, /^aimarket-warden-\d+\.\d+\.\d+\.tgz$/, 'Invalid vendored archive name');
  const reference = `file:vendor/${metadata.tarball}`;
  const manifest = json(join(scannerDir, 'package.json'));
  const lock = json(join(scannerDir, 'package-lock.json'));
  const locked = lock.packages['node_modules/@aimarket/warden'];
  assert.equal(manifest.dependencies[metadata.package], reference, 'Manifest does not select the pinned archive');
  assert.equal(lock.packages[''].dependencies[metadata.package], reference, 'Lock root does not select the pinned archive');
  assert.equal(locked.resolved, reference, 'Lock resolves a different scanner archive');
  assert.equal(locked.version, metadata.version, 'Locked scanner version differs');
  const archive = readFileSync(join(scannerDir, 'vendor', metadata.tarball));
  const integrity = `sha512-${createHash('sha512').update(archive).digest('base64')}`;
  assert.equal(createHash('sha256').update(archive).digest('hex'), metadata.sha256, 'Vendored archive SHA256 differs');
  assert.equal(integrity, metadata.integrity, 'Vendored archive integrity differs');
  assert.equal(locked.integrity, integrity, 'Lock integrity differs from the vendored archive');
  const installed = join(scannerDir, 'node_modules', '@aimarket', 'warden');
  const packageBytes = readFileSync(join(installed, 'package.json'));
  const installedManifest = JSON.parse(packageBytes.toString('utf8'));
  assert.equal(installedManifest.name, metadata.package, 'Installed scanner package differs');
  assert.equal(installedManifest.version, metadata.version, 'Installed scanner version differs');
  assert.equal(createHash('sha256').update(packageBytes).digest('hex'), metadata.packageJsonSha256,
    'Installed scanner package metadata differs');
  assert.equal(buildDigest(installed), metadata.buildDigest, 'Installed scanner build digest differs');
  return metadata;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const metadata = verifyBuild();
    console.log(`Verified ${metadata.package}@${metadata.version}: ${metadata.buildDigest}`);
  } catch (error) {
    console.error(`WARDEN build verification failed: ${error.message}`);
    process.exitCode = 1;
  }
}
