"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.assertionPointers = exports.relationFields = exports.sectionFields = exports.primaryFields = void 0;
exports.validateOwnerInputs = validateOwnerInputs;
exports.createCandidate = createCandidate;
exports.validateCandidate = validateCandidate;
exports.pointer = pointer;
exports.createAttestation = createAttestation;
exports.validateAttestation = validateAttestation;
exports.draftCapture = draftCapture;
exports.primaryClaim = primaryClaim;
const node_crypto_1 = require("node:crypto");
const common_1 = require("./common");
const documents_1 = require("./documents");
exports.primaryFields = { snapshot: 'current_context', observation: 'observation', archive: 'content', decision: 'decision', assumption: 'assumption', term: 'definition', intent: 'intent', document: 'content' };
exports.sectionFields = {
    snapshot: { current_context: 'Current context', open_items: 'Open items', next_steps: 'Next steps', decided: 'Decided', refs: 'References', capture_candidates: 'Capture candidates' },
    observation: { observation: 'Observation', evidence: 'Evidence', impact: 'Impact', current_handling: 'Current handling', followup_conditions: 'Follow-up conditions' },
    archive: { content: 'Content' }, decision: { decision: 'Decision', rationale: 'Rationale', rejected_alternatives: 'Rejected alternatives', constraints: 'Evidence and constraints', tradeoffs: 'Trade-offs', revisit_when: 'Revisit conditions' },
    assumption: { assumption: 'Assumption', basis: 'Basis', confirm_conditions: 'Confirmation conditions', refute_conditions: 'Refutation conditions' },
    term: { definition: 'Definition' }, intent: { intent: 'Intent', success_criteria: 'Success criteria', constraints: 'Constraints', revisit_conditions: 'Revisit conditions' }, document: { content: 'Content' }
};
exports.relationFields = { serves_intents: 'serves:intent', informed_by_observations: 'informed_by:observation', informed_by_assumptions: 'informed_by:assumption', affects_documents: 'affects:document' };
exports.assertionPointers = {
    snapshot: { handoff_requested: ['/owner_inputs/snapshot/current_context'], unfinished_context_present: ['/owner_inputs/snapshot/open_items/0'] },
    observation: { reusable_observation: ['/owner_inputs/observation/observation'], evidence_present: ['/owner_inputs/observation/evidence/0'] },
    archive: { source_adopted_as_evidence: ['/source_refs/0'], immutable_original_present: ['/owner_inputs/archive/content'] },
    decision: { explicit_choice: ['/owner_inputs/decision/decision'], scope_identified: ['/scope_hint'], commitment_present: ['/evidence/0'] },
    assumption: { assumption_present: ['/owner_inputs/assumption/assumption'], unverified_ok: ['/owner_inputs/assumption/unverified_ok'] },
    term: { term_identified: ['/owner_inputs/term/term'], definition_present: ['/owner_inputs/term/definition'] },
    intent: { intent_present: ['/owner_inputs/intent/intent'], desired_direction: ['/owner_inputs/intent/intent'] },
    document: { content_present: ['/owner_inputs/document/content'], living_document: ['/owner_inputs/document/document_key', '/owner_inputs/document/content'] }
};
function validateOwnerInputs(kind, value) {
    (0, common_1.check)(documents_1.kinds.includes(kind) && (0, common_1.object)(value), 'candidate_invalid', 'Unknown record kind or missing owner inputs.');
    const fields = common_1.contracts.capabilities[kind].draft_fields, specs = { ...fields.required, ...fields.optional, ...(kind === 'assumption' ? { unverified_ok: { type: 'boolean' } } : {}), ...(kind === 'term' ? { project_signal: { type: 'string', max_chars: 80 } } : {}) }, result = {};
    if (kind === 'assumption')
        (0, common_1.check)(value.unverified_ok === true, 'candidate_invalid', 'An assumption must explicitly remain unverified.', {}, common_1.EXIT.conflict);
    if (kind === 'term')
        (0, common_1.check)(['project-specific', 'project-special-meaning'].includes(value.project_signal), 'owner_decline', 'Generic dictionary meaning is outside TERM authority.', {}, common_1.EXIT.conflict);
    (0, common_1.check)(Object.keys(value).every(k => Object.hasOwn(specs, k)) && Object.keys(fields.required).every(k => Object.hasOwn(value, k)), 'candidate_invalid', 'Owner input fields are incomplete or undeclared.');
    for (const [key, raw] of Object.entries(value)) {
        const spec = specs[key];
        if (spec.type === 'string')
            result[key] = (0, common_1.shortText)(raw, key, spec.max_chars, true);
        else if (spec.type === 'string_list') {
            result[key] = (0, common_1.stringList)(raw, key, spec.min_items ?? 0, spec.max_items, spec.max_item_chars);
            if (spec.format === 'context_id') {
                result[key].forEach((v) => (0, common_1.requireId)(v, key));
                (0, common_1.check)(new Set(result[key]).size === result[key].length, 'candidate_invalid', 'Context IDs must be unique.');
            }
        }
        else if (spec.type === 'date')
            result[key] = (0, common_1.date)(raw);
        else if (spec.type === 'boolean')
            result[key] = raw;
    }
    for (const key of ['decision_key', 'intent_key', 'document_key'])
        if (result[key])
            result[key] = (0, common_1.canonicalKey)(result[key]);
    const maximum = kind === 'archive' ? 512 * 1024 : 8192;
    (0, common_1.check)(Buffer.byteLength((0, common_1.canonicalJson)(result)) <= maximum, 'owner_input_too_large', 'Owner input exceeds its byte budget.', { kind, maximum }, common_1.EXIT.conflict);
    return result;
}
function createCandidate(input) {
    const values = validateOwnerInputs(input.kind, input.ownerInputs), kind = input.kind;
    const candidate = {
        schema: 'context-capture-candidate/v1', candidate_id: input.candidateId ?? 'cand_' + (0, node_crypto_1.randomUUID)().replaceAll('-', ''),
        title: (0, common_1.shortText)(input.title, 'title', 120), claim: values[exports.primaryFields[kind]], summary: (0, common_1.shortText)(input.summary, 'summary', 280), captured_from: input.capturedFrom ?? 'manual',
        requested_kind: kind, specialized_kinds: [kind], fallback_kind: null,
        source_refs: (0, common_1.stringList)(input.sourceRefs ?? [], 'source_refs'), tags: (0, common_1.stringList)(input.tags ?? [], 'tags', 0, 12, 40), search_terms: (0, common_1.stringList)(input.searchTerms ?? [], 'search_terms', 0, 12, 40), owner_inputs: { [kind]: values }
    };
    if (common_1.contracts.owners[kind]) {
        candidate.scope_hint = (0, common_1.canonicalScope)(input.scope);
        if (kind === 'decision' || input.evidence?.length)
            candidate.evidence = (0, common_1.stringList)(input.evidence, 'evidence', kind === 'decision' ? 1 : 0, 2, 240);
    }
    if (input.kindHint !== undefined) {
        (0, common_1.check)(kind === 'observation' && input.kindHint === 'decision', 'candidate_invalid', 'kind_hint applies only to decision-like observations.');
        candidate.kind_hint = input.kindHint;
    }
    validateCandidate(candidate);
    return candidate;
}
function validateCandidate(candidate, targetKind) {
    (0, common_1.check)((0, common_1.object)(candidate) && candidate.schema === 'context-capture-candidate/v1' && /^cand_[0-9a-f]{32}$/.test(candidate.candidate_id), 'candidate_invalid', 'Invalid capture candidate.');
    (0, common_1.check)(!['claim_key', 'claim_fingerprint', 'source_claim_fingerprint'].some(k => Object.hasOwn(candidate, k)), 'schema_removed_field', 'Semantic identity surrogate fields were removed.');
    const kind = targetKind ?? candidate.requested_kind;
    (0, common_1.check)(documents_1.kinds.includes(kind) && Array.isArray(candidate.specialized_kinds) && (candidate.specialized_kinds.includes(kind) || candidate.fallback_kind === kind) && (!candidate.requested_kind || candidate.requested_kind === kind), 'candidate_invalid', 'Candidate is not routed to the selected owner.');
    const values = validateOwnerInputs(kind, candidate.owner_inputs?.[kind]);
    (0, common_1.check)(candidate.claim === values[exports.primaryFields[kind]], 'candidate_invalid', 'Claim must match the actual primary body.');
    (0, common_1.shortText)(candidate.title, 'title', 120);
    (0, common_1.shortText)(candidate.summary, 'summary', 280);
    (0, common_1.check)(['conversation', 'workspace', 'manual', 'import'].includes(candidate.captured_from), 'candidate_invalid', 'Invalid captured_from.');
    for (const [key, max] of [['source_refs', 500], ['tags', 40], ['search_terms', 40]])
        (0, common_1.stringList)(candidate[key] ?? [], key, 0, 12, max);
    if (common_1.contracts.owners[kind]) {
        (0, common_1.canonicalScope)(candidate.scope_hint);
        (0, common_1.stringList)(candidate.evidence ?? [], 'evidence', kind === 'decision' ? 1 : 0, 2, 240);
    }
    else if (candidate.requested_kind)
        (0, common_1.check)(candidate.specialized_kinds.length === 1 && candidate.fallback_kind === null && Object.keys(candidate.owner_inputs).length === 1, 'candidate_invalid', 'Direct builtin capture must name exactly one owner.');
    if (kind === 'archive')
        (0, common_1.stringList)(candidate.source_refs, 'source_refs', 1);
    (0, common_1.check)(Buffer.byteLength((0, common_1.canonicalJson)(candidate)) <= (kind === 'archive' ? 512 * 1024 : 16384), 'candidate_too_large', 'Candidate exceeds its byte budget.', {}, common_1.EXIT.conflict);
    return { kind, values };
}
function pointer(value, reference) {
    (0, common_1.check)(typeof reference === 'string' && reference.startsWith('/') && !/~(?![01])/u.test(reference), 'semantic_attestation_invalid', 'Expected an RFC6901 pointer.');
    let current = value;
    for (const raw of reference.slice(1).split('/')) {
        const key = raw.replaceAll('~1', '/').replaceAll('~0', '~');
        (0, common_1.check)(current !== null && typeof current === 'object' && Object.hasOwn(current, key) && (!Array.isArray(current) || /^(0|[1-9]\d*)$/.test(key)), 'semantic_attestation_invalid', 'Evidence pointer is unresolved.', { pointer: reference }, common_1.EXIT.conflict);
        current = current[key];
    }
    (0, common_1.check)(current !== null && current !== '' && (!Array.isArray(current) || current.length > 0), 'semantic_attestation_invalid', 'Evidence pointer must reference non-empty evidence.', {}, common_1.EXIT.conflict);
    return current;
}
/** The caller supplies assertions after examining meaning; this does not infer approval. */
function createAttestation(input, assertions, operation = 'claim') {
    return { schema: 'context-semantic-attestation/v1', operation, input_schema: input.schema, input_digest: (0, common_1.canonicalDigest)(input), assertions: structuredClone(assertions) };
}
function validateAttestation(attestation, input, required, operation = 'claim', targetKind) {
    (0, common_1.check)((0, common_1.object)(attestation) && attestation.schema === 'context-semantic-attestation/v1' && attestation.operation === operation && attestation.input_schema === input.schema && attestation.input_digest === (0, common_1.canonicalDigest)(input), 'semantic_attestation_invalid', 'Attestation is not bound to the exact semantic input.', {}, common_1.EXIT.conflict);
    (0, common_1.check)(Array.isArray(attestation.assertions) && attestation.assertions.length === required.length && new Set(attestation.assertions.map(x => x.name)).size === required.length && required.every(name => attestation.assertions.some(x => x.name === name)), 'semantic_attestation_invalid', 'Attestation assertions differ from the owner capability.', {}, common_1.EXIT.conflict);
    for (const assertion of attestation.assertions) {
        (0, common_1.check)(assertion.value === true && Array.isArray(assertion.evidence_pointers) && assertion.evidence_pointers.length >= 1 && assertion.evidence_pointers.length <= 4, 'semantic_attestation_invalid', 'Invalid semantic assertion.', {}, common_1.EXIT.conflict);
        assertion.evidence_pointers.forEach(p => pointer(input, p));
        if (operation === 'claim') {
            const kind = targetKind ?? input.requested_kind, expected = exports.assertionPointers[kind]?.[assertion.name];
            if (['assumption', 'term', 'intent', 'document'].includes(kind))
                (0, common_1.check)(compactPointers(assertion.evidence_pointers) === compactPointers(expected), 'semantic_attestation_invalid', 'Assertion points to the wrong actual evidence.', { assertion: assertion.name }, common_1.EXIT.conflict);
            if (kind === 'decision' && expected)
                (0, common_1.check)(assertion.evidence_pointers.some(p => assertion.name === 'commitment_present' ? /^\/evidence\/(0|1)$/.test(p) : expected.includes(p)), 'semantic_attestation_invalid', 'Decision assertion points to the wrong evidence.', { assertion: assertion.name }, common_1.EXIT.conflict);
        }
    }
    if (operation === 'same_claim') {
        const pointers = attestation.assertions.flatMap(x => x.evidence_pointers);
        (0, common_1.check)(pointers.includes('/predecessor/primary_claim') && pointers.includes('/successor/primary_claim'), 'semantic_attestation_invalid', 'same_claim must cite both actual claims.', {}, common_1.EXIT.conflict);
    }
}
const compactPointers = (value) => JSON.stringify(value);
function draftCapture(candidate, attestation, options = {}) {
    const { kind, values } = validateCandidate(candidate, options.targetKind), capability = common_1.contracts.capabilities[kind];
    validateAttestation(attestation, candidate, capability.claim_assertions, 'claim', kind);
    const fm = { schema: capability.artifact_schema, id: options.id ?? (0, common_1.newId)(), title: candidate.title, summary: candidate.summary, created_at: (0, common_1.timestamp)(options.now), captured_from: candidate.captured_from };
    for (const key of ['source_refs', 'tags', 'search_terms'])
        if (candidate[key]?.length)
            fm[key] = candidate[key];
    if (common_1.contracts.owners[kind])
        fm.scope = (0, common_1.canonicalScope)(candidate.scope_hint);
    for (const key of ['decision_key', 'intent_key', 'document_key', 'term', 'aliases', 'deprecated_terms', 'related', 'impacted_decisions', 'revisit_when', 'revisit_on', 'anchors'])
        if (values[key] !== undefined && (!Array.isArray(values[key]) || values[key].length))
            fm[key] = values[key];
    if (kind === 'term')
        fm.term_key = (0, common_1.canonicalKey)(values.term);
    if (kind === 'snapshot')
        fm.updated_at = fm.created_at;
    if (kind === 'observation' && candidate.kind_hint)
        fm.kind_hint = candidate.kind_hint;
    const relations = {};
    for (const [field, predicate] of Object.entries(exports.relationFields))
        if (values[field]?.length)
            relations[predicate] = values[field];
    if (Object.keys(relations).length)
        fm.relations = relations;
    const sections = {};
    for (const [field, name] of Object.entries(exports.sectionFields[kind]))
        if (values[field] !== undefined && (!Array.isArray(values[field]) || values[field].length))
            sections[name] = Array.isArray(values[field]) ? values[field].map((v) => '- ' + v).join('\n') : values[field];
    const content = (0, documents_1.renderDocument)(fm, sections);
    return { path: `context/${kind}/${options.filename ? (0, common_1.filename)(options.filename) : (0, common_1.naturalFilename)(fm.title)}`, content, document: (0, documents_1.parseDocument)(content) };
}
function primaryClaim(document) {
    const kind = document.frontmatter.schema.slice(8, -3);
    return (0, documents_1.sectionValue)(document, exports.sectionFields[kind]?.[exports.primaryFields[kind]] ?? Object.keys(document.sections)[0]);
}
