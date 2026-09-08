// Install the packed @bobbin/context tarball in this example's directory first.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { createBobbin, createCandidate, createAttestation } = require('@bobbin/context');
async function main() {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-consumer-'));
    const bobbin = createBobbin({ vault });
    await bobbin.initialize();
    const candidate = createCandidate({
        kind: 'snapshot', title: 'Consumer handoff', summary: 'Resume the consumer test',
        ownerInputs: { current_context: 'A Node consumer uses the shared core.', open_items: ['Check CLI interoperability'], next_steps: ['Read the record through the CLI'] },
    });
    const attestation = createAttestation(candidate, [
        { name: 'handoff_requested', value: true, evidence_pointers: ['/owner_inputs/snapshot/current_context'] },
        { name: 'unfinished_context_present', value: true, evidence_pointers: ['/owner_inputs/snapshot/open_items/0'] },
    ]);
    const preview = await bobbin.preview({ action: 'capture', candidate, attestation });
    await bobbin.apply(preview, { source: 'user' });
    const cli = path.join(path.dirname(require.resolve('@bobbin/context')), 'cli.js');
    // No Python, Git, plugin installation, global npm install, or developer checkout on PATH.
    const run = args => JSON.parse(execFileSync(process.execPath, [cli, ...args, '--vault', vault, '--json'], { encoding: 'utf8', env: { ...process.env, PATH: '' } }));
    const read = run(['read', preview.operation.id]);
    assert.equal(read.result.sections['Current context'], candidate.claim);
    const input = path.join(vault, 'operation.json');
    fs.writeFileSync(input, JSON.stringify({ action: 'update', id: preview.operation.id, merge: true, sections: { 'Next steps': '- Resume in a second client' } }));
    const fromCli = run(['preview', '--input', '@' + input]);
    const frozen = path.join(vault, 'preview.json');
    fs.writeFileSync(frozen, JSON.stringify(fromCli));
    run(['apply', '--input', '@' + frozen, '--authorization', '{"source":"user"}']);
    assert.equal((await bobbin.read(preview.operation.id)).sections['Next steps'], '- Resume in a second client');
    assert.equal((await bobbin.refresh()).ok, true);
    console.log(JSON.stringify({ ok: true, vault, node: process.versions.node, electron: process.versions.electron ?? null, library_to_cli: true, cli_to_library: true, python_required: false }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
