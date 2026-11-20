import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createMCPClient } from '@ai-sdk/mcp';
import { StdioMCPTransport } from '@ai-sdk/mcp/mcp-stdio';
import { fingerprintTools } from 'ai';
const pkg = name => JSON.parse(readFileSync(new URL(`./node_modules/${name}/package.json`, import.meta.url), 'utf8')).version;
const client = await createMCPClient({ transport: new StdioMCPTransport({ command: process.execPath, args: [fileURLToPath(new URL('./server.mjs', import.meta.url))] }) });
try {
  const definitions = await client.listTools();
  const fingerprints = await fingerprintTools(client.toolsFromDefinitions(definitions));
  const viaTools = await fingerprintTools(await client.tools());
  writeFileSync(new URL('./vectors.json', import.meta.url), JSON.stringify({
    generated: new Date().toISOString(), node: process.version,
    versions: { ai: pkg('ai'), '@ai-sdk/mcp': pkg('@ai-sdk/mcp'), zod: pkg('zod') },
    sameThroughToolsCall: JSON.stringify(fingerprints) === JSON.stringify(viaTools),
    names: definitions.tools.map(t => t.name), fingerprints }, null, 1) + '\n');
  console.log('tools', definitions.tools.length, 'fingerprints', Object.keys(fingerprints).length, 'same via tools():', JSON.stringify(fingerprints) === JSON.stringify(viaTools));
} finally { await client.close(); }
