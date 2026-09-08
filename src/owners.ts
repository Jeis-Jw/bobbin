import { randomUUID } from 'node:crypto';
import { ObjectValue, contracts, check, object, shortText, stringList, canonicalJson, canonicalDigest, canonicalScope, canonicalKey, newId, timestamp, naturalFilename, filename, requireId, date, nfc, EXIT } from './common';
import { Kind, kinds, ContextDocument, renderDocument, parseDocument, sectionValue } from './documents';
export interface OwnerInputs {
    snapshot: {
        current_context: string;
        open_items: string[];
        next_steps: string[];
        decided?: string[];
        refs?: string[];
        capture_candidates?: string[];
        anchors?: string[];
    };
    observation: {
        observation: string;
        evidence: string[];
        impact?: string;
        current_handling?: string;
        followup_conditions?: string[];
    };
    archive: {
        content: string;
    };
    decision: {
        decision: string;
        rationale: string;
        rejected_alternatives: string[];
        decision_key: string;
        constraints?: string[];
        tradeoffs?: string[];
        revisit_when?: string[];
        revisit_on?: string;
        serves_intents?: string[];
        informed_by_observations?: string[];
        informed_by_assumptions?: string[];
        affects_documents?: string[];
    };
    assumption: {
        assumption: string;
        basis: string[];
        unverified_ok: true;
        confirm_conditions?: string[];
        refute_conditions?: string[];
        impacted_decisions?: string[];
    };
    term: {
        term: string;
        definition: string;
        project_signal: 'project-specific' | 'project-special-meaning';
        aliases?: string[];
        deprecated_terms?: string[];
        related?: string[];
    };
    intent: {
        intent: string;
        intent_key: string;
        success_criteria?: string[];
        constraints?: string[];
        revisit_conditions?: string[];
    };
    document: {
        content: string;
        document_key: string;
    };
}
export interface CaptureInput<K extends Kind = Kind> {
    kind: K;
    title: string;
    summary: string;
    ownerInputs: OwnerInputs[K];
    scope?: string;
    evidence?: string[];
    capturedFrom?: 'conversation' | 'workspace' | 'manual' | 'import';
    sourceRefs?: string[];
    tags?: string[];
    searchTerms?: string[];
    kindHint?: 'decision';
    candidateId?: string;
}
export interface Assertion {
    name: string;
    value: true;
    evidence_pointers: string[];
}
export interface Attestation {
    schema: 'context-semantic-attestation/v1';
    operation: string;
    input_schema: string;
    input_digest: string;
    assertions: Assertion[];
}
export type Candidate = ObjectValue;
export const primaryFields: Record<Kind, string> = { snapshot: 'current_context', observation: 'observation', archive: 'content', decision: 'decision', assumption: 'assumption', term: 'definition', intent: 'intent', document: 'content' };
export const sectionFields: Record<Kind, Record<string, string>> = {
    snapshot: { current_context: 'Current context', open_items: 'Open items', next_steps: 'Next steps', decided: 'Decided', refs: 'References', capture_candidates: 'Capture candidates' },
    observation: { observation: 'Observation', evidence: 'Evidence', impact: 'Impact', current_handling: 'Current handling', followup_conditions: 'Follow-up conditions' },
    archive: { content: 'Content' }, decision: { decision: 'Decision', rationale: 'Rationale', rejected_alternatives: 'Rejected alternatives', constraints: 'Evidence and constraints', tradeoffs: 'Trade-offs', revisit_when: 'Revisit conditions' },
    assumption: { assumption: 'Assumption', basis: 'Basis', confirm_conditions: 'Confirmation conditions', refute_conditions: 'Refutation conditions' },
    term: { definition: 'Definition' }, intent: { intent: 'Intent', success_criteria: 'Success criteria', constraints: 'Constraints', revisit_conditions: 'Revisit conditions' }, document: { content: 'Content' }
};
export const relationFields: Record<string, string> = { serves_intents: 'serves:intent', informed_by_observations: 'informed_by:observation', informed_by_assumptions: 'informed_by:assumption', affects_documents: 'affects:document' };
export const assertionPointers: Record<Kind, Record<string, string[]>> = {
    snapshot: { handoff_requested: ['/owner_inputs/snapshot/current_context'], unfinished_context_present: ['/owner_inputs/snapshot/open_items/0'] },
    observation: { reusable_observation: ['/owner_inputs/observation/observation'], evidence_present: ['/owner_inputs/observation/evidence/0'] },
    archive: { source_adopted_as_evidence: ['/source_refs/0'], immutable_original_present: ['/owner_inputs/archive/content'] },
    decision: { explicit_choice: ['/owner_inputs/decision/decision'], scope_identified: ['/scope_hint'], commitment_present: ['/evidence/0'] },
    assumption: { assumption_present: ['/owner_inputs/assumption/assumption'], unverified_ok: ['/owner_inputs/assumption/unverified_ok'] },
    term: { term_identified: ['/owner_inputs/term/term'], definition_present: ['/owner_inputs/term/definition'] },
    intent: { intent_present: ['/owner_inputs/intent/intent'], desired_direction: ['/owner_inputs/intent/intent'] },
    document: { content_present: ['/owner_inputs/document/content'], living_document: ['/owner_inputs/document/document_key', '/owner_inputs/document/content'] }
};
export function validateOwnerInputs(kind: Kind, value: unknown): ObjectValue {
    check(kinds.includes(kind) && object(value), 'candidate_invalid', 'Unknown record kind or missing owner inputs.');
    const fields = contracts.capabilities[kind].draft_fields, specs = { ...fields.required, ...fields.optional, ...(kind === 'assumption' ? { unverified_ok: { type: 'boolean' } } : {}), ...(kind === 'term' ? { project_signal: { type: 'string', max_chars: 80 } } : {}) }, result: ObjectValue = {};
    if (kind === 'assumption')
        check(value.unverified_ok === true, 'candidate_invalid', 'An assumption must explicitly remain unverified.', {}, EXIT.conflict);
    if (kind === 'term')
        check(['project-specific', 'project-special-meaning'].includes(value.project_signal), 'owner_decline', 'Generic dictionary meaning is outside TERM authority.', {}, EXIT.conflict);
    check(Object.keys(value).every(k => Object.hasOwn(specs, k)) && Object.keys(fields.required).every(k => Object.hasOwn(value, k)), 'candidate_invalid', 'Owner input fields are incomplete or undeclared.');
    for (const [key, raw] of Object.entries(value)) {
        const spec = specs[key];
        if (spec.type === 'string')
            result[key] = shortText(raw, key, spec.max_chars, true);
        else if (spec.type === 'string_list') {
            result[key] = stringList(raw, key, spec.min_items ?? 0, spec.max_items, spec.max_item_chars);
            if (spec.format === 'context_id') {
                result[key].forEach((v: string) => requireId(v, key));
                check(new Set(result[key]).size === result[key].length, 'candidate_invalid', 'Context IDs must be unique.');
            }
        }
        else if (spec.type === 'date')
            result[key] = date(raw as string);
        else if (spec.type === 'boolean')
            result[key] = raw;
    }
    for (const key of ['decision_key', 'intent_key', 'document_key'])
        if (result[key])
            result[key] = canonicalKey(result[key]);
    const maximum = kind === 'archive' ? 512 * 1024 : 8192;
    check(Buffer.byteLength(canonicalJson(result)) <= maximum, 'owner_input_too_large', 'Owner input exceeds its byte budget.', { kind, maximum }, EXIT.conflict);
    return result;
}
export function createCandidate<K extends Kind>(input: CaptureInput<K>): Candidate {
    const values = validateOwnerInputs(input.kind, input.ownerInputs), kind = input.kind;
    const candidate: Candidate = {
        schema: 'context-capture-candidate/v1', candidate_id: input.candidateId ?? 'cand_' + randomUUID().replaceAll('-', ''),
        title: shortText(input.title, 'title', 120), claim: values[primaryFields[kind]], summary: shortText(input.summary, 'summary', 280), captured_from: input.capturedFrom ?? 'manual',
        requested_kind: kind, specialized_kinds: [kind], fallback_kind: null,
        source_refs: stringList(input.sourceRefs ?? [], 'source_refs'), tags: stringList(input.tags ?? [], 'tags', 0, 12, 40), search_terms: stringList(input.searchTerms ?? [], 'search_terms', 0, 12, 40), owner_inputs: { [kind]: values }
    };
    if (contracts.owners[kind]) {
        candidate.scope_hint = canonicalScope(input.scope!);
        if (kind === 'decision' || input.evidence?.length)
            candidate.evidence = stringList(input.evidence, 'evidence', kind === 'decision' ? 1 : 0, 2, 240);
    }
    if (input.kindHint !== undefined) {
        check(kind === 'observation' && input.kindHint === 'decision', 'candidate_invalid', 'kind_hint applies only to decision-like observations.');
        candidate.kind_hint = input.kindHint;
    }
    validateCandidate(candidate);
    return candidate;
}
export function validateCandidate(candidate: Candidate, targetKind?: Kind): {
    kind: Kind;
    values: ObjectValue;
} {
    check(object(candidate) && candidate.schema === 'context-capture-candidate/v1' && /^cand_[0-9a-f]{32}$/.test(candidate.candidate_id), 'candidate_invalid', 'Invalid capture candidate.');
    check(!['claim_key', 'claim_fingerprint', 'source_claim_fingerprint'].some(k => Object.hasOwn(candidate, k)), 'schema_removed_field', 'Semantic identity surrogate fields were removed.');
    const kind = targetKind ?? candidate.requested_kind as Kind;
    check(kinds.includes(kind) && Array.isArray(candidate.specialized_kinds) && (candidate.specialized_kinds.includes(kind) || candidate.fallback_kind === kind) && (!candidate.requested_kind || candidate.requested_kind === kind), 'candidate_invalid', 'Candidate is not routed to the selected owner.');
    const values = validateOwnerInputs(kind, candidate.owner_inputs?.[kind]);
    check(candidate.claim === values[primaryFields[kind]], 'candidate_invalid', 'Claim must match the actual primary body.');
    shortText(candidate.title, 'title', 120);
    shortText(candidate.summary, 'summary', 280);
    check(['conversation', 'workspace', 'manual', 'import'].includes(candidate.captured_from), 'candidate_invalid', 'Invalid captured_from.');
    for (const [key, max] of [['source_refs', 500], ['tags', 40], ['search_terms', 40]] as const)
        stringList(candidate[key] ?? [], key, 0, 12, max);
    if (contracts.owners[kind]) {
        canonicalScope(candidate.scope_hint);
        stringList(candidate.evidence ?? [], 'evidence', kind === 'decision' ? 1 : 0, 2, 240);
    }
    else if (candidate.requested_kind)
        check(candidate.specialized_kinds.length === 1 && candidate.fallback_kind === null && Object.keys(candidate.owner_inputs).length === 1, 'candidate_invalid', 'Direct builtin capture must name exactly one owner.');
    if (kind === 'archive')
        stringList(candidate.source_refs, 'source_refs', 1);
    check(Buffer.byteLength(canonicalJson(candidate)) <= (kind === 'archive' ? 512 * 1024 : 16384), 'candidate_too_large', 'Candidate exceeds its byte budget.', {}, EXIT.conflict);
    return { kind, values };
}
export function pointer(value: unknown, reference: string): unknown {
    check(typeof reference === 'string' && reference.startsWith('/') && !/~(?![01])/u.test(reference), 'semantic_attestation_invalid', 'Expected an RFC6901 pointer.');
    let current: any = value;
    for (const raw of reference.slice(1).split('/')) {
        const key = raw.replaceAll('~1', '/').replaceAll('~0', '~');
        check(current !== null && typeof current === 'object' && Object.hasOwn(current, key) && (!Array.isArray(current) || /^(0|[1-9]\d*)$/.test(key)), 'semantic_attestation_invalid', 'Evidence pointer is unresolved.', { pointer: reference }, EXIT.conflict);
        current = current[key];
    }
    check(current !== null && current !== '' && (!Array.isArray(current) || current.length > 0), 'semantic_attestation_invalid', 'Evidence pointer must reference non-empty evidence.', {}, EXIT.conflict);
    return current;
}
/** The caller supplies assertions after examining meaning; this does not infer approval. */
export function createAttestation(input: ObjectValue, assertions: Assertion[], operation = 'claim'): Attestation {
    return { schema: 'context-semantic-attestation/v1', operation, input_schema: input.schema, input_digest: canonicalDigest(input), assertions: structuredClone(assertions) };
}
export function validateAttestation(attestation: Attestation, input: ObjectValue, required: string[], operation = 'claim', targetKind?: Kind): void {
    check(object(attestation) && attestation.schema === 'context-semantic-attestation/v1' && attestation.operation === operation && attestation.input_schema === input.schema && attestation.input_digest === canonicalDigest(input), 'semantic_attestation_invalid', 'Attestation is not bound to the exact semantic input.', {}, EXIT.conflict);
    check(Array.isArray(attestation.assertions) && attestation.assertions.length === required.length && new Set(attestation.assertions.map(x => x.name)).size === required.length && required.every(name => attestation.assertions.some(x => x.name === name)), 'semantic_attestation_invalid', 'Attestation assertions differ from the owner capability.', {}, EXIT.conflict);
    for (const assertion of attestation.assertions) {
        check(assertion.value === true && Array.isArray(assertion.evidence_pointers) && assertion.evidence_pointers.length >= 1 && assertion.evidence_pointers.length <= 4, 'semantic_attestation_invalid', 'Invalid semantic assertion.', {}, EXIT.conflict);
        assertion.evidence_pointers.forEach(p => pointer(input, p));
        if (operation === 'claim') {
            const kind = targetKind ?? input.requested_kind as Kind, expected = assertionPointers[kind]?.[assertion.name];
            if (['assumption', 'term', 'intent', 'document'].includes(kind))
                check(compactPointers(assertion.evidence_pointers) === compactPointers(expected), 'semantic_attestation_invalid', 'Assertion points to the wrong actual evidence.', { assertion: assertion.name }, EXIT.conflict);
            if (kind === 'decision' && expected)
                check(assertion.evidence_pointers.some(p => assertion.name === 'commitment_present' ? /^\/evidence\/(0|1)$/.test(p) : expected.includes(p)), 'semantic_attestation_invalid', 'Decision assertion points to the wrong evidence.', { assertion: assertion.name }, EXIT.conflict);
        }
    }
    if (operation === 'same_claim') {
        const pointers = attestation.assertions.flatMap(x => x.evidence_pointers);
        check(pointers.includes('/predecessor/primary_claim') && pointers.includes('/successor/primary_claim'), 'semantic_attestation_invalid', 'same_claim must cite both actual claims.', {}, EXIT.conflict);
    }
}
const compactPointers = (value: unknown) => JSON.stringify(value);
export interface DraftOptions {
    id?: string;
    now?: string;
    filename?: string;
    targetKind?: Kind;
}
export function draftCapture(candidate: Candidate, attestation: Attestation, options: DraftOptions = {}): {
    path: string;
    content: string;
    document: ContextDocument;
} {
    const { kind, values } = validateCandidate(candidate, options.targetKind), capability = contracts.capabilities[kind];
    validateAttestation(attestation, candidate, capability.claim_assertions, 'claim', kind);
    const fm: ObjectValue = { schema: capability.artifact_schema, id: options.id ?? newId(), title: candidate.title, summary: candidate.summary, created_at: timestamp(options.now), captured_from: candidate.captured_from };
    for (const key of ['source_refs', 'tags', 'search_terms'])
        if (candidate[key]?.length)
            fm[key] = candidate[key];
    if (contracts.owners[kind])
        fm.scope = canonicalScope(candidate.scope_hint);
    for (const key of ['decision_key', 'intent_key', 'document_key', 'term', 'aliases', 'deprecated_terms', 'related', 'impacted_decisions', 'revisit_when', 'revisit_on', 'anchors'])
        if (values[key] !== undefined && (!Array.isArray(values[key]) || values[key].length))
            fm[key] = values[key];
    if (kind === 'term')
        fm.term_key = canonicalKey(values.term);
    if (kind === 'snapshot')
        fm.updated_at = fm.created_at;
    if (kind === 'observation' && candidate.kind_hint)
        fm.kind_hint = candidate.kind_hint;
    const relations: ObjectValue = {};
    for (const [field, predicate] of Object.entries(relationFields))
        if (values[field]?.length)
            relations[predicate] = values[field];
    if (Object.keys(relations).length)
        fm.relations = relations;
    const sections: Record<string, string> = {};
    for (const [field, name] of Object.entries(sectionFields[kind]))
        if (values[field] !== undefined && (!Array.isArray(values[field]) || values[field].length))
            sections[name] = Array.isArray(values[field]) ? values[field].map((v: string) => '- ' + v).join('\n') : values[field];
    const content = renderDocument(fm, sections);
    return { path: `context/${kind}/${options.filename ? filename(options.filename) : naturalFilename(fm.title)}`, content, document: parseDocument(content) };
}
export function primaryClaim(document: ContextDocument): string {
    const kind = document.frontmatter.schema.slice(8, -3) as Kind;
    return sectionValue(document, sectionFields[kind]?.[primaryFields[kind]] ?? Object.keys(document.sections)[0]);
}
