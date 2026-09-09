import { ObjectValue, contracts, normalizedKey, codepoints, shortText, check, canonicalScope, canonicalKey, scopesOverlap, canonicalDigest, canonicalJson, sha256, compareText, EXIT } from './common';
import { parseAreaIndex, parseDocument, sectionValue } from './documents';
import { registeredAreas, Area } from './catalog';
import { readText } from './filesystem';
const STOP = new Set(`a an the and or but if then else when while of to in on at by for with from into onto over under about as is are was were be been being have has had do does did not no nor so than that this these those it its we our us you your they their them he she his her also only just very more most less least such same other another any some all each every both either neither own per via because until unless whether which who whom whose what where why how here there now today again still yet already ever never should would could can may might must shall will use used using make made keep keeps kept take taken get got put set let`.split(' '));
const HANGUL_STOP = new Set('것 수 등 및 또는 그리고 하다 있다 없다 한다 된다'.split(' '));
const JOSA = '에서 에게 으로 부터 까지 처럼 보다 이나 든지 라도 은 는 이 가 을 를 의 에 로 과 와'.split(' ');
const SUFFIXES = 'ations ation ments ment ities ity ings ing ness ies ers ely ly ed es al s'.split(' ');
const hasHangul = (word: string) => /[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7a3\ud7b0-\ud7ff]/u.test(word);
export function canonicalTerms(text: string): string[] {
    const out: string[] = [];
    for (const token of normalizedKey(text).match(/[\p{L}\p{N}]+/gu) ?? []) {
        const hangul = hasHangul(token), minimum = hangul ? 2 : 4;
        if (codepoints(token) < minimum || codepoints(token) > 40 || /^\p{Nd}+$/u.test(token) || STOP.has(token))
            continue;
        let word = token;
        if (hangul) {
            for (const suffix of JOSA)
                if (token.endsWith(suffix)) {
                    const candidate = token.slice(0, -suffix.length);
                    if (codepoints(candidate) >= 2 || HANGUL_STOP.has(candidate)) {
                        word = candidate;
                        break;
                    }
                }
        }
        else {
            for (let i = 0; i < 2; i++)
                for (const suffix of SUFFIXES)
                    if (word.endsWith(suffix) && codepoints(word) - suffix.length >= 4) {
                        word = word.slice(0, -suffix.length);
                        break;
                    }
            if (/^[\x00-\x7f]+$/.test(word) && word.length > 4) {
                if (word.endsWith('e') && !word.endsWith('ee'))
                    word = word.slice(0, -1);
                else if (word.endsWith('y') && !/[aeiou]/.test(word.at(-2)!))
                    word = word.slice(0, -1);
            }
        }
        if (codepoints(word) >= (hasHangul(word) ? 2 : 3) && codepoints(word) <= 40 && !(hangul && HANGUL_STOP.has(word)) && !out.includes(word))
            out.push(word);
    }
    return out;
}
export function deriveSearchTerms(title: string, summary: string, values: ObjectValue, maximum = 12): string[] {
    const existing = new Set(canonicalTerms(title + ' ' + summary)), out: string[] = [];
    for (const part of [values.decision ?? '', ...(values.rejected_alternatives ?? []), values.rationale ?? '', ...(values.revisit_when ?? [])])
        for (const word of canonicalTerms(part)) {
            if (existing.has(word) || out.includes(word))
                continue;
            out.push(word);
            if (out.length >= maximum)
                return out;
        }
    return out;
}
const intersect = (a: Set<string>, b: Set<string>) => new Set([...a].filter(x => b.has(x)));
const CORE_SECTIONS = ['Decision', 'Rationale', 'Rejected alternatives'];
export interface DecisionCheckOptions {
    statement: string;
    scope?: string;
    decisionKey?: string;
    rationale?: string;
    query?: string;
    limit?: number;
    knownCurrent?: { id: string; sha256: string }[];
}
export interface DecisionCompareOptions extends DecisionCheckOptions {
    scope: string;
    decisionKey: string;
}
export const DECISION_ASSESSMENT_CONTRACT = 'context-decision-assessment/v1';
function decisionArea(root: string): Area { const area = registeredAreas(root).find(a => a.row.area === 'decision'); check(area, 'area_not_registered', 'Decision owner is not initialized.', {}, EXIT.notFound); return area; }
function selectDecisionCandidates(rows: ObjectValue[], options: { statement: string; rationale: string; query: string; exact: boolean; scope: string | null; key: string | null; limit: number; mode: 'legacy' | 'scoped' }) {
    const { statement, rationale, query, exact, scope, key, limit, mode } = options;
    const tokens = new Set(canonicalTerms([statement, rationale, query].filter(Boolean).join(' '))), metadata = rows.map(r => new Set(canonicalTerms([r.title, r.summary, r.decision_key, ...r.terms].join(' ')))), titles = rows.map(r => new Set(canonicalTerms(r.title))), keys = rows.map(r => new Set(canonicalTerms(r.decision_key))), frequency = new Map([...tokens].map(t => [t, metadata.filter(m => m.has(t)).length]));
    const distinctive = new Set([...tokens].filter(t => frequency.get(t)! > 0 && frequency.get(t)! <= Math.max(1, Math.floor((rows.length + 3) / 4)))), weights = new Map([...distinctive].map(t => { const weight = Math.log1p(rows.length / frequency.get(t)!); return [t, exact ? weight : Math.max(1, Math.floor(weight))]; }));
    const mandatory = new Set<string>();
    const ranked = rows.map((row, index) => {
        let score = 0, lexical = 0;
        const reasons: string[] = [];
        if (exact) {
            if (row.scope === scope && row.decision_key === key) {
                score += 100;
                reasons.push('exact_slot');
                mandatory.add(row.id);
            }
            else if (row.decision_key === key && scopesOverlap(row.scope, scope!)) {
                score += 80;
                reasons.push('scope_overlap');
                mandatory.add(row.id);
            }
            else if (mode === 'legacy' && row.decision_key === key) {
                score += 40;
                reasons.push('same_decision_key');
            }
            else if (mode === 'legacy' && row.scope === scope) {
                score += 20;
                reasons.push('exact_scope');
            }
            else if (mode === 'legacy' && scopesOverlap(row.scope, scope!)) {
                score += 20;
                reasons.push('related_scope');
            }
        }
        const hits = [...intersect(distinctive, metadata[index])].sort(compareText), heading = hits.filter(t => titles[index].has(t) || keys[index].has(t));
        if (hits.length && (score > 0 || hits.length >= 2 || heading.length || hits.some(t => frequency.get(t) === 1))) {
            lexical = hits.reduce((sum, t) => sum + weights.get(t)! * (1 + Number(titles[index].has(t)) + Number(keys[index].has(t))), 0);
            score += 8 * lexical / (1 + lexical);
            reasons.push('lexical:' + hits.slice(0, 4).join(','));
        }
        return { row, index, score, lexical, reasons, hits: new Set(hits), metadata: metadata[index] };
    }).sort((a, b) => b.score - a.score || compareText(a.row.path, b.row.path) || compareText(a.row.id, b.row.id));
    check(mandatory.size <= limit, 'comparison_too_broad', 'Exact-slot and scope-overlap decisions exceed the check limit.', { required: mandatory.size, limit }, EXIT.conflict);
    const eligible = ranked.filter(x => x.score > 0);
    let selected: typeof eligible = [];
    if (!exact && eligible.length) {
        selected = eligible.slice(0, Math.ceil(limit / 2));
        const remaining = eligible.slice(selected.length);
        while (remaining.length && selected.length < limit) {
            const priority = (item: typeof eligible[number]) => { const similarity = Math.max(...selected.map(s => intersect(item.hits, s.hits).size / Math.max(1, new Set([...item.hits, ...s.hits]).size))), overlap = Math.max(...selected.map(s => intersect(item.metadata, s.metadata).size / Math.max(1, s.metadata.size))); return [-item.lexical * (1 - .8 * similarity), overlap]; };
            remaining.sort((a, b) => { const aa = priority(a), bb = priority(b); return aa[0] - bb[0] || aa[1] - bb[1] || compareText(a.row.path, b.row.path) || compareText(a.row.id, b.row.id); });
            selected.push(remaining.shift()!);
        }
    }
    else {
        selected = eligible.filter(x => mandatory.has(x.row.id));
        for (const item of eligible) {
            if (selected.length >= limit)
                break;
            if (!mandatory.has(item.row.id))
                selected.push(item);
        }
    }
    return { selected, ranked, mandatory, eligible };
}
function readDecisionBodies(root: string, area: Area, selected: { row: ObjectValue; reasons: string[] }[], mandatory: Set<string>, proposal: ObjectValue) {
    const current: ObjectValue[] = [], currentLinks: ObjectValue[] = [];
    let bodyReads = 0;
    for (const item of selected) {
        bodyReads++;
        const content = readText(root, item.row.path), doc = parseDocument(content, area.descriptor), fm = doc.frontmatter, sections: ObjectValue = Object.fromEntries(CORE_SECTIONS.map(k => [k, sectionValue(doc, k)])), revisit = sectionValue(doc, 'Revisit conditions');
        if (revisit)
            sections['Revisit conditions'] = revisit;
        const record = { id: fm.id, path: item.row.path, sha256: sha256(Buffer.from(content)), title: fm.title, summary: fm.summary, scope: fm.scope, decision_key: fm.decision_key, sections, retrieval_reasons: item.reasons };
        if (Buffer.byteLength(canonicalJson({ schema: 'context-decision-comparison-input/v1', proposal, current: [...current, record] })) > 24576) {
            check(!mandatory.has(fm.id), 'comparison_too_large', 'Mandatory actual bodies exceed the check byte limit.', { id: fm.id, max_bytes: 24576 }, EXIT.conflict);
            continue;
        }
        current.push(record);
        currentLinks.push({ id: fm.id, state: 'current', supersedes: fm.supersedes ?? [] });
    }
    return { current, currentLinks, bodyReads };
}
function prepareDecisionEvidence(root: string, options: DecisionCheckOptions, mode: 'legacy' | 'scoped') {
    if (mode === 'scoped')
        check(typeof options.scope === 'string' && typeof options.decisionKey === 'string', 'usage_invalid', 'Compare requires both scope and decisionKey.');
    const limit = options.limit ?? 8, statement = shortText(options.statement, 'statement', 1200, true), rationale = options.rationale?.trim() ?? '', query = options.query?.trim() ?? '';
    check(limit >= 1 && limit <= 12, 'usage_invalid', 'Check limit must be in 1..12.');
    check((options.scope === undefined) === (options.decisionKey === undefined), 'usage_invalid', 'Provide both scope and decisionKey, or neither.');
    const known = new Map<string, string>();
    if (options.knownCurrent !== undefined) {
        check(Array.isArray(options.knownCurrent) && options.knownCurrent.length <= 12, 'usage_invalid', 'knownCurrent must be an array of at most 12 {id, sha256} entries.');
        for (const hint of options.knownCurrent) {
            check(hint && !Array.isArray(hint) && Object.keys(hint).length === 2 && Object.hasOwn(hint, 'id') && Object.hasOwn(hint, 'sha256') && typeof hint.id === 'string' && /^ctx_[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$/.test(hint.id) && typeof hint.sha256 === 'string' && /^(?:sha256:)?[0-9a-f]{64}$/.test(hint.sha256), 'usage_invalid', 'Each knownCurrent entry needs a canonical record ID and lowercase file SHA-256 (optional sha256: prefix); CLI: --known-current ID:SHA256.');
            check(!known.has(hint.id), 'usage_invalid', 'knownCurrent IDs must be unique; provide each --known-current ID:SHA256 only once.');
            known.set(hint.id, hint.sha256.startsWith('sha256:') ? hint.sha256 : 'sha256:' + hint.sha256);
        }
    }
    if (rationale)
        shortText(rationale, 'rationale', 1200, true);
    if (query)
        shortText(query, 'query', 280, true);
    const exact = options.scope !== undefined, scope = exact ? canonicalScope(options.scope!) : null, key = exact ? canonicalKey(options.decisionKey!) : null, area = decisionArea(root), allRows = parseAreaIndex(area.text).current;
    const rows = mode === 'scoped' ? allRows.filter(r => scopesOverlap(r.scope, scope!)) : allRows;
    const { selected, ranked, mandatory, eligible } = selectDecisionCandidates(rows, { statement, rationale, query, exact, scope, key, limit, mode });
    const proposal = { statement, rationale: rationale || null, scope, decision_key: key, query: query || null };
    const { current, currentLinks, bodyReads } = readDecisionBodies(root, area, selected, mandatory, proposal);
    const comparison = { schema: 'context-decision-comparison-input/v1', proposal, current }, extract = (items: ObjectValue[]) => items.map(x => ({ id: x.id, path: x.path, sha256: x.sha256 })), selectedIds = new Set(current.map(x => x.id)), omitted = rows.length - current.length, sample = ranked.filter(x => !selectedIds.has(x.row.id)).slice(0, 8).map(x => x.row.id);
    return { comparison, currentLinks, known, exact,
        deterministic: { exact_slot: extract(current.filter(x => exact && x.scope === scope && x.decision_key === key)), scope_overlap: extract(current.filter(x => exact && x.decision_key === key && x.scope !== scope && scopesOverlap(x.scope, scope!))) },
        retrieval: { total_current: allRows.length, metadata_matches: eligible.length, body_reads: current.length, selected_semantic_bytes: Buffer.byteLength(canonicalJson(current)), index_sha256: sha256(Buffer.from(area.text)), returned: current.length, omitted, omitted_id_sample: sample, omitted_id_sample_truncated: omitted > sample.length, full_current_set: omitted === 0, bounded: true },
        scopedCurrent: rows.length, bodyReads,
    };
}
export function decisionAssessmentContract(withRevisit = true): ObjectValue {
    const assessment: ObjectValue = { relations: ['new', 'same', 'supporting', 'rationale_changed', 'conflict'], required_fields: ['relation', 'related_ids', 'reason'], actions: contracts.owners.decision.relation_actions, rule: 'Judge returned actual sections, not hashes or similarity. For rationale_changed/conflict, quote every returned non-empty actual section, hold action, and ask one explicit binary question: keep = not performed; supersede only after explicit choice. Revisit permits reassessment, not implementation. The explicit choice settles that decision payload and authorizes capture without a second storage question.' };
    if (withRevisit)
        assessment.conflict_revisit = { required_when: 'relation=conflict and a related Current DEC has non-empty Revisit conditions', required_fields: ['revisit_conditions', 'revisit_assessment'], classifications: ['satisfied', 'no evidence', 'ambiguous'], rule: 'Surface each relevant stored Revisit condition from comparison_input.sections and state the selected classification token verbatim in the user response. Do not invent evidence. Use satisfied only when user-supplied present facts directly establish it; the requested conflicting action itself is not evidence. Use no evidence when facts are absent or concern something other than the stored condition. Use ambiguous only when user-supplied condition facts are relevant but incomplete or conflicting.' };
    return assessment;
}
function comparisonTransport(comparison: ObjectValue, known: Map<string, string>): ObjectValue {
    return { schema: 'context-decision-comparison-delta/v1', proposal: comparison.proposal, current: comparison.current.map((record: ObjectValue) => {
        if (known.get(record.id) !== record.sha256)
            return record;
        const { sections, ...metadata } = record;
        return { ...metadata, sections_ref: { id: record.id, sha256: record.sha256 } };
    }) };
}
const comparisonWarning = 'relation=new is valid only within the queried Current set and does not prove global absence of conflict.';
export function prepareDecisionCheck(root: string, options: DecisionCheckOptions): ObjectValue {
    const { comparison, currentLinks, known, exact, deterministic, retrieval } = prepareDecisionEvidence(root, options, 'legacy');
    const assessment = decisionAssessmentContract(comparison.current.some(x => x.sections['Revisit conditions']));
    const result: ObjectValue = { schema: 'context-decision-check/v1', coverage: exact ? 'exact_slot' : 'discovery_only', comparison_input: comparison, input_digest: canonicalDigest(comparison), deterministic, assessment_contract: assessment, retrieval, warning: comparisonWarning, physical_write: false };
    result.current_links = currentLinks;
    if (!exact)
        result.caveat = 'no-conflict cannot be concluded; re-run with exact scope/decision_key before preview';
    check(Buffer.byteLength(canonicalJson(result)) <= 32768, 'comparison_too_large', 'Decision check exceeds its output byte limit.', {}, EXIT.conflict);
    if (options.knownCurrent !== undefined) {
        // Reuse only changes transport after fresh full bodies pass every existing gate.
        const delta = comparisonTransport(comparison, known);
        result.schema = 'context-decision-check-delta/v1';
        result.comparison_delta = delta;
        result.hydrated_input_digest = result.input_digest;
        result.transport_digest = canonicalDigest(delta);
        delete result.comparison_input;
        delete result.input_digest;
        assessment.rule = 'Resolve every sections_ref from the complete actual sections still in caller context; if unavailable, repeat without knownCurrent/--known-current before judging. ' + assessment.rule.replaceAll('returned', 'returned or retained');
        if (assessment.conflict_revisit)
            assessment.conflict_revisit.rule = assessment.conflict_revisit.rule.replace('comparison_input.sections', 'the hydrated comparison sections');
        check(Buffer.byteLength(canonicalJson(result)) <= 32768, 'comparison_too_large', 'Decision check delta exceeds its output byte limit.', {}, EXIT.conflict);
    }
    return result;
}
export function prepareDecisionCompare(root: string, options: DecisionCompareOptions): ObjectValue {
    const evidence = prepareDecisionEvidence(root, options, 'scoped');
    const { comparison, currentLinks, known, deterministic, scopedCurrent } = evidence;
    const transport = comparisonTransport(comparison, known);
    // The budget is checked against full semantic evidence before any references are substituted.
    const { full_current_set, ...retrieval } = evidence.retrieval;
    const warnings = [comparisonWarning];
    if (retrieval.omitted)
        warnings.push('Some scoped Current records were omitted; inspect metadata or explicitly read relevant IDs if the evidence is insufficient.');
    const result: ObjectValue = {
        schema: 'context-decision-compare/v1', coverage: 'exact_slot',
        assessment_contract_ref: DECISION_ASSESSMENT_CONTRACT,
        comparison: transport, deterministic, current_links: currentLinks,
        retrieval: { ...retrieval, body_reads: evidence.bodyReads, scoped_current: scopedCurrent, outside_scope: retrieval.total_current - scopedCurrent, full_scoped_set: full_current_set },
        hydrated_input_digest: canonicalDigest(comparison), transport_digest: canonicalDigest(transport),
        warnings, physical_write: false,
    };
    check(Buffer.byteLength(canonicalJson(result)) <= 32768, 'comparison_too_large', 'Decision compare exceeds its output byte limit.', {}, EXIT.conflict);
    return result;
}
export function decisionSpecView(root: string, scope: string, maxBytes = 32768): ObjectValue {
    check(maxBytes >= 512 && maxBytes <= 32768, 'usage_invalid', 'spec-view maxBytes must be in 512..32768.');
    scope = canonicalScope(scope);
    const area = decisionArea(root), rows = parseAreaIndex(area.text).current, matched = rows.filter(r => scopesOverlap(scope, r.scope)).sort((a, b) => compareText(a.created_at, b.created_at) || compareText(a.id, b.id)), items: ObjectValue[] = [];
    let bodyReads = 0;
    const result = () => ({ schema: 'context-decision-spec-view/v1', scope, items, returned: items.length, omitted_count: matched.length - items.length, truncated: matched.length > items.length, max_bytes: maxBytes, retrieval: { index_sha256: sha256(Buffer.from(area.text)), total_current: rows.length, metadata_matches: matched.length, body_reads: bodyReads, history_body_reads: 0 }, projection: 'ephemeral', physical_write: false });
    const size = () => Buffer.byteLength(JSON.stringify({ ok: true, result: result() }) + '\n');
    check(size() <= maxBytes, 'output_too_large', 'spec-view metadata exceeds the byte budget.', {}, EXIT.conflict);
    for (const row of matched) {
        const doc = parseDocument(readText(root, row.path), area.descriptor), fm = doc.frontmatter;
        bodyReads++;
        items.push({ id: fm.id, path: row.path, created_at: fm.created_at, scope: fm.scope, decision_key: fm.decision_key, sections: { Decision: sectionValue(doc, 'Decision'), Rationale: sectionValue(doc, 'Rationale') } });
        if (size() > maxBytes) {
            items.pop();
            while (items.length && size() > maxBytes)
                items.pop();
            break;
        }
    }
    check(size() <= maxBytes, 'output_too_large', 'spec-view output exceeds the byte budget.', {}, EXIT.conflict);
    return result();
}
