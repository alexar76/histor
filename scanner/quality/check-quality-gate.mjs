// Release evidence is signed by the isolated production evaluator, never by a PR runner.
import { createHash, verify } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, resolve } from 'node:path';

export function buildDigest(build) {
  const hash = createHash('sha256');
  const files = readdirSync(join(build, 'dist')).filter(n => n.endsWith('.js')).sort();
  if (!files.length) throw Error('WARDEN build missing');
  if (files.some(name => name.startsWith('._'))) throw Error('Build contains AppleDouble transfer metadata');
  for (const name of files) hash.update(name + '\0').update(readFileSync(join(build, 'dist', name))).update('\0');
  return hash.digest('hex');
}

export function checkEvidence(envelope, publicKey, expectedBuild, now = Date.now() / 1000) {
  const payload = Buffer.from(envelope.payload, 'base64');
  if (!verify(null, payload, publicKey, Buffer.from(envelope.signature, 'base64'))) throw Error('Invalid quality signature');
  const r = JSON.parse(payload);
  if (r.type !== 'momus.release-quality/v1' || r.policy !== 'zero-regressions-v1' || r.status !== 'passed')
    throw Error('Quality cycle has not passed');
  if (r.buildDigest !== expectedBuild) throw Error('This exact WARDEN build has not passed');
  if (!Number.isFinite(r.evaluatedAt) || !Number.isFinite(r.expiresAt) || r.evaluatedAt > now + 60
      || now - r.evaluatedAt > 48 * 3600 || r.expiresAt < now) throw Error('Quality evidence expired');
  const full = r.fullEvidence;
  if (!full || full.status !== 'passed' || full.buildDigest !== expectedBuild
      || full.holdoutSha256 !== r.holdoutSha256 || !Number.isFinite(full.evaluatedAt)
      || full.evaluatedAt > now + 60 || now - full.evaluatedAt > 35 * 86400) throw Error('Fresh full evaluation required');
  for (const name of ['development', 'holdout', 'candidates']) {
    const suite = r.suites?.[name];
    if (!suite?.passed || !suite.counts || ['missed', 'falsePositives', 'incomplete'].some(k => suite.counts[k] !== 0))
      throw Error(`Quality failure in ${name}`);
  }
  if (r.suites.holdout.counts.attacks < 20 || r.suites.holdout.counts.benign < 20) throw Error('Holdout coverage missing');
  if (Object.values(r.suites).some(s => !s.passed)) throw Error('Regression suite failed');
  const b = r.behaviour;
  if (!b?.passed || b.cases !== 72 || b.executed !== 72 || !Number.isFinite(b.evaluatedAt)
      || now - b.evaluatedAt > 35 * 86400 || b.evaluatedAt > now + 60
      || ['missed', 'falsePositives', 'incomplete'].some(k => b[k] !== 0)) throw Error('Behaviour campaign missing or failed');
  if (r.discovery?.status !== 'complete' || r.discovery.generation?.status !== 'ok') throw Error('Adversarial discovery unavailable');
  const smoke = r.behaviourSmoke;
  if (!smoke?.passed || smoke.executed < 3 || !Number.isFinite(smoke.evaluatedAt)
      || smoke.evaluatedAt > now + 60 || now - smoke.evaluatedAt > 48 * 3600
      || smoke.observerSha256 !== b.observerSha256 || smoke.fixturesSha256 !== b.fixturesSha256
      || ['missed', 'falsePositives', 'incomplete'].some(k => smoke[k] !== 0)) throw Error('Daily live sandbox check missing or failed');
  return r;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const build = resolve(process.argv[2] || fileURLToPath(new URL('..', import.meta.url)));
    const response = await fetch('https://histor.modelmarket.dev/security-quality/latest.json',
        { redirect: 'error', signal: AbortSignal.timeout(15000), cache: 'no-store' });
      if (!response.ok) throw Error('Quality service unavailable');
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > 128 * 1024) throw Error('Oversized quality receipt');
    const envelope = JSON.parse(Buffer.from(bytes).toString());
    const report = checkEvidence(envelope, readFileSync(new URL('./quality-public-key.pem', import.meta.url)), buildDigest(build));
    console.log(`MOMUS release gate passed: ${report.run}, ${report.buildDigest}`);
  } catch (error) {
    console.error(`Release blocked: ${error.message}`);
    process.exitCode = 1;
  }
}
