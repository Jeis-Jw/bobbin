#!/usr/bin/env node
"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.runCli = runCli;
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
const store_1 = require("./store");
const common_1 = require("./common");
const documents_1 = require("./documents");
const cli_input_1 = require("./cli-input");
const commands_1 = require("./commands");
const receipts_1 = require("./receipts");
const cli_options_json_1 = __importDefault(require("./cli-options.json"));
const routing_1 = require("./routing");
const cli_input_2 = require("./cli-input");
const booleanFlags = new Set(['json', 'help', 'check', 'include-history', 'include-archive', 'pack', 'strict-index', 'confirm-legacy-stopped', 'inline', 'approved', 'keep-receipt', 'merge', 'due', ...Object.entries(cli_options_json_1.default).filter(([, v]) => v === 'boolean').map(([k]) => k), ...['explicit-choice', 'scope-identified', 'commitment-present', 'assumption-present', 'unverified-ok', 'term-identified', 'definition-present', 'intent-present', 'desired-direction', 'content-present', 'living-document', 'same-claim'].map(k => 'attest-' + k)]);
const extraFlags = new Set(['input', 'authorization', 'project', 'features', 'approval-mode', 'lock-timeout-ms', 'now', 'successor-id', 'same-claim-attestation', 'commitment-evidence', 'project-signal', 'sec-success-criterion', 'sec-constraint', 'alias', 'deprecated-term', 'related-term', 'supersede', 'withdraw', 'clear', 'sec-capture-candidates', 'attestation', 'receipt-file', 'approved-digest', 'approval-source', 'policy-decision', 'policy-reason', 'core-cli']);
function defaultProject() {
    const current = process.env.BOBBIN_PROJECT_ROOT ? path.resolve(process.env.BOBBIN_PROJECT_ROOT) : process.cwd();
    for (let candidate = current;; candidate = path.dirname(candidate)) {
        if (fs.existsSync(path.join(candidate, '.bobbin')) || fs.existsSync(path.join(candidate, 'context/context.index.md')))
            return candidate;
        if (path.dirname(candidate) === candidate)
            break;
    }
    return current;
}
async function runCli(argv) {
    try {
        const args = [...argv], flags = {}, positional = [];
        for (let i = 0; i < args.length; i++) {
            const arg = args[i];
            if (!arg.startsWith('--')) {
                positional.push(arg);
                continue;
            }
            const key = arg.slice(2);
            (0, common_1.check)(booleanFlags.has(key) || Object.hasOwn(cli_options_json_1.default, key) || extraFlags.has(key), 'usage_invalid', `Unknown option --${key}.`);
            if (booleanFlags.has(key))
                flags[key] = true;
            else {
                if (i + 1 >= args.length || args[i + 1].startsWith('--'))
                    (0, common_1.fail)('usage_invalid', `Missing value for ${arg}.`);
                const value = args[++i];
                flags[key] = flags[key] === undefined ? value : Array.isArray(flags[key]) ? [...flags[key], value] : [flags[key], value];
            }
        }
        const command = positional.shift() ?? 'help';
        flags.id ??= flags.identifier;
        flags.now ??= flags['created-at'] ?? flags['updated-at'] ?? flags['retired-at'];
        if (command === 'help' || flags.help) {
            process.stdout.write('Bobbin: filesystem context for Node.js clients and agents\n\nCommands: init, read ID, recall, preview, apply, refresh, doctor, schema, capabilities, runtime adopt\nUse --vault DIR [--project DIR]. preview --input JSON | @FILE; apply --input @PREVIEW --authorization JSON.\nAll results are JSON; failures use exit codes 2 (usage), 3 (missing), 5 (conflict), 6 (integrity).\n');
            return 0;
        }
        if (command === 'schema' || command === 'capabilities') {
            const result = command === 'schema' ? { ...common_1.contracts.core_schema, runtime: { language: 'typescript', node: '>=20.20.0', version: common_1.VERSION, library: '@bobbin/context', preview_schema: 'bobbin-preview/v1' } } : { protocol: common_1.PROTOCOL, capabilities: Object.values(common_1.contracts.capabilities) };
            process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
            return 0;
        }
        if (flags['core-cli']) {
            const permitted = [__filename, path.resolve(__dirname, '../skills/context/scripts/context_cli.mjs')];
            (0, common_1.check)(permitted.some(p => fs.existsSync(p) && fs.existsSync(flags['core-cli']) && fs.realpathSync(p) === fs.realpathSync(flags['core-cli'])), 'core_mismatch', 'This package uses its own core; an external checkout cannot replace it.', {}, common_1.EXIT.conflict);
        }
        const bobbin = (0, store_1.createBobbin)({ vault: flags.vault, project: flags.project ?? (flags.vault ? undefined : defaultProject()), lockTimeoutMs: flags['lock-timeout-ms'] ? Number(flags['lock-timeout-ms']) : undefined });
        let result;
        if (documents_1.kinds.includes(command))
            result = await (0, commands_1.kindCommand)(bobbin, command, positional.shift() ?? 'help', positional, flags);
        else
            switch (command) {
                case 'settings':
                    result = bobbin.settings();
                    break;
                case 'init':
                case 'bootstrap':
                    result = await bobbin.initialize({ features: flags.features !== undefined ? String(flags.features).split(',').filter(Boolean) : undefined, approvalMode: flags['approval-mode'], host: flags.host });
                    break;
                case 'validate':
                    result = await bobbin.validate((0, cli_input_1.loadJson)(flags.input));
                    break;
                case 'area':
                    (0, common_1.check)(positional[0] === 'register', 'usage_invalid', 'Expected area register.');
                    result = await bobbin.registerArea((0, cli_input_1.loadJson)(flags.descriptor), (0, cli_input_2.loadBody)(flags['index-seed'], 16384));
                    break;
                case 'candidate':
                    (0, common_1.check)(positional[0] === 'route', 'usage_invalid', 'Expected candidate route.');
                    result = bobbin.route((0, cli_input_1.loadJson)(flags.batch ?? flags.input), (0, cli_input_1.loadJson)(flags['claim-results'] ?? flags['owner-results']));
                    break;
                case 'draft':
                    result = (0, routing_1.draftOwnerResult)((0, cli_input_1.loadJson)(flags.candidate), (0, cli_input_1.loadJson)(flags.attestation), { filename: flags.filename, now: flags.now, id: flags.id, targetKind: flags.kind });
                    break;
                case 'lifecycle':
                    (0, common_1.check)(positional[0] === 'prepare', 'usage_invalid', 'Expected lifecycle prepare.');
                    result = await bobbin.prepareSameClaim(flags.predecessor ?? flags.id, (0, routing_1.operationFromOwnerResult)((0, cli_input_1.loadJson)(flags['successor-result'])));
                    break;
                case 'runtime':
                    if (positional[0] === 'adopt')
                        result = bobbin.adoptLegacy(!!flags['confirm-legacy-stopped']);
                    else if (positional[0] === 'recover')
                        result = await bobbin.recoverRuntime();
                    else
                        (0, common_1.fail)('usage_invalid', 'Expected runtime adopt or recover.');
                    break;
                case 'read':
                    result = await bobbin.read(positional[0] ?? flags.id, { sections: (0, cli_input_1.list)(flags.section), maxBytes: flags['max-bytes'] ? Number(flags['max-bytes']) : undefined });
                    break;
                case 'recall':
                case 'search':
                    result = await bobbin.recall({ query: flags.query ?? positional.join(' '), areas: (0, cli_input_1.list)(flags.area), includeHistory: !!flags['include-history'], includeArchive: !!flags['include-archive'], pack: !!flags.pack, sections: (0, cli_input_1.list)(flags.section), readIds: (0, cli_input_1.list)(flags.read), limit: flags.limit ? Number(flags.limit) : undefined, maxBytes: flags['max-bytes'] ? Number(flags['max-bytes']) : undefined, strictIndex: !!flags['strict-index'] });
                    break;
                case 'preview':
                    result = await bobbin.preview((0, cli_input_1.loadJson)(flags.input));
                    break;
                case 'apply': {
                    const input = (0, cli_input_1.loadJson)(flags.input);
                    result = await bobbin.apply(input.result ?? input, (0, commands_1.authorization)(flags));
                    break;
                }
                case 'transaction':
                    if (positional[0] === 'apply') {
                        if (flags['receipt-file'])
                            result = await (0, receipts_1.applyReceipt)(bobbin, flags['receipt-file'], flags['approved-digest'], (0, commands_1.authorization)(flags));
                        else {
                            const preview = (0, cli_input_1.loadJson)(flags['plan-bundle']);
                            (0, common_1.check)(flags['approved-digest'] === preview.approval_digest, 'approval_digest_mismatch', 'Approved digest differs.', {}, common_1.EXIT.conflict);
                            result = await bobbin.apply(preview, (0, commands_1.authorization)(flags));
                        }
                    }
                    else if (positional[0] === 'preview') {
                        const draft = (0, cli_input_1.loadJson)(flags['owner-result'] ?? flags.input);
                        result = (0, receipts_1.freezeReceipt)(bobbin, await bobbin.preview(draft.schema === 'context-owner-result/v1' ? (0, routing_1.operationFromOwnerResult)(draft) : draft.operation ?? draft), flags['receipt-file']);
                    }
                    else
                        (0, common_1.fail)('usage_invalid', 'Expected transaction preview or apply.');
                    break;
                case 'rename':
                case 'discard':
                    result = await (0, commands_1.submit)(bobbin, { action: command, id: flags.id, ...(flags.filename ? { filename: flags.filename } : {}) }, flags);
                    break;
                case 'refresh':
                    result = await bobbin.refresh(flags.fix === 'index');
                    break;
                case 'doctor':
                    result = { protocol: common_1.PROTOCOL, version: common_1.VERSION, runtime: { node: process.versions.node, electron: process.versions.electron ?? null, python_required: false }, vault: bobbin.vault, project: bobbin.project, ...await bobbin.refresh() };
                    break;
                default: (0, common_1.fail)('usage_invalid', `Unknown command: ${command}.`);
            }
        if ((command === 'refresh' || command === 'doctor') && result.ok === false) {
            process.stdout.write(JSON.stringify({ ok: false, error: { code: 'integrity_error', message: 'Record or index validation found issues.', details: result } }) + '\n');
            return common_1.EXIT.integrity;
        }
        process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
        return 0;
    }
    catch (error) {
        const e = (0, common_1.toBobbinError)(error);
        process.stdout.write(JSON.stringify(e.envelope()) + '\n');
        return e.exitCode;
    }
}
if (require.main === module)
    runCli(process.argv.slice(2)).then(code => { process.exitCode = code; });
