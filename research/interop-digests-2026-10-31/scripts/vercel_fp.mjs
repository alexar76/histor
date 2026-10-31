// Fingerprint each tool exactly as @ai-sdk/mcp tools() builds it (schemas 'automatic'), with ai@7.0.159.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { fingerprintTools, dynamicTool, jsonSchema } = require('ai');
const lines = readFileSync(process.argv[2], 'utf8').trim().split('\n');
for (const line of lines) {
  const t = JSON.parse(line);
  const inputSchema = t.inputSchema ?? {};
  const tool = dynamicTool({
    description: t.description,
    title: t.title ?? t.annotations?.title,
    inputSchema: jsonSchema({ ...inputSchema, properties: inputSchema.properties ?? {}, additionalProperties: false }),
    execute: async () => null,
  });
  const fp = await fingerprintTools({ [t.name]: tool });
  console.log(fp[t.name]);
}
