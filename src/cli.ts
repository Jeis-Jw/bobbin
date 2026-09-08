#!/usr/bin/env node
import * as fs from 'node:fs';
import * as path from 'node:path';
import { createBobbin } from './store';
import { ObjectValue, toBobbinError, check, fail, contracts, PROTOCOL, VERSION, EXIT } from './common';
import { kinds, Kind } from './documents';
import { list, loadJson as load } from './cli-input';
import { kindCommand, submit, authorization } from './commands';
import { applyReceipt, freezeReceipt } from './receipts';
import optionTypes from './cli-options.json';
import { operationFromOwnerResult, draftOwnerResult, validateCandidateBatch } from './routing';
import { loadBody } from './cli-input';
const booleanFlags = new Set(['json', 'help', 'check', 'include-history', 'include-archive', 'pack', 'strict-index', 'confirm-legacy-stopped', 'inline', 'approved', 'keep-receipt', 'merge', 'due', ...Object.entries(optionTypes).filter(([, v]) => v === 'boolean').map(([k]) => k), ...['explicit-choice', 'scope-identified', 'commitment-present', 'assumption-present', 'unverified-ok', 'term-identified', 'definition-present', 'intent-present', 'desired-direction', 'content-present', 'living-document', 'same-claim'].map(k => 'attest-' + k)]);
const extraFlags = new Set(['input', 'authorization', 'project', 'features', 'approval-mode', 'lock-timeout-ms', 'now', 'successor-id', 'same-claim-attestation', 'commitment-evidence', 'project-signal', 'sec-success-criterion', 'sec-constraint', 'alias', 'deprecated-term', 'related-term', 'supersede', 'withdraw', 'clear', 'sec-capture-candidates', 'attestation', 'receipt-file', 'approved-digest', 'approval-source', 'policy-decision', 'policy-reason', 'core-cli']);
function defaultProject(): string {
    const current = process.env.BOBBIN_PROJECT_ROOT ? path.resolve(process.env.BOBBIN_PROJECT_ROOT) : process.cwd();
    for (let candidate = current;; candidate = path.dirname(candidate)) {
        if (fs.existsSync(path.join(candidate, '.bobbin')) || fs.existsSync(path.join(candidate, 'context/context.index.md')))
            return candidate;
        if (path.dirname(candidate) === candidate)
            break;
    }
    return current;
}
export async function runCli(argv: string[]): Promise<number> {
    try {
        const args = [...argv], flags: ObjectValue = {}, positional: string[] = [];
        for (let i = 0; i < args.length; i++) {
            const arg = args[i];
            if (!arg.startsWith('--')) {
                positional.push(arg);
                continue;
            }
            const key = arg.slice(2);
            check(booleanFlags.has(key) || Object.hasOwn(optionTypes, key) || extraFlags.has(key), 'usage_invalid', `Unknown option --${key}.`);
            if (booleanFlags.has(key))
                flags[key] = true;
            else {
                if (i + 1 >= args.length || args[i + 1].startsWith('--'))
                    fail('usage_invalid', `Missing value for ${arg}.`);
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
            const result = command === 'schema' ? { ...contracts.core_schema, runtime: { language: 'typescript', node: '>=20.20.0', version: VERSION, library: '@bobbin/context', preview_schema: 'bobbin-preview/v1' } } : { protocol: PROTOCOL, capabilities: Object.values(contracts.capabilities) };
            process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
            return 0;
        }
        if (flags['core-cli']) {
            const permitted = [__filename, path.resolve(__dirname, '../skills/context/scripts/context_cli.mjs')];
            check(permitted.some(p => fs.existsSync(p) && fs.existsSync(flags['core-cli']) && fs.realpathSync(p) === fs.realpathSync(flags['core-cli'])), 'core_mismatch', 'This package uses its own core; an external checkout cannot replace it.', {}, EXIT.conflict);
        }
        const bobbin = createBobbin({ vault: flags.vault, project: flags.project ?? (flags.vault ? undefined : defaultProject()), lockTimeoutMs: flags['lock-timeout-ms'] ? Number(flags['lock-timeout-ms']) : undefined });
        let result: ObjectValue;
        if (kinds.includes(command as Kind))
            result = await kindCommand(bobbin, command as Kind, positional.shift() ?? 'help', positional, flags);
        else
            switch (command) {
                case 'settings':
                    result = bobbin.settings();
                    break;
                case 'init':
                case 'bootstrap':
                    result = await bobbin.initialize({ features: flags.features !== undefined ? String(flags.features).split(',').filter(Boolean) : undefined, approvalMode: flags['approval-mode'], host: flags.host } as any);
                    break;
                case 'validate':
                    result = await bobbin.validate(load(flags.input));
                    break;
                case 'area':
                    check(positional[0] === 'register', 'usage_invalid', 'Expected area register.');
                    result = await bobbin.registerArea(load(flags.descriptor), loadBody(flags['index-seed'], 16384));
                    break;
                case 'candidate':
                    check(positional[0] === 'route', 'usage_invalid', 'Expected candidate route.');
                    result = bobbin.route(load(flags.batch ?? flags.input), load(flags['claim-results'] ?? flags['owner-results']));
                    break;
                case 'draft':
                    result = draftOwnerResult(load(flags.candidate), load(flags.attestation), { filename: flags.filename, now: flags.now, id: flags.id, targetKind: flags.kind });
                    break;
                case 'lifecycle':
                    check(positional[0] === 'prepare', 'usage_invalid', 'Expected lifecycle prepare.');
                    result = await bobbin.prepareSameClaim(flags.predecessor ?? flags.id, operationFromOwnerResult(load(flags['successor-result'])));
                    break;
                case 'runtime':
                    if (positional[0] === 'adopt')
                        result = bobbin.adoptLegacy(!!flags['confirm-legacy-stopped']);
                    else if (positional[0] === 'recover')
                        result = await bobbin.recoverRuntime();
                    else
                        fail('usage_invalid', 'Expected runtime adopt or recover.');
                    break;
                case 'read':
                    result = await bobbin.read(positional[0] ?? flags.id, { sections: list(flags.section), maxBytes: flags['max-bytes'] ? Number(flags['max-bytes']) : undefined });
                    break;
                case 'recall':
                case 'search':
                    result = await bobbin.recall({ query: flags.query ?? positional.join(' '), areas: list(flags.area), includeHistory: !!flags['include-history'], includeArchive: !!flags['include-archive'], pack: !!flags.pack, sections: list(flags.section), readIds: list(flags.read), limit: flags.limit ? Number(flags.limit) : undefined, maxBytes: flags['max-bytes'] ? Number(flags['max-bytes']) : undefined, strictIndex: !!flags['strict-index'] });
                    break;
                case 'preview':
                    result = await bobbin.preview(load(flags.input));
                    break;
                case 'apply': {
                    const input = load(flags.input);
                    result = await bobbin.apply(input.result ?? input, authorization(flags));
                    break;
                }
                case 'transaction':
                    if (positional[0] === 'apply') {
                        if (flags['receipt-file'])
                            result = await applyReceipt(bobbin, flags['receipt-file'], flags['approved-digest'], authorization(flags));
                        else {
                            const preview = load(flags['plan-bundle']);
                            check(flags['approved-digest'] === preview.approval_digest, 'approval_digest_mismatch', 'Approved digest differs.', {}, EXIT.conflict);
                            result = await bobbin.apply(preview, authorization(flags));
                        }
                    }
                    else if (positional[0] === 'preview') {
                        const draft = load(flags['owner-result'] ?? flags.input);
                        result = freezeReceipt(bobbin, await bobbin.preview(draft.schema === 'context-owner-result/v1' ? operationFromOwnerResult(draft) : draft.operation ?? draft), flags['receipt-file']);
                    }
                    else
                        fail('usage_invalid', 'Expected transaction preview or apply.');
                    break;
                case 'rename':
                case 'discard':
                    result = await submit(bobbin, { action: command, id: flags.id, ...(flags.filename ? { filename: flags.filename } : {}) }, flags);
                    break;
                case 'refresh':
                    result = await bobbin.refresh(flags.fix === 'index');
                    break;
                case 'doctor':
                    result = { protocol: PROTOCOL, version: VERSION, runtime: { node: process.versions.node, electron: process.versions.electron ?? null, python_required: false }, vault: bobbin.vault, project: bobbin.project, ...await bobbin.refresh() };
                    break;
                default: fail('usage_invalid', `Unknown command: ${command}.`);
            }
        if ((command === 'refresh' || command === 'doctor') && result.ok === false) {
            process.stdout.write(JSON.stringify({ ok: false, error: { code: 'integrity_error', message: 'Record or index validation found issues.', details: result } }) + '\n');
            return EXIT.integrity;
        }
        process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
        return 0;
    }
    catch (error) {
        const e = toBobbinError(error);
        process.stdout.write(JSON.stringify(e.envelope()) + '\n');
        return e.exitCode;
    }
}
if (require.main === module)
    runCli(process.argv.slice(2)).then(code => { process.exitCode = code; });
