"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.authorization = authorization;
exports.submit = submit;
exports.kindCommand = kindCommand;
const common_1 = require("./common");
const owners_1 = require("./owners");
const cli_input_1 = require("./cli-input");
const receipts_1 = require("./receipts");
const routing_1 = require("./routing");
const decision_1 = require("./decision");
const snapshot_1 = require("./snapshot");
function authorization(flags) {
    if (flags.authorization)
        return (0, cli_input_1.loadJson)(flags.authorization);
    return flags['approval-source'] === 'policy' ? { source: 'policy', ...(flags['policy-decision'] ? { decision: flags['policy-decision'] } : {}), ...(flags['policy-reason'] ? { reason: flags['policy-reason'] } : {}) } : { source: 'user' };
}
async function submit(bobbin, operation, flags, record = false) {
    const preview = await bobbin.preview(operation);
    if (record) {
        (0, common_1.check)(flags.approved === true || flags['approval-source'] === 'policy', 'approval_required', 'Use --approved only after the user settled this exact content, or supply configured policy authorization.', {}, common_1.EXIT.conflict);
        return bobbin.apply(preview, authorization(flags));
    }
    return (0, receipts_1.freezeReceipt)(bobbin, preview, flags['receipt-file']);
}
async function kindCommand(bobbin, kind, command, words, flags) {
    const number = (key) => flags[key] === undefined ? undefined : Number(flags[key]);
    if (command === 'workflow')
        return kindCommand(bobbin, kind, words.shift() ?? 'help', words, flags);
    if (command === 'init') {
        const settings = bobbin.settings(), existing = settings.config?.features ?? settings.registered_features;
        return bobbin.initialize({ features: [...new Set([...existing, ...(common_1.contracts.owners[kind] ? [kind] : [])])], host: flags.host });
    }
    if (command === 'schema' && kind === 'decision') {
        const assessment = (0, decision_1.decisionAssessmentContract)();
        assessment.conflict_revisit.rule = assessment.conflict_revisit.rule.replace('comparison_input.sections', 'the hydrated comparison.current sections');
        return { ...common_1.contracts.owners.decision.schema, comparison_contract: { schema: decision_1.DECISION_ASSESSMENT_CONTRACT, ...assessment, reuse_rule: 'Resolve sections_ref only from complete actual sections still in the caller context. If unavailable, omit knownCurrent and compare again before judging.' } };
    }
    if (command === 'schema')
        return common_1.contracts.owners[kind]?.schema ?? { capability: common_1.contracts.capabilities[kind] };
    if (command === 'capabilities')
        return common_1.contracts.capabilities[kind];
    if (['read', 'load'].includes(command)) {
        if (kind === 'assumption' || kind === 'term')
            (0, common_1.check)(flags.signal === (kind === 'assumption' ? 'assumption-relevant' : 'term-encountered'), 'signal_required', 'Provide the owner-specific recall signal.', {}, common_1.EXIT.conflict);
        const result = await bobbin.read(flags.id ?? words[0], { sections: (0, cli_input_1.list)(flags.section), maxBytes: number('max-bytes') });
        (0, common_1.check)(result.kind === kind, 'artifact_kind_mismatch', 'Artifact has the wrong record kind.', {}, common_1.EXIT.notFound);
        return result;
    }
    if (['search', 'list', 'brief'].includes(command)) {
        if (command !== 'brief' && common_1.contracts.owners[kind])
            return bobbin.search(kind, { query: flags.query, scope: flags.scope, key: flags[kind + '-key'], limit: number('limit'), includeHistory: !!flags['include-history'], signal: flags.signal });
        const facets = (0, cli_input_1.list)(flags.facet).map((x) => {
            const i = x.indexOf('=');
            (0, common_1.check)(i > 0, 'usage_invalid', 'Facet must be field=value.');
            return [x.slice(0, i), x.slice(i + 1)];
        });
        if (flags.scope)
            facets.push(['scope', flags.scope]);
        for (const key of ['decision-key', 'term-key', 'intent-key', 'document-key'])
            if (flags[key])
                facets.push([key.replaceAll('-', '_'), flags[key]]);
        return bobbin.recall({ areas: [kind], includeArchive: kind === 'archive', query: flags.query ?? '', facets, includeHistory: !!flags['include-history'], limit: number('limit'), pack: command === 'brief' || flags.pack, readIds: (0, cli_input_1.list)(flags.id), sections: (0, cli_input_1.list)(flags.section), maxBytes: number('max-bytes') });
    }
    if (kind === 'decision' && ['check', 'conflicts', 'compare'].includes(command)) {
        const knownCurrent = flags['known-current'] === undefined ? undefined : (0, cli_input_1.list)(flags['known-current']).map((value) => {
            const separator = value.indexOf(':');
            (0, common_1.check)(separator > 0, 'usage_invalid', 'Use --known-current ID:SHA256 once per retained Current body.');
            return { id: value.slice(0, separator), sha256: value.slice(separator + 1) };
        });
        const options = { statement: flags.statement ?? flags['decision-key'], scope: flags.scope, decisionKey: flags['decision-key'], rationale: flags.rationale, query: flags.query, limit: number('limit'), knownCurrent };
        return command === 'compare' ? bobbin.compareDecision(options) : bobbin.checkDecision(options);
    }
    if (kind === 'decision' && command === 'spec-view')
        return bobbin.specView(flags.scope, number('max-bytes'));
    if (kind === 'decision' && command === 'revisit')
        return bobbin.revisitDecisions({ ids: (0, cli_input_1.list)(flags.id), due: !!flags.due, asOf: flags['as-of'] });
    if (command === 'apply')
        return (0, receipts_1.applyReceipt)(bobbin, flags['receipt-file'], flags['approved-digest'], authorization(flags), !!flags['keep-receipt']);
    if (command === 'reject')
        return (0, receipts_1.rejectReceipt)(bobbin, flags['receipt-file']);
    if (command === 'candidate' && words[0] === 'prepare')
        return { candidate: (0, cli_input_1.captureArguments)(kind, flags, true).candidate };
    if (command === 'candidate-batch') {
        const candidates = (0, routing_1.validateCandidateBatch)((0, cli_input_1.loadJson)(flags.batch ?? flags.input));
        return { status: 'valid', count: candidates.length, physical_write: false };
    }
    if (command === 'decline')
        return (0, routing_1.declineOwnerResult)((0, cli_input_1.loadJson)(flags.candidate), kind, flags.reason ?? flags['decline-reason']);
    if (['same-claim-input', 'supersede', 'import-fallback'].includes(command) || flags.supersede) {
        const raw = flags['successor-result'] ? (0, routing_1.operationFromOwnerResult)((0, cli_input_1.loadJson)(flags['successor-result'])) : flags['successor-candidate'] ? { candidate: (0, cli_input_1.loadJson)(flags['successor-candidate']), attestation: (0, cli_input_1.loadJson)(flags['claim-attestation'] ?? flags.attestation) } : flags.input ? (0, cli_input_1.loadJson)(flags.input) : (0, cli_input_1.captureArguments)(kind, flags);
        const successor = { action: 'capture', ...raw, id: flags['successor-id'] ?? raw.id ?? (0, common_1.newId)(), ...(flags.now ? { now: flags.now } : {}), ...(flags.filename ? { filename: flags.filename } : {}) };
        const id = flags.id ?? flags.supersede, semantic = await bobbin.prepareSameClaim(id, successor);
        if (command === 'same-claim-input')
            return { input: semantic, input_digest: (0, common_1.canonicalDigest)(semantic), successor };
        let sameClaim = flags['same-claim-attestation'] ? (0, cli_input_1.loadJson)(flags['same-claim-attestation']) : flags['lifecycle-attestation'] ? (0, cli_input_1.loadJson)(flags['lifecycle-attestation']) : undefined;
        if (!sameClaim) {
            (0, common_1.check)(flags['attest-same-claim'] === true, 'semantic_attestation_invalid', 'Supersession requires a same-claim attestation. Compare the predecessor and successor actual bodies, scope, and rationale; only if they address the same governing claim, retry with --attest-same-claim or --same-claim-attestation @<file> bound to that comparison. Approval does not supply this attestation.');
            sameClaim = (0, owners_1.createAttestation)(semantic, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
        }
        return submit(bobbin, { action: 'supersede', id, successor, sameClaim, ...(flags.now ? { now: flags.now } : {}) }, flags, command === 'record' || !!flags.approved);
    }
    if (['save', 'preview', 'record', 'capture', 'claim', 'draft'].includes(command) && !flags.withdraw) {
        const capture = { ...(flags.input ? (0, cli_input_1.loadJson)(flags.input) : { action: 'capture', ...(0, cli_input_1.captureArguments)(kind, flags), ...(flags.filename ? { filename: flags.filename } : {}), ...(flags.now ? { now: flags.now } : {}), ...(flags.id ? { id: flags.id } : {}), ...(flags['ack-conflicts'] ? { acknowledgements: (0, cli_input_1.list)(flags['ack-conflicts']).flatMap((x) => x.split(',')) } : {}) }), targetKind: kind };
        (0, common_1.check)(capture.action === 'capture', 'usage_invalid', 'Owner capture requires a capture operation.');
        if (command === 'draft' || command === 'claim')
            return (0, routing_1.draftOwnerResult)(capture.candidate, capture.attestation, { ...capture, targetKind: kind });
        return submit(bobbin, capture, flags, command === 'record' || !!flags.approved);
    }
    if (['annotate', 'update', 'invalidate', 'withdraw', 'confirm', 'refute', 'deprecate', 'discard', 'rename', 'reverify'].includes(command) || flags.withdraw) {
        const actual = flags.withdraw ? 'withdraw' : command, values = {}, sections = {}, id = flags.id ?? flags.withdraw;
        if (['invalidate', 'withdraw', 'confirm', 'refute', 'deprecate'].includes(actual)) {
            values.reason = { invalidate: 'invalidated', withdraw: 'withdrawn', confirm: 'confirmed', refute: 'refuted', deprecate: 'deprecated' }[actual];
            if (['invalidate', 'withdraw'].includes(actual))
                values.note = flags.reason;
            if (['confirm', 'refute'].includes(actual))
                values.evidence_refs = (0, cli_input_1.bodyItems)(flags['evidence-ref']);
            if (actual === 'refute') {
                values.refutation_reason = flags.reason;
                values.impacted_decisions = (0, cli_input_1.list)(flags['impacted-decision']);
            }
            if (actual === 'deprecate') {
                values.deprecation_reason = flags.reason;
                if (flags['replacement-term'])
                    values.replacement_term = flags['replacement-term'];
            }
            return submit(bobbin, { action: 'retire', id, values, ...(flags.now ? { now: flags.now } : {}) }, flags, command === 'record' || !!flags.approved);
        }
        for (const key of ['title', 'summary'])
            if (flags[key] !== undefined)
                values[key] = flags[key];
        for (const [flag, key] of [['tag', 'tags'], ['search-term', 'search_terms'], ['source-ref', 'source_refs'], ['anchor', 'anchors']])
            if (flags[flag] !== undefined)
                values[key] = (0, cli_input_1.list)(flags[flag]);
        if (kind === 'observation' && flags.related)
            values.related = (0, cli_input_1.list)(flags.related);
        if (actual === 'update') {
            const inputs = (0, cli_input_1.inlineInputs)(kind, flags);
            for (const [field, name] of Object.entries(owners_1.sectionFields[kind]))
                if (inputs[field] !== undefined)
                    sections[name] = Array.isArray(inputs[field]) ? kind === 'snapshot' ? (0, snapshot_1.renderSnapshotList)(inputs[field]) : inputs[field].map((x) => '- ' + x).join('\n') : inputs[field];
        }
        if (actual === 'reverify') {
            values.verified_at = flags['verified-at'];
            values.evidence_ref = flags['evidence-ref'];
        }
        return submit(bobbin, { action: actual, id, values, sections, merge: !!flags.merge, clear: (0, cli_input_1.list)(flags.clear), ...(flags.filename ? { filename: flags.filename } : {}), ...(flags.now ? { now: flags.now } : {}) }, flags, !!flags.approved);
    }
    if (['batch', 'plan'].includes(command) && words[0] === 'validate')
        return bobbin.validate((0, cli_input_1.loadJson)(flags['plan-bundle'] ?? flags['owner-result'] ?? flags.input ?? flags.batch));
    (0, common_1.fail)('usage_invalid', `Unknown ${kind} command: ${command}.`);
}
