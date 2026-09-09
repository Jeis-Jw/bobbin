import * as fs from 'node:fs';
import * as path from 'node:path';
import { ObjectValue, strictJson, check, fail, BobbinError, EXIT } from './common';
import { bytes, utf8 } from './filesystem';
import { Kind } from './documents';
import { createCandidate, createAttestation, assertionPointers, Attestation, Candidate } from './owners';
import { deriveSearchTerms } from './decision';
import { SNAP_TRANSPORT_MAX_BYTES, snapshotText, snapshotList } from './snapshot';
export const list = (value: any): any[] => value === undefined ? [] : Array.isArray(value) ? value : [value];
export function loadJson(value: string): any {
    check(typeof value === 'string', 'usage_invalid', 'Missing JSON input.');
    const text = value === '-' ? utf8(fs.readFileSync(0)) : value.startsWith('@') ? loadFile(value.slice(1), 2 * 1024 * 1024) : value;
    check(Buffer.byteLength(text) <= 2 * 1024 * 1024, 'input_too_large', 'JSON input exceeds the byte budget.');
    return strictJson(text);
}
export function loadFile(value: string, maximum = 8192): string {
    const absolute = path.resolve(value), parent = path.dirname(absolute), raw = bytes(parent, path.basename(absolute), maximum);
    check(raw, 'input_not_found', 'Input file does not exist.', { path: absolute }, EXIT.notFound);
    return utf8(raw);
}
export const loadBody = (value: string, maximum = 8192): string => value.startsWith('@@') ? value.slice(1) : value.startsWith('@') ? loadFile(value.slice(1), maximum) : value;
export const bodyItems = (value: any): string[] => list(value).flatMap((x: string) => loadBody(x).split(/\r?\n/).map(v => v.replace(/^\s*-\s?/, '').trim()).filter(Boolean));
function snapshotBody(value: string): string {
    if (value.startsWith('@') && !value.startsWith('@@')) {
        const stat = fs.lstatSync(path.resolve(value.slice(1)), { throwIfNoEntry: false });
        if (stat?.isFile())
            check(stat.size <= SNAP_TRANSPORT_MAX_BYTES, 'input_too_large', `SNAP transport input is ${stat.size} bytes; maximum is ${SNAP_TRANSPORT_MAX_BYTES} bytes.`, { actual_bytes: stat.size, max_bytes: SNAP_TRANSPORT_MAX_BYTES });
    }
    const text = value === '-' ? utf8(fs.readFileSync(0)) : loadBody(value, SNAP_TRANSPORT_MAX_BYTES);
    const actual_bytes = Buffer.byteLength(text);
    check(actual_bytes <= SNAP_TRANSPORT_MAX_BYTES, 'input_too_large', `SNAP transport input is ${actual_bytes} bytes; maximum is ${SNAP_TRANSPORT_MAX_BYTES} bytes.`, { actual_bytes, max_bytes: SNAP_TRANSPORT_MAX_BYTES });
    return snapshotText(text, 'SNAP content');
}
function snapshotItems(value: any): string[] {
    return list(value).flatMap((item: string) => {
        const text = snapshotBody(item);
        // The existing one-item-per-line syntax remains available; JSON arrays
        // carry multiline items without destroying Markdown indentation.
        let parsed: unknown;
        if (text.trimStart().startsWith('[')) {
            try { parsed = strictJson(text); }
            catch (error) { if (!(error instanceof BobbinError)) throw error; }
        }
        return Array.isArray(parsed) ? snapshotList(parsed, 'SNAP items') : text.split('\n').map(line => line.replace(/^\s*-\s?/, '').trim()).filter(Boolean);
    });
}
const mappings: Record<Kind, Record<string, string>> = {
    snapshot: { 'sec-context': 'current_context', 'sec-open-items': 'open_items', 'sec-next-steps': 'next_steps', 'sec-decided': 'decided', 'sec-refs': 'refs', 'sec-capture-candidates': 'capture_candidates', 'sec-candidates': 'capture_candidates', 'anchor': 'anchors' },
    observation: { 'sec-observation': 'observation', 'sec-evidence': 'evidence', 'sec-impact': 'impact', 'sec-handling': 'current_handling', 'sec-followup': 'followup_conditions' }, archive: { 'sec-content': 'content', content: 'content' },
    decision: { 'sec-decision': 'decision', 'sec-rationale': 'rationale', 'sec-alternatives': 'rejected_alternatives', 'decision-key': 'decision_key', 'sec-constraints': 'constraints', 'sec-tradeoffs': 'tradeoffs', 'sec-revisit': 'revisit_when', 'revisit-on': 'revisit_on', 'serves-intent': 'serves_intents', 'informed-by-observation': 'informed_by_observations', 'informed-by-assumption': 'informed_by_assumptions', 'affects-document': 'affects_documents' },
    assumption: { 'sec-assumption': 'assumption', 'sec-basis': 'basis', 'sec-confirm': 'confirm_conditions', 'sec-refute': 'refute_conditions', 'impacted-decision': 'impacted_decisions' },
    term: { term: 'term', 'sec-definition': 'definition', 'project-signal': 'project_signal', alias: 'aliases', 'deprecated-term': 'deprecated_terms', 'related-term': 'related' },
    intent: { 'sec-intent': 'intent', 'intent-key': 'intent_key', 'sec-success-criterion': 'success_criteria', 'sec-constraint': 'constraints', 'sec-revisit': 'revisit_conditions' }, document: { 'document-key': 'document_key', 'sec-content': 'content' }
};
const listFields = new Set('open_items next_steps decided refs capture_candidates anchors evidence followup_conditions rejected_alternatives constraints tradeoffs revisit_when serves_intents informed_by_observations informed_by_assumptions affects_documents basis confirm_conditions refute_conditions impacted_decisions aliases deprecated_terms related success_criteria revisit_conditions'.split(' '));
export function inlineInputs(kind: Kind, flags: ObjectValue): ObjectValue {
    const values: ObjectValue = {};
    for (const [flag, key] of Object.entries(mappings[kind]))
        if (flags[flag] !== undefined)
            values[key] = kind === 'snapshot' && key !== 'anchors' ? listFields.has(key) ? snapshotItems(flags[flag]) : snapshotBody(flags[flag]) : listFields.has(key) ? bodyItems(flags[flag]) : loadBody(flags[flag], kind === 'archive' ? 260000 : 8192);
    if (kind === 'assumption')
        values.unverified_ok = flags['attest-unverified-ok'] === true;
    if (kind === 'term')
        values.project_signal ??= 'project-special-meaning';
    return values;
}
export function captureArguments(kind: Kind, flags: ObjectValue, prepareOnly = false): {
    candidate: Candidate;
    attestation: Attestation;
} {
    if (flags.candidate) {
        check(!flags.inline, 'usage_invalid', 'Candidate file and inline flags cannot be mixed.');
        return { candidate: loadJson(flags.candidate), attestation: loadJson(flags.attestation) };
    }
    const values = inlineInputs(kind, flags), searchTerms = bodyItems(flags['search-term']);
    if (kind === 'decision' && !searchTerms.length)
        searchTerms.push(...deriveSearchTerms(flags.title ?? '', flags.summary ?? '', values));
    const candidate = createCandidate({ kind, title: flags.title, summary: flags.summary, ownerInputs: values as any, scope: flags.scope, evidence: bodyItems(flags['commitment-evidence'] ?? flags['evidence']), capturedFrom: flags['captured-from'] ?? 'conversation', sourceRefs: bodyItems(flags['source-ref']), tags: bodyItems(flags.tag), searchTerms, kindHint: flags['kind-hint'], candidateId: flags['candidate-id'] });
    if (prepareOnly)
        return { candidate, attestation: undefined as any };
    if (flags.attestation)
        return { candidate, attestation: loadJson(flags.attestation) };
    const aliases: Record<string, string> = { source_adopted_as_evidence: 'attest-source-adopted', immutable_original_present: 'attest-immutable-original' };
    const assertions = Object.entries(assertionPointers[kind]).map(([name, evidence_pointers]) => { const flag = aliases[name] ?? 'attest-' + name.replaceAll('_', '-'); check(flags[flag] === true, 'usage_invalid', `Explicit semantic assertion --${flag} is required.`); return { name, value: true as const, evidence_pointers }; });
    // Non-decision owners historically carry no commitment-evidence CLI field; the
    // owner's explicit assertion is the user's statement, not an inferred fact.
    return { candidate, attestation: createAttestation(candidate, assertions) };
}
