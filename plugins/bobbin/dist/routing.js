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
Object.defineProperty(exports, "__esModule", { value: true });
exports.validateCandidateBatch = validateCandidateBatch;
exports.draftOwnerResult = draftOwnerResult;
exports.declineOwnerResult = declineOwnerResult;
exports.validateOwnerResult = validateOwnerResult;
exports.operationFromOwnerResult = operationFromOwnerResult;
exports.routeCandidates = routeCandidates;
const path = __importStar(require("node:path"));
const common_1 = require("./common");
const documents_1 = require("./documents");
const owners_1 = require("./owners");
function validateCandidateBatch(batch) {
    if (!Array.isArray(batch)) {
        (0, common_1.exact)(batch, ['schema', 'audit_count', 'candidates'], 'candidate_invalid');
        (0, common_1.check)(batch.schema === 'context-capture-batch/v1' && batch.audit_count === 1, 'audit_repeated', 'A semantic milestone is audited once.');
    }
    const candidates = Array.isArray(batch) ? batch : batch.candidates;
    (0, common_1.check)(Array.isArray(candidates) && candidates.length <= 8, 'candidate_batch_too_large', 'At most eight candidates are supported.');
    const maximum = candidates.length === 1 && candidates[0].requested_kind === 'archive' ? 512 * 1024 : 16384;
    (0, common_1.check)(Buffer.byteLength((0, common_1.canonicalJson)(batch)) <= maximum, 'candidate_batch_too_large', 'Candidate batch exceeds its byte budget.');
    const ids = new Set();
    for (const c of candidates) {
        (0, common_1.check)((0, common_1.object)(c) && c.schema === 'context-capture-candidate/v1' && /^cand_[0-9a-f]{32}$/.test(c.candidate_id) && !ids.has(c.candidate_id), 'candidate_invalid', 'Invalid or duplicated candidate ID.');
        ids.add(c.candidate_id);
        (0, common_1.check)(!['claim_key', 'claim_fingerprint', 'source_claim_fingerprint'].some(k => Object.hasOwn(c, k)), 'schema_removed_field', 'Semantic identity surrogates are not permitted.');
        (0, common_1.shortText)(c.title, 'title', 120);
        (0, common_1.shortText)(c.summary, 'summary', 280);
        (0, common_1.shortText)(c.claim, 'claim', c.requested_kind === 'archive' ? 65000 : 2000, true);
        (0, common_1.check)(['conversation', 'workspace', 'manual', 'import'].includes(c.captured_from), 'candidate_invalid', 'Invalid provenance.');
        (0, common_1.check)(c.requested_kind === null || typeof c.requested_kind === 'string', 'candidate_invalid', 'Invalid requested kind.');
        const specialized = (0, common_1.stringList)(c.specialized_kinds, 'specialized_kinds', 0, 2, 80);
        (0, common_1.check)(new Set(specialized).size === specialized.length && [null, 'observation', 'snapshot'].includes(c.fallback_kind), 'candidate_invalid', 'Invalid routing fields.');
        (0, common_1.stringList)(c.evidence ?? [], 'evidence', 0, 2, 240);
        (0, common_1.check)((0, common_1.object)(c.owner_inputs), 'candidate_invalid', 'Owner inputs are missing.');
        const relevant = new Set([...specialized, c.requested_kind, c.fallback_kind]);
        for (const [kind, input] of Object.entries(c.owner_inputs))
            (0, common_1.check)(relevant.has(kind) && Buffer.byteLength((0, common_1.canonicalJson)(input)) <= (kind === 'archive' ? 512 * 1024 : 8192), 'candidate_invalid', 'Unrouted or oversized owner input.');
    }
    return candidates;
}
function draftOwnerResult(candidate, attestation, options = {}) {
    const draft = (0, owners_1.draftCapture)(candidate, attestation, options), kind = options.targetKind ?? candidate.requested_kind, capability = common_1.contracts.capabilities[kind], effect = `effect_create_${kind}`;
    return { schema: 'context-owner-result/v1', result_type: 'claim', transition: 'capture', owner: capability.owner, target_kind: kind, candidate_id: candidate.candidate_id, decision: 'claim', reason: 'caller-attested capture', capability_digest: (0, common_1.canonicalDigest)(capability), semantic_inputs: [{ operation: 'claim', input_schema: candidate.schema, input_digest: (0, common_1.canonicalDigest)(candidate), value: candidate }], semantic_attestations: [attestation], artifact_drafts: [{ effect_id: effect, path: draft.path, content: draft.content, semantic_projection: { kind, primary_claim: (0, owners_1.primaryClaim)(draft.document), supporting_context: [] } }], effects: [{ effect_id: effect, action: 'create', area: kind, id: draft.document.frontmatter.id, state: 'current' }], proposed_plan: { schema: 'context-owner-plan/v1', transition: 'capture', operations: [{ op: 'create', effect_id: effect, area: kind, path: draft.path }] } };
}
function declineOwnerResult(candidate, kind, reason) {
    validateCandidateBatch([candidate]);
    const capability = common_1.contracts.capabilities[kind];
    (0, common_1.check)(capability, 'owner_unavailable', 'Unknown owner.');
    return { schema: 'context-owner-result/v1', result_type: 'claim', transition: 'capture', owner: capability.owner, target_kind: kind, candidate_id: candidate.candidate_id, decision: 'decline', reason: (0, common_1.shortText)(reason, 'reason', 500), capability_digest: (0, common_1.canonicalDigest)(capability), semantic_inputs: [{ operation: 'claim', input_schema: candidate.schema, input_digest: (0, common_1.canonicalDigest)(candidate), value: candidate }], semantic_attestations: [], artifact_drafts: [], effects: [], proposed_plan: null };
}
function validateOwnerResult(result) {
    const kind = result.target_kind, capability = common_1.contracts.capabilities[kind];
    (0, common_1.check)(capability && result.schema === 'context-owner-result/v1' && result.result_type === 'claim' && result.owner === capability.owner && result.capability_digest === (0, common_1.canonicalDigest)(capability) && ['claim', 'decline', 'needs_clarification'].includes(result.decision), 'owner_result_invalid', 'Owner result does not match the capability.', {}, common_1.EXIT.conflict);
    const semantic = result.semantic_inputs?.find((x) => x.operation === 'claim');
    (0, common_1.check)(semantic && semantic.input_schema === semantic.value?.schema && semantic.input_digest === (0, common_1.canonicalDigest)(semantic.value) && semantic.value.candidate_id === result.candidate_id, 'claim_result_mismatch', 'Owner result must bind its actual candidate.', {}, common_1.EXIT.conflict);
    validateCandidateBatch([semantic.value]);
    if (result.decision === 'claim') {
        const attestation = result.semantic_attestations?.find((x) => x.operation === 'claim');
        (0, owners_1.validateAttestation)(attestation, semantic.value, capability.claim_assertions, 'claim', kind);
        if (result.artifact_drafts?.length)
            operationFromOwnerResult(result, false);
        else
            (0, common_1.check)(result.effects?.length === 0, 'owner_result_invalid', 'A route-only claim cannot carry effects.');
    }
    else {
        (0, common_1.shortText)(result.reason, 'reason', 500);
        (0, common_1.check)(result.artifact_drafts?.length === 0 && result.effects?.length === 0, 'owner_result_invalid', 'Declined and clarification results cannot carry writes.');
    }
}
function operationFromOwnerResult(result, validate = true) {
    if (validate)
        validateOwnerResult(result);
    (0, common_1.check)(result.decision === 'claim' && result.transition === 'capture' && result.artifact_drafts?.length === 1 && result.effects?.length === 1, 'owner_result_invalid', 'Expected one approved capture draft.');
    const draft = result.artifact_drafts[0], doc = (0, documents_1.parseDocument)(draft.content), kind = result.target_kind, semantic = result.semantic_inputs.find((x) => x.operation === 'claim'), attestation = result.semantic_attestations.find((x) => x.operation === 'claim');
    const operation = { action: 'capture', targetKind: kind, candidate: semantic.value, attestation, id: doc.frontmatter.id, now: doc.frontmatter.created_at, filename: path.posix.basename(draft.path) };
    const expected = (0, owners_1.draftCapture)(operation.candidate, operation.attestation, operation), effect = result.effects[0];
    (0, common_1.check)(expected.path === draft.path && expected.content === draft.content && effect.id === operation.id && effect.area === kind && effect.action === 'create' && effect.state === 'current' && effect.effect_id === draft.effect_id, 'owner_result_invalid', 'Draft content or effects differ from the attested input.', {}, common_1.EXIT.conflict);
    (0, common_1.check)(result.proposed_plan?.transition === 'capture' && result.proposed_plan?.operations?.length === 1 && result.proposed_plan.operations[0].path === draft.path && result.proposed_plan.operations[0].effect_id === draft.effect_id && result.proposed_plan.operations[0].op === 'create', 'owner_result_invalid', 'Proposed plan differs from its draft.');
    return operation;
}
/** Pure routing: callers collect semantic owner decisions; this never invokes agents. */
function routeCandidates(batch, claimResults, enabled = null) {
    const candidates = validateCandidateBatch(batch), results = Array.isArray(claimResults) ? claimResults : claimResults.results;
    (0, common_1.check)(Array.isArray(results), 'owner_result_invalid', 'Expected owner results.');
    const byKey = new Map();
    for (const result of results) {
        const key = result.candidate_id + ':' + result.target_kind;
        (0, common_1.check)(!byKey.has(key), 'owner_conflict', 'Duplicate result for candidate and kind.', {}, common_1.EXIT.conflict);
        byKey.set(key, result);
    }
    const available = (kind) => common_1.contracts.capabilities[kind] && (!enabled || enabled.includes(kind));
    const routes = candidates.map(c => {
        const emit = (status, reason, fields = {}) => ({ candidate_id: c.candidate_id, status, reason, ...fields });
        const ordered = c.requested_kind ? [c.requested_kind] : c.specialized_kinds;
        if (enabled && ordered.length && !ordered.some((k) => enabled.includes(k)))
            return emit('skipped', 'feature_disabled');
        const offered = ordered.filter(available);
        if (c.requested_kind && !offered.length)
            return emit('owner_unavailable', 'requested_owner_unavailable');
        const collect = (kind) => {
            const r = byKey.get(c.candidate_id + ':' + kind);
            if (!r)
                return;
            validateOwnerResult(r);
            (0, common_1.check)(r.semantic_inputs.find((x) => x.operation === 'claim').input_digest === (0, common_1.canonicalDigest)(c), 'claim_result_mismatch', 'Collected result differs from the exact candidate.', {}, common_1.EXIT.conflict);
            return r;
        };
        const evaluated = offered.map(collect).filter(Boolean), clarification = evaluated.find(r => r.decision === 'needs_clarification'), claims = evaluated.filter(r => r.decision === 'claim');
        if (clarification)
            return emit('needs_clarification', clarification.reason, { owner: clarification.owner, target_kind: clarification.target_kind });
        if (claims.length > 1)
            return emit('owner_conflict', 'multiple_specialized_owners_claimed');
        const proposed = (r, reason) => emit('proposed', reason, { owner: r.owner, target_kind: r.target_kind, authority: common_1.contracts.capabilities[r.target_kind].authority, owner_result_digest: (0, common_1.canonicalDigest)(r) });
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
    return { schema: 'context-route-result/v1', routes, conflicts: routes.filter(r => r.status === 'owner_conflict'), skipped: routes.filter(r => r.status === 'skipped'), canonical_bytes: Buffer.byteLength((0, common_1.canonicalJson)(batch)), router_owner_process_invocations: 0, cache_probe_count: 0, alternate_runtime_count: 0 };
}
