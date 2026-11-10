import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { verifyBuild } from './check-build.mjs';

const here = dirname(fileURLToPath(import.meta.url));

function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), 'histor-scanner-build-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  for (const relative of ['package.json', 'package-lock.json', 'vendor', 'node_modules/@aimarket/warden']) {
    cpSync(join(here, relative), join(directory, relative), { recursive: true });
  }
  return directory;
}

test('the installed local scanner is the pinned accepted 0.25.0 build', () => {
  const metadata = verifyBuild();
  assert.equal(metadata.version, '0.25.0');
  assert.equal(metadata.buildDigest, '6c6d85a732825d19e56646b24a679268ae6bf86516fc24cc71ccf31872404a4d');
});

test('rejects an older installed version even when the source lock is current', (t) => {
  const directory = fixture(t);
  const path = join(directory, 'node_modules/@aimarket/warden/package.json');
  const manifest = JSON.parse(readFileSync(path, 'utf8'));
  manifest.version = '0.22.3';
  writeFileSync(path, JSON.stringify(manifest));
  assert.throws(() => verifyBuild(directory), /Installed scanner version differs/);
});

test('rejects changed runtime bytes even when the installed version is current', (t) => {
  const directory = fixture(t);
  const path = join(directory, 'node_modules/@aimarket/warden/dist/index.js');
  writeFileSync(path, readFileSync(path, 'utf8') + '\n// changed build\n');
  assert.throws(() => verifyBuild(directory), /Installed scanner build digest differs/);
});

test('rejects changed module routing even when version and dist bytes are current', (t) => {
  const directory = fixture(t);
  const path = join(directory, 'node_modules/@aimarket/warden/package.json');
  const manifest = JSON.parse(readFileSync(path, 'utf8'));
  manifest.exports['.'] = './alternate.mjs';
  writeFileSync(path, JSON.stringify(manifest));
  writeFileSync(join(directory, 'node_modules/@aimarket/warden/alternate.mjs'), 'export {};\n');
  assert.throws(() => verifyBuild(directory), /Installed scanner package metadata differs/);
});

test('rejects a modified vendored archive before accepting installed code', (t) => {
  const directory = fixture(t);
  const metadata = JSON.parse(readFileSync(join(directory, 'vendor/warden-build.json'), 'utf8'));
  const path = join(directory, 'vendor', metadata.tarball);
  writeFileSync(path, Buffer.concat([readFileSync(path), Buffer.from('modified')]));
  assert.throws(() => verifyBuild(directory), /Vendored archive SHA256 differs/);
});
