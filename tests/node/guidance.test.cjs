const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const packageRoot = process.env.BOBBIN_TEST_PACKAGE_ROOT || path.resolve(__dirname, '../..');
const { createBobbin, createAttestation, newId } = require(packageRoot);
const skills = path.join(packageRoot, 'plugins/bobbin/skills');
const core = path.join(skills, 'context/scripts/context_cli.mjs');
const values = {
    '<codex|claude-code>': 'codex', '<title>': 'Local storage', '<summary>': 'Offline consumer storage',
    '<scope>': 'consumer', '<key>': 'storage', '<evidence>': 'The owner explicitly selected local files.',
    '<decision>': 'Use local files.', '<rationale>': 'The consumer must work offline.',
    '<rejected alternative>': 'Hosted storage requires a network.', '<revisit condition>': 'Revisit if offline use is no longer required.',
};
function source(kind, language) { return fs.readFileSync(path.join(skills, kind, `SKILL${language}.md`), 'utf8'); }
// Parse only the shipped examples' simple quoted arguments; never execute a shell.
function example(language, supersede, replacements = {}) {
    const commands = [...source('decision', language).matchAll(/```bash\n([\s\S]*?)```/g)]
        .flatMap(match => match[1].trim().split(/\n\n/));
    const command = commands.find(value => value.includes('decision_workflow.mjs record') && value.includes('--supersede') === supersede);
    assert.ok(command, 'The shipped skill must contain a complete record example.');
    const tokens = command.replace(/\\\n/g, ' ').match(/'[^']*'|\S+/g).slice(1).map(value => value.replace(/^'|'$/g, ''));
    const supplied = { ...values, ...replacements };
    return tokens.map(value => value.startsWith('/loaded/bobbin/skills/') ? value.replace('/loaded/bobbin/skills', skills) : supplied[value] ?? value);
}
function cli(vault, args, status = 0) {
    const result = spawnSync(process.execPath, [...args, '--vault', vault, '--json'], { cwd: vault, env: { ...process.env, PATH: '', BOBBIN_PROJECT_ROOT: vault }, encoding: 'utf8' });
    assert.equal(result.status, status, result.error?.message || result.stdout || result.stderr);
    return JSON.parse(result.stdout);
}
function tree(directory) {
    return Object.fromEntries(fs.readdirSync(directory, { recursive: true, withFileTypes: true })
        .filter(entry => entry.isFile()).map(entry => {
            const file = path.join(entry.parentPath, entry.name);
            return [path.relative(directory, file), fs.readFileSync(file).toString('base64')];
        }));
}
async function fixture(t, language = '') {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-guidance-'));
    t.after(() => fs.rmSync(vault, { recursive: true, force: true }));
    const b = createBobbin({ vault });
    await b.initialize({ features: ['decision'] });
    assert.equal(cli(vault, example(language, false)).result.applied, true);
    const checked = cli(vault, [path.join(skills, 'decision/scripts/decision_cli.mjs'), 'check', '--statement', values['<decision>'], '--scope', values['<scope>'], '--decision-key', values['<key>']]);
    const current = checked.result.comparison_input.current[0];
    assert.equal(current.scope, values['<scope>']);
    assert.equal(current.sections.Decision, values['<decision>']);
    assert.equal(current.sections.Rationale, values['<rationale>']);
    // Both actual bodies govern offline storage; the successor clarifies its rationale.
    const args = example(language, true, { '<current-id>': current.id, '<title>': 'Offline storage clarification', '<rationale>': 'Local files keep the consumer usable without network access.' });
    return { vault, b, current, args };
}
for (const language of ['', '.ko']) test(`shipped ${language || 'EN'} supersede example succeeds on its first attempt`, async t => {
    const { vault, b, current, args } = await fixture(t, language);
    assert.equal(cli(vault, args).result.applied, true);
    const old = await b.read(current.id), next = await b.read(old.frontmatter.superseded_by);
    assert.equal(old.state, 'history');
    assert.equal(old.sections.Rationale, values['<rationale>']);
    assert.equal(next.state, 'current');
    assert.deepEqual(next.frontmatter.supersedes, [current.id]);
    assert.equal(next.sections.Rationale, args[args.indexOf('--sec-rationale') + 1]);
    assert.equal((await b.refresh()).ok, true);
});
test('missing attestation is actionable and writes nothing; bound attestations remain validated', async t => {
    const { vault, b, current, args } = await fixture(t);
    const missing = args.filter(value => value !== '--attest-same-claim');
    const before = tree(vault), rejected = cli(vault, missing, 2);
    assert.equal(rejected.error.code, 'semantic_attestation_invalid');
    for (const text of ['actual bodies, scope, and rationale', '--attest-same-claim', '--same-claim-attestation @<file>', 'Approval does not supply'])
        assert.ok(rejected.error.message.includes(text));
    assert.deepEqual(tree(vault), before);
    // Keep IDs stable so the two calls describe exactly the same semantic input.
    missing.push('--candidate-id', 'cand_11111111111111111111111111111111', '--successor-id', newId());
    const prepare = missing.filter(value => value !== '--approved').map(value => value === 'record' ? 'same-claim-input' : value);
    const semantic = cli(vault, prepare).result.input;
    const bound = createAttestation(semantic, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
    const invalid = { ...bound, input_digest: '0'.repeat(64) };
    assert.equal(cli(vault, [...missing, '--same-claim-attestation', JSON.stringify(invalid)], 5).error.code, 'semantic_attestation_invalid');
    assert.deepEqual(tree(vault), before);
    assert.equal(cli(vault, [...missing, '--same-claim-attestation', JSON.stringify(bound)]).result.applied, true);
    assert.equal((await b.read(current.id)).state, 'history');
});
test('OBS guidance matches capabilities and executable evidence count/codepoint boundaries', async t => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-observation-guidance-'));
    t.after(() => fs.rmSync(vault, { recursive: true, force: true }));
    await createBobbin({ vault }).initialize();
    const required = cli(vault, [core, 'observation', 'capabilities']).result.draft_fields.required;
    assert.deepEqual(required.evidence, { type: 'string_list', min_items: 1, max_items: 6, max_item_chars: 500 });
    for (const language of ['', '.ko']) {
        const text = source('observation', language);
        assert.ok(text.includes(`1–${required.observation.max_chars.toLocaleString('en-US')}`));
        assert.ok(text.includes(`${required.evidence.min_items}–${required.evidence.max_items}`));
        assert.ok(text.includes(`${required.evidence.max_item_chars} codepoint`));
        assert.ok(text.includes('observation capabilities --json'));
    }
    const base = [core, 'observation', 'preview', '--title', 'Evidence limit', '--summary', 'Reproducible boundary', '--captured-from', 'workspace', '--attest-reusable-observation', '--attest-evidence-present', '--sec-observation', 'The evidence boundary is reproducible.'];
    for (const [index, evidence] of [['😀'.repeat(500)], Array(6).fill('e'.repeat(500))].entries()) {
        const args = [...base];
        args[args.indexOf('--title') + 1] += ` ${index}`;
        const preview = cli(vault, [...args, ...evidence.flatMap(item => ['--sec-evidence', item])]).result;
        assert.equal(preview.applied, false);
        cli(vault, [core, 'transaction', 'apply', '--receipt-file', preview.receipt_file, '--approved-digest', preview.approval_digest]);
    }
    for (const evidence of [[], ['😀'.repeat(501)], Array(7).fill('A verified test result.')]) {
        const before = tree(vault);
        assert.equal(cli(vault, [...base, ...evidence.flatMap(item => ['--sec-evidence', item])], 2).ok, false);
        assert.deepEqual(tree(vault), before);
    }
    assert.ok(!fs.readFileSync(path.join(skills, 'context/references/recording-policy.md'), 'utf8').includes('*_workflow.py'));
});
