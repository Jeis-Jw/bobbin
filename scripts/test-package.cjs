/* Build the actual tarball, install it into a fresh consumer and use that package. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '..');
const version = require('../plugins/bobbin/.codex-plugin/plugin.json').version;
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bobbin-package-'));
const consumer = path.join(directory, 'consumer');
fs.mkdirSync(consumer);
const cache = path.join(os.tmpdir(), 'bobbin-npm-cache');
const npm = process.env.npm_execpath;
assert.ok(npm, 'Run npm run test:package to use its configured npm CLI.');
function run(executable, args, options = {}) {
    const r = spawnSync(executable, args, { cwd: consumer, encoding: 'utf8', timeout: 60000, maxBuffer: 16 * 1024 * 1024, ...options });
    assert.equal(r.status, 0, r.error?.message || r.stderr || r.stdout);
    return r.stdout;
}
(async () => {
    const packed = JSON.parse(run(process.execPath, [npm, 'pack', '--json', '--pack-destination', directory, '--cache', cache], { cwd: root }))[0];
    assert.equal(packed.version, version);
    const tarball = path.join(directory, packed.filename);
    assert.ok(packed.files.some(f => f.path === 'plugins/bobbin/dist/index.d.ts'));
    assert.ok(packed.files.some(f => f.path.endsWith('bobbin_init.mjs')));
    assert.ok(!packed.files.some(f => /(?:^tests\/|^src\/|node_modules|\.py(?:c)?$|\.git\/)/.test(f.path)));
    fs.writeFileSync(path.join(consumer, 'package.json'), JSON.stringify({ name: 'bobbin-isolated-consumer', private: true }));
    run(process.execPath, [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', cache, tarball]);
    fs.copyFileSync(path.join(root, 'examples/consumer.cjs'), path.join(consumer, 'consumer.cjs'));
    const nodeResult = JSON.parse(run(process.execPath, ['consumer.cjs'], { env: { ...process.env, PATH: '' } }));
    const imported = run(process.execPath, ['--input-type=module', '-e', "import { createBobbin } from '@bobbin/context'; if(typeof createBobbin!=='function')process.exit(1); console.log('ESM import passed');"], { env: { ...process.env, PATH: '' } });
    const packageRoot = path.join(consumer, 'node_modules/@bobbin/context');
    assert.equal(require(path.join(packageRoot, 'package.json')).version, version);
    assert.equal(require(packageRoot).VERSION, version);
    const skills = path.join(packageRoot, 'plugins/bobbin/skills');
    const vault = fs.mkdtempSync(path.join(directory, 'plugin-vault-'));
    const wrapped = (entry, args) => JSON.parse(run(process.execPath, [path.join(skills, entry), ...args, '--vault', vault, '--json'], { env: { ...process.env, PATH: '' } }));
    assert.equal(wrapped('init/scripts/bobbin_init.mjs', ['--features', 'decision,intent,document', '--host', 'codex']).result.version, version);
    assert.equal(wrapped('context/scripts/context_cli.mjs', ['schema']).result.runtime.version, version);
    wrapped('decision/scripts/decision_workflow.mjs', ['record', '--inline', '--approved', '--title', 'Package decision', '--summary', 'Installed plugin flow', '--scope', 'package', '--decision-key', 'storage', '--commitment-evidence', 'The owner explicitly selected local storage.', '--sec-decision', 'Use local files.', '--sec-rationale', 'The consumer must work offline.', '--sec-alternatives', 'Hosted storage requires a network.', '--attest-explicit-choice', '--attest-scope-identified', '--attest-commitment-present']);
    const checked = wrapped('decision/scripts/decision_cli.mjs', ['check', '--statement', 'Use local files.', '--scope', 'package', '--decision-key', 'storage']);
    assert.equal(checked.result.comparison_input.current.length, 1);
    assert.equal(wrapped('context/scripts/context_cli.mjs', ['doctor']).result.record_count, 1);
    const guidance = run(process.execPath, ['--test', path.join(root, 'tests/node/guidance.test.cjs'), path.join(root, 'tests/node/decision-navigation.test.cjs')], { env: { ...process.env, BOBBIN_TEST_PACKAGE_ROOT: packageRoot } });
    fs.writeFileSync(path.join(consumer, 'consumer.ts'), "import {createBobbin,createCandidate,ReadResult} from '@bobbin/context'; const b=createBobbin({vault:'/an/existing/directory'}); const c=createCandidate({kind:'snapshot',title:'Handoff',summary:'Resume',ownerInputs:{current_context:'Continue',open_items:['Check'],next_steps:['Run']}}); const read:Promise<ReadResult>=b.read('ctx_...'); void c; void read;\n");
    run(process.execPath, [require.resolve('typescript/bin/tsc'), '--noEmit', '--strict', '--module', 'Node16', '--target', 'ES2022', '--types', 'node', '--typeRoots', path.join(root, 'node_modules/@types'), 'consumer.ts']);
    const result = { ok: true, version, tarball, integrity: packed.integrity, files: packed.files.length, package_bytes: packed.size, node: nodeResult, esm_import: imported.trim(), typescript_consumer: true, plugin_flow: true, shipped_guidance: guidance.trim() };
    if (process.env.BOBBIN_TEST_NODE)
        result.node_minimum = JSON.parse(run(process.env.BOBBIN_TEST_NODE, [path.join(consumer, 'consumer.cjs')], { env: { ...process.env, PATH: '' } }));
    if (process.env.BOBBIN_TEST_ELECTRON) {
        result.electron = JSON.parse(run(process.env.BOBBIN_TEST_ELECTRON, [path.join(consumer, 'consumer.cjs')], { env: { ...process.env, PATH: '', ELECTRON_RUN_AS_NODE: '1' } }));
        if (process.env.BOBBIN_TEST_ASAR) {
            const asar = await import(pathToFileURL(process.env.BOBBIN_TEST_ASAR).href);
            const appRoot = path.join(directory, 'app');
            fs.mkdirSync(appRoot);
            fs.cpSync(path.join(consumer, 'node_modules'), path.join(appRoot, 'node_modules'), { recursive: true, verbatimSymlinks: true });
            fs.writeFileSync(path.join(appRoot, 'package.json'), JSON.stringify({ name: 'bobbin-asar-consumer', version: '1.0.0', main: 'main.cjs' }));
            fs.writeFileSync(path.join(appRoot, 'main.cjs'), `const {app}=require('electron');const fs=require('node:fs'),os=require('node:os'),path=require('node:path');const {createBobbin}=require('@bobbin/context');app.whenReady().then(async()=>{const vault=fs.mkdtempSync(path.join(os.tmpdir(),'bobbin-asar-vault-'));const b=createBobbin({vault});await b.initialize();if(!(await b.refresh()).ok)throw Error('Invalid indexes');console.log(JSON.stringify({asar:true,electron:process.versions.electron,node:process.versions.node}));app.exit(0);}).catch(e=>{console.error(e);app.exit(1);});`);
            const archive = path.join(directory, 'consumer.asar');
            await asar.createPackage(appRoot, archive);
            const env = { ...process.env };
            delete env.ELECTRON_RUN_AS_NODE;
            if (process.env.BOBBIN_ASAR_BUILD_ONLY === '1')
                result.asar_artifact = archive;
            else {
                const output = run(process.env.BOBBIN_TEST_ELECTRON, [archive, '--user-data-dir=' + path.join(directory, 'electron-data')], { env, timeout: 30000 });
                result.asar = JSON.parse(output.trim().split('\n').find(line => line.startsWith('{"asar":')));
            }
        }
    }
    fs.writeFileSync(path.join(directory, 'evidence.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
})().catch(error => { console.error(error); process.exitCode = 1; });
