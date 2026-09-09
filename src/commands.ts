import { Bobbin, Operation, CaptureOperation, Authorization } from './store';
import { ObjectValue, check, fail, contracts, canonicalDigest, newId, EXIT } from './common';
import { Kind } from './documents';
import { sectionFields, draftCapture, createAttestation, validateCandidate } from './owners';
import { list, loadJson, bodyItems, captureArguments, inlineInputs } from './cli-input';
import { freezeReceipt, applyReceipt, rejectReceipt } from './receipts';
import { draftOwnerResult, declineOwnerResult, validateCandidateBatch, operationFromOwnerResult } from './routing';
import { renderSnapshotList } from './snapshot';
export function authorization(flags: ObjectValue): Authorization {
    if (flags.authorization)
        return loadJson(flags.authorization);
    return flags['approval-source'] === 'policy' ? { source: 'policy', ...(flags['policy-decision'] ? { decision: flags['policy-decision'] } : {}), ...(flags['policy-reason'] ? { reason: flags['policy-reason'] } : {}) } : { source: 'user' };
}
export async function submit(bobbin: Bobbin, operation: Operation, flags: ObjectValue, record = false): Promise<ObjectValue> {
    const preview = await bobbin.preview(operation);
    if (record) {
        check(flags.approved === true || flags['approval-source'] === 'policy', 'approval_required', 'Use --approved only after the user settled this exact content, or supply configured policy authorization.', {}, EXIT.conflict);
        return bobbin.apply(preview, authorization(flags));
    }
    return freezeReceipt(bobbin, preview, flags['receipt-file']);
}
export async function kindCommand(bobbin: Bobbin, kind: Kind, command: string, words: string[], flags: ObjectValue): Promise<ObjectValue> {
    const number = (key: string) => flags[key] === undefined ? undefined : Number(flags[key]);
    if (command === 'workflow')
        return kindCommand(bobbin, kind, words.shift() ?? 'help', words, flags);
    if (command === 'init') {
        const settings = bobbin.settings(), existing = settings.config?.features ?? settings.registered_features;
        return bobbin.initialize({ features: [...new Set([...existing, ...(contracts.owners[kind] ? [kind] : [])])] as any, host: flags.host });
    }
    if (command === 'schema')
        return contracts.owners[kind]?.schema ?? { capability: contracts.capabilities[kind] };
    if (command === 'capabilities')
        return contracts.capabilities[kind];
    if (['read', 'load'].includes(command)) {
        if (kind === 'assumption' || kind === 'term')
            check(flags.signal === (kind === 'assumption' ? 'assumption-relevant' : 'term-encountered'), 'signal_required', 'Provide the owner-specific recall signal.', {}, EXIT.conflict);
        const result = await bobbin.read(flags.id ?? words[0], { sections: list(flags.section), maxBytes: number('max-bytes') });
        check(result.kind === kind, 'artifact_kind_mismatch', 'Artifact has the wrong record kind.', {}, EXIT.notFound);
        return result;
    }
    if (['search', 'list', 'brief'].includes(command)) {
        if (command !== 'brief' && contracts.owners[kind])
            return bobbin.search(kind, { query: flags.query, scope: flags.scope, key: flags[kind + '-key'], limit: number('limit'), includeHistory: !!flags['include-history'], signal: flags.signal });
        const facets = list(flags.facet).map((x: string) => { const i = x.indexOf('='); check(i > 0, 'usage_invalid', 'Facet must be field=value.'); return [x.slice(0, i), x.slice(i + 1)] as [
            string,
            string
        ]; });
        if (flags.scope)
            facets.push(['scope', flags.scope]);
        for (const key of ['decision-key', 'term-key', 'intent-key', 'document-key'])
            if (flags[key])
                facets.push([key.replaceAll('-', '_'), flags[key]]);
        return bobbin.recall({ areas: [kind], includeArchive: kind === 'archive', query: flags.query ?? '', facets, includeHistory: !!flags['include-history'], limit: number('limit'), pack: command === 'brief' || flags.pack, readIds: list(flags.id), sections: list(flags.section), maxBytes: number('max-bytes') });
    }
    if (kind === 'decision' && ['check', 'conflicts'].includes(command))
        return bobbin.checkDecision({ statement: flags.statement ?? flags['decision-key'], scope: flags.scope, decisionKey: flags['decision-key'], rationale: flags.rationale, query: flags.query, limit: number('limit') });
    if (kind === 'decision' && command === 'spec-view')
        return bobbin.specView(flags.scope, number('max-bytes'));
    if (kind === 'decision' && command === 'revisit')
        return bobbin.revisitDecisions({ ids: list(flags.id), due: !!flags.due, asOf: flags['as-of'] });
    if (command === 'apply')
        return applyReceipt(bobbin, flags['receipt-file'], flags['approved-digest'], authorization(flags), !!flags['keep-receipt']);
    if (command === 'reject')
        return rejectReceipt(bobbin, flags['receipt-file']);
    if (command === 'candidate' && words[0] === 'prepare')
        return { candidate: captureArguments(kind, flags, true).candidate };
    if (command === 'candidate-batch') {
        const candidates = validateCandidateBatch(loadJson(flags.batch ?? flags.input));
        return { status: 'valid', count: candidates.length, physical_write: false };
    }
    if (command === 'decline')
        return declineOwnerResult(loadJson(flags.candidate), kind, flags.reason ?? flags['decline-reason']);
    if (['same-claim-input', 'supersede', 'import-fallback'].includes(command) || flags.supersede) {
        const raw = flags['successor-result'] ? operationFromOwnerResult(loadJson(flags['successor-result'])) : flags['successor-candidate'] ? { candidate: loadJson(flags['successor-candidate']), attestation: loadJson(flags['claim-attestation'] ?? flags.attestation) } : flags.input ? loadJson(flags.input) : captureArguments(kind, flags);
        const successor: CaptureOperation = { action: 'capture', ...raw, id: flags['successor-id'] ?? raw.id ?? newId(), ...(flags.now ? { now: flags.now } : {}), ...(flags.filename ? { filename: flags.filename } : {}) };
        const id = flags.id ?? flags.supersede, semantic = await bobbin.prepareSameClaim(id, successor);
        if (command === 'same-claim-input')
            return { input: semantic, input_digest: canonicalDigest(semantic), successor };
        let sameClaim = flags['same-claim-attestation'] ? loadJson(flags['same-claim-attestation']) : flags['lifecycle-attestation'] ? loadJson(flags['lifecycle-attestation']) : undefined;
        if (!sameClaim) {
            check(flags['attest-same-claim'] === true, 'semantic_attestation_invalid', 'Supersession requires a same-claim attestation. Compare the predecessor and successor actual bodies, scope, and rationale; only if they address the same governing claim, retry with --attest-same-claim or --same-claim-attestation @<file> bound to that comparison. Approval does not supply this attestation.');
            sameClaim = createAttestation(semantic, [{ name: 'same_semantic_claim', value: true, evidence_pointers: ['/predecessor/primary_claim', '/successor/primary_claim'] }], 'same_claim');
        }
        return submit(bobbin, { action: 'supersede', id, successor, sameClaim, ...(flags.now ? { now: flags.now } : {}) }, flags, command === 'record' || !!flags.approved);
    }
    if (['save', 'preview', 'record', 'capture', 'claim', 'draft'].includes(command) && !flags.withdraw) {
        const capture = { ...(flags.input ? loadJson(flags.input) : { action: 'capture', ...captureArguments(kind, flags), ...(flags.filename ? { filename: flags.filename } : {}), ...(flags.now ? { now: flags.now } : {}), ...(flags.id ? { id: flags.id } : {}), ...(flags['ack-conflicts'] ? { acknowledgements: list(flags['ack-conflicts']).flatMap((x: string) => x.split(',')) } : {}) }), targetKind: kind };
        check(capture.action === 'capture', 'usage_invalid', 'Owner capture requires a capture operation.');
        if (command === 'draft' || command === 'claim')
            return draftOwnerResult(capture.candidate, capture.attestation, { ...capture, targetKind: kind });
        return submit(bobbin, capture, flags, command === 'record' || !!flags.approved);
    }
    if (['annotate', 'update', 'invalidate', 'withdraw', 'confirm', 'refute', 'deprecate', 'discard', 'rename', 'reverify'].includes(command) || flags.withdraw) {
        const actual = flags.withdraw ? 'withdraw' : command, values: ObjectValue = {}, sections: Record<string, string> = {}, id = flags.id ?? flags.withdraw;
        if (['invalidate', 'withdraw', 'confirm', 'refute', 'deprecate'].includes(actual)) {
            values.reason = ({ invalidate: 'invalidated', withdraw: 'withdrawn', confirm: 'confirmed', refute: 'refuted', deprecate: 'deprecated' } as ObjectValue)[actual];
            if (['invalidate', 'withdraw'].includes(actual))
                values.note = flags.reason;
            if (['confirm', 'refute'].includes(actual))
                values.evidence_refs = bodyItems(flags['evidence-ref']);
            if (actual === 'refute') {
                values.refutation_reason = flags.reason;
                values.impacted_decisions = list(flags['impacted-decision']);
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
                values[key] = list(flags[flag]);
        if (kind === 'observation' && flags.related)
            values.related = list(flags.related);
        if (actual === 'update') {
            const inputs = inlineInputs(kind, flags);
            for (const [field, name] of Object.entries(sectionFields[kind]))
                if (inputs[field] !== undefined)
                    sections[name] = Array.isArray(inputs[field]) ? kind === 'snapshot' ? renderSnapshotList(inputs[field]) : inputs[field].map((x: string) => '- ' + x).join('\n') : inputs[field];
        }
        if (actual === 'reverify') {
            values.verified_at = flags['verified-at'];
            values.evidence_ref = flags['evidence-ref'];
        }
        return submit(bobbin, { action: actual as any, id, values, sections, merge: !!flags.merge, clear: list(flags.clear), ...(flags.filename ? { filename: flags.filename } : {}), ...(flags.now ? { now: flags.now } : {}) }, flags, !!flags.approved);
    }
    if (['batch', 'plan'].includes(command) && words[0] === 'validate')
        return bobbin.validate(loadJson(flags['plan-bundle'] ?? flags['owner-result'] ?? flags.input ?? flags.batch));
    fail('usage_invalid', `Unknown ${kind} command: ${command}.`);
}
