import * as path from 'node:path';
import { ObjectValue, check, exact, object, contracts, canonicalDigest, canonicalJson, shortText, stringList, EXIT } from './common';
import { Kind, parseDocument } from './documents';
import { Candidate, Attestation, DraftOptions, draftCapture, primaryClaim, validateAttestation, validateCandidate } from './owners';
import { SNAP_MAX_BYTES, SNAP_TRANSPORT_MAX_BYTES, snapshotCandidate } from './snapshot';
import type { CaptureOperation } from './store';
export interface CandidateBatch {
    schema: 'context-capture-batch/v1';
    audit_count: 1;
    candidates: Candidate[];
}
export function validateCandidateBatch(batch: CandidateBatch | Candidate[]): Candidate[] {
    if (!Array.isArray(batch)) {
        exact(batch, ['schema', 'audit_count', 'candidates'], 'candidate_invalid');
        check(batch.schema === 'context-capture-batch/v1' && batch.audit_count === 1, 'audit_repeated', 'A semantic milestone is audited once.');
    }
    const candidates = Array.isArray(batch) ? batch : batch.candidates;
    check(Array.isArray(candidates) && candidates.length <= 8, 'candidate_batch_too_large', 'At most eight candidates are supported.');
    const snapshots = candidates.filter(snapshotCandidate), ordinary = candidates.filter(c => !snapshotCandidate(c));
    const maximum = snapshots.length ? SNAP_TRANSPORT_MAX_BYTES : candidates.length === 1 && candidates[0].requested_kind === 'archive' ? 512 * 1024 : 16384;
    if (snapshots.length) {
        const ordinaryBatch = Array.isArray(batch) ? ordinary : { ...batch, candidates: ordinary };
        check(Buffer.byteLength(canonicalJson(ordinaryBatch)) <= 16384, 'candidate_batch_too_large', 'Non-SNAP candidates exceed their existing byte budget.');
    }
    check(Buffer.byteLength(snapshots.length ? JSON.stringify(batch) : canonicalJson(batch)) <= maximum, 'candidate_batch_too_large', 'Candidate batch exceeds its byte budget.');
    const ids = new Set<string>();
    for (const c of candidates) {
        check(object(c) && c.schema === 'context-capture-candidate/v1' && /^cand_[0-9a-f]{32}$/.test(c.candidate_id) && !ids.has(c.candidate_id), 'candidate_invalid', 'Invalid or duplicated candidate ID.');
        ids.add(c.candidate_id);
        check(!['claim_key', 'claim_fingerprint', 'source_claim_fingerprint'].some(k => Object.hasOwn(c, k)), 'schema_removed_field', 'Semantic identity surrogates are not permitted.');
        shortText(c.title, 'title', 120);
        shortText(c.summary, 'summary', 280);
        if (c.requested_kind === 'snapshot')
            validateCandidate(c, 'snapshot');
        else
            shortText(c.claim, 'claim', snapshotCandidate(c) ? SNAP_MAX_BYTES : c.requested_kind === 'archive' ? 65000 : 2000, true);
        check(['conversation', 'workspace', 'manual', 'import'].includes(c.captured_from), 'candidate_invalid', 'Invalid provenance.');
        check(c.requested_kind === null || typeof c.requested_kind === 'string', 'candidate_invalid', 'Invalid requested kind.');
        const specialized = stringList(c.specialized_kinds, 'specialized_kinds', 0, 2, 80);
        check(new Set(specialized).size === specialized.length && [null, 'observation', 'snapshot'].includes(c.fallback_kind), 'candidate_invalid', 'Invalid routing fields.');
        stringList(c.evidence ?? [], 'evidence', 0, 2, 240);
        check(object(c.owner_inputs), 'candidate_invalid', 'Owner inputs are missing.');
        const relevant = new Set([...specialized, c.requested_kind, c.fallback_kind]);
        for (const [kind, input] of Object.entries(c.owner_inputs))
            check(relevant.has(kind) && (kind === 'snapshot' && snapshotCandidate(c) || Buffer.byteLength(canonicalJson(input)) <= (kind === 'archive' ? 512 * 1024 : 8192)), 'candidate_invalid', 'Unrouted or oversized owner input.');
    }
    return candidates;
}
export function draftOwnerResult(candidate: Candidate, attestation: Attestation, options: DraftOptions = {}): ObjectValue {
    const draft = draftCapture(candidate, attestation, options), kind = options.targetKind ?? candidate.requested_kind as Kind, capability = contracts.capabilities[kind], effect = `effect_create_${kind}`;
    return { schema: 'context-owner-result/v1', result_type: 'claim', transition: 'capture', owner: capability.owner, target_kind: kind, candidate_id: candidate.candidate_id, decision: 'claim', reason: 'caller-attested capture', capability_digest: canonicalDigest(capability), semantic_inputs: [{ operation: 'claim', input_schema: candidate.schema, input_digest: canonicalDigest(candidate), value: candidate }], semantic_attestations: [attestation], artifact_drafts: [{ effect_id: effect, path: draft.path, content: draft.content, semantic_projection: { kind, primary_claim: primaryClaim(draft.document), supporting_context: [] } }], effects: [{ effect_id: effect, action: 'create', area: kind, id: draft.document.frontmatter.id, state: 'current' }], proposed_plan: { schema: 'context-owner-plan/v1', transition: 'capture', operations: [{ op: 'create', effect_id: effect, area: kind, path: draft.path }] } };
}
export function declineOwnerResult(candidate: Candidate, kind: Kind, reason: string): ObjectValue {
    validateCandidateBatch([candidate]);
    const capability = contracts.capabilities[kind];
    check(capability, 'owner_unavailable', 'Unknown owner.');
    return { schema: 'context-owner-result/v1', result_type: 'claim', transition: 'capture', owner: capability.owner, target_kind: kind, candidate_id: candidate.candidate_id, decision: 'decline', reason: shortText(reason, 'reason', 500), capability_digest: canonicalDigest(capability), semantic_inputs: [{ operation: 'claim', input_schema: candidate.schema, input_digest: canonicalDigest(candidate), value: candidate }], semantic_attestations: [], artifact_drafts: [], effects: [], proposed_plan: null };
}
export function validateOwnerResult(result: ObjectValue): void {
    const kind = result.target_kind as Kind, capability = contracts.capabilities[kind];
    check(capability && result.schema === 'context-owner-result/v1' && result.result_type === 'claim' && result.owner === capability.owner && result.capability_digest === canonicalDigest(capability) && ['claim', 'decline', 'needs_clarification'].includes(result.decision), 'owner_result_invalid', 'Owner result does not match the capability.', {}, EXIT.conflict);
    const semantic = result.semantic_inputs?.find((x: ObjectValue) => x.operation === 'claim');
    check(semantic && semantic.input_schema === semantic.value?.schema && semantic.input_digest === canonicalDigest(semantic.value) && semantic.value.candidate_id === result.candidate_id, 'claim_result_mismatch', 'Owner result must bind its actual candidate.', {}, EXIT.conflict);
    validateCandidateBatch([semantic.value]);
    if (result.decision === 'claim') {
        if (kind === 'snapshot')
            validateCandidate(semantic.value, 'snapshot');
        const attestation = result.semantic_attestations?.find((x: ObjectValue) => x.operation === 'claim');
        validateAttestation(attestation, semantic.value, capability.claim_assertions, 'claim', kind);
        if (result.artifact_drafts?.length)
            operationFromOwnerResult(result, false);
        else
            check(result.effects?.length === 0, 'owner_result_invalid', 'A route-only claim cannot carry effects.');
    }
    else {
        shortText(result.reason, 'reason', 500);
        check(result.artifact_drafts?.length === 0 && result.effects?.length === 0, 'owner_result_invalid', 'Declined and clarification results cannot carry writes.');
    }
}
export function operationFromOwnerResult(result: ObjectValue, validate = true): CaptureOperation {
    if (validate)
        validateOwnerResult(result);
    check(result.decision === 'claim' && result.transition === 'capture' && result.artifact_drafts?.length === 1 && result.effects?.length === 1, 'owner_result_invalid', 'Expected one approved capture draft.');
    const draft = result.artifact_drafts[0], doc = parseDocument(draft.content), kind = result.target_kind as Kind, semantic = result.semantic_inputs.find((x: ObjectValue) => x.operation === 'claim'), attestation = result.semantic_attestations.find((x: ObjectValue) => x.operation === 'claim');
    const operation: CaptureOperation = { action: 'capture', targetKind: kind, candidate: semantic.value, attestation, id: doc.frontmatter.id, now: doc.frontmatter.created_at, filename: path.posix.basename(draft.path) };
    const expected = draftCapture(operation.candidate, operation.attestation, operation), effect = result.effects[0];
    check(expected.path === draft.path && expected.content === draft.content && effect.id === operation.id && effect.area === kind && effect.action === 'create' && effect.state === 'current' && effect.effect_id === draft.effect_id, 'owner_result_invalid', 'Draft content or effects differ from the attested input.', {}, EXIT.conflict);
    check(result.proposed_plan?.transition === 'capture' && result.proposed_plan?.operations?.length === 1 && result.proposed_plan.operations[0].path === draft.path && result.proposed_plan.operations[0].effect_id === draft.effect_id && result.proposed_plan.operations[0].op === 'create', 'owner_result_invalid', 'Proposed plan differs from its draft.');
    return operation;
}
/** Pure routing: callers collect semantic owner decisions; this never invokes agents. */
export function routeCandidates(batch: CandidateBatch | Candidate[], claimResults: ObjectValue[] | ObjectValue, enabled: string[] | null = null): ObjectValue {
    const candidates = validateCandidateBatch(batch), results = Array.isArray(claimResults) ? claimResults : claimResults.results;
    check(Array.isArray(results), 'owner_result_invalid', 'Expected owner results.');
    const byKey = new Map<string, ObjectValue>();
    for (const result of results) {
        const key = result.candidate_id + ':' + result.target_kind;
        check(!byKey.has(key), 'owner_conflict', 'Duplicate result for candidate and kind.', {}, EXIT.conflict);
        byKey.set(key, result);
    }
    const available = (kind: string) => contracts.capabilities[kind] && (!enabled || enabled.includes(kind));
    const routes = candidates.map(c => {
        const emit = (status: string, reason: string, fields: ObjectValue = {}) => ({ candidate_id: c.candidate_id, status, reason, ...fields });
        const ordered = c.requested_kind ? [c.requested_kind] : c.specialized_kinds;
        if (enabled && ordered.length && !ordered.some((k: string) => enabled.includes(k)))
            return emit('skipped', 'feature_disabled');
        const offered = ordered.filter(available);
        if (c.requested_kind && !offered.length)
            return emit('owner_unavailable', 'requested_owner_unavailable');
        const collect = (kind: string) => { const r = byKey.get(c.candidate_id + ':' + kind); if (!r)
            return; validateOwnerResult(r); check(r.semantic_inputs.find((x: ObjectValue) => x.operation === 'claim').input_digest === canonicalDigest(c), 'claim_result_mismatch', 'Collected result differs from the exact candidate.', {}, EXIT.conflict); return r; };
        const evaluated = offered.map(collect).filter(Boolean) as ObjectValue[], clarification = evaluated.find(r => r.decision === 'needs_clarification'), claims = evaluated.filter(r => r.decision === 'claim');
        if (clarification)
            return emit('needs_clarification', clarification.reason, { owner: clarification.owner, target_kind: clarification.target_kind });
        if (claims.length > 1)
            return emit('owner_conflict', 'multiple_specialized_owners_claimed');
        const proposed = (r: ObjectValue, reason: string) => emit('proposed', reason, { owner: r.owner, target_kind: r.target_kind, authority: contracts.capabilities[r.target_kind].authority, owner_result_digest: canonicalDigest(r) });
        if (claims.length === 1)
            return proposed(claims[0], c.requested_kind ? 'requested_owner' : claims[0].target_kind === c.fallback_kind ? 'fallback_owner' : 'specialized_owner');
        if (c.requested_kind)
            return emit('skipped', evaluated.some(r => r.decision === 'decline') ? 'owner_decline' : 'owner_unavailable');
        if (offered.length && evaluated.length !== offered.length)
            return emit('owner_unavailable', 'specialized_owner_result_missing');
        if (c.fallback_kind && available(c.fallback_kind)) {
            const r = collect(c.fallback_kind);
            if (r?.decision === 'claim')
                return proposed(r, 'fallback_owner');
        }
        return emit('skipped', 'no_owner_claim');
    });
    return { schema: 'context-route-result/v1', routes, conflicts: routes.filter(r => r.status === 'owner_conflict'), skipped: routes.filter(r => r.status === 'skipped'), canonical_bytes: Buffer.byteLength(canonicalJson(batch)), router_owner_process_invocations: 0, cache_probe_count: 0, alternate_runtime_count: 0 };
}
