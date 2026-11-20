// Serves tools.json as the literal text it was written in, so number spellings reach the client unchanged.
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
const raw = readFileSync(new URL('./tools.json', import.meta.url), 'utf8').replace(/\n/g, '');
const out = text => process.stdout.write(text + '\n');
createInterface({ input: process.stdin }).on('line', line => {
  const m = JSON.parse(line);
  const id = JSON.stringify(m.id);
  if (m.method === 'initialize') out(`{"jsonrpc":"2.0","id":${id},"result":{"protocolVersion":${JSON.stringify(m.params.protocolVersion)},"capabilities":{"tools":{}},"serverInfo":{"name":"vectors","version":"1.0.0"}}}`);
  else if (m.method === 'tools/list') out(`{"jsonrpc":"2.0","id":${id},"result":{"tools":${raw}}}`);
  else if (m.id !== undefined) out(`{"jsonrpc":"2.0","id":${id},"error":{"code":-32601,"message":"no such method"}}`);
});
