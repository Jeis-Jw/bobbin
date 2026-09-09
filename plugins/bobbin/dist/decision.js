"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.canonicalTerms = canonicalTerms;
exports.deriveSearchTerms = deriveSearchTerms;
exports.prepareDecisionCheck = prepareDecisionCheck;
exports.decisionSpecView = decisionSpecView;
const common_1 = require("./common");
const documents_1 = require("./documents");
const catalog_1 = require("./catalog");
const filesystem_1 = require("./filesystem");
const STOP = new Set(`a an the and or but if then else when while of to in on at by for with from into onto over under about as is are was were be been being have has had do does did not no nor so than that this these those it its we our us you your they their them he she his her also only just very more most less least such same other another any some all each every both either neither own per via because until unless whether which who whom whose what where why how here there now today again still yet already ever never should would could can may might must shall will use used using make made keep keeps kept take taken get got put set let`.split(' '));
const HANGUL_STOP = new Set('것 수 등 및 또는 그리고 하다 있다 없다 한다 된다'.split(' '));
const JOSA = '에서 에게 으로 부터 까지 처럼 보다 이나 든지 라도 은 는 이 가 을 를 의 에 로 과 와'.split(' ');
const SUFFIXES = 'ations ation ments ment ities ity ings ing ness ies ers ely ly ed es al s'.split(' ');
const hasHangul = (word) => /[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7a3\ud7b0-\ud7ff]/u.test(word);
function canonicalTerms(text) {
    const out = [];
    for (const token of (0, common_1.normalizedKey)(text).match(/[\p{L}\p{N}]+/gu) ?? []) {
        const hangul = hasHangul(token), minimum = hangul ? 2 : 4;
        if ((0, common_1.codepoints)(token) < minimum || (0, common_1.codepoints)(token) > 40 || /^\p{Nd}+$/u.test(token) || STOP.has(token))
            continue;
        let word = token;
        if (hangul) {
            for (const suffix of JOSA)
                if (token.endsWith(suffix)) {
                    const candidate = token.slice(0, -suffix.length);
                    if ((0, common_1.codepoints)(candidate) >= 2 || HANGUL_STOP.has(candidate)) {
                        word = candidate;
                        break;
                    }
                }
        }
        else {
            for (let i = 0; i < 2; i++)
                for (const suffix of SUFFIXES)
                    if (word.endsWith(suffix) && (0, common_1.codepoints)(word) - suffix.length >= 4) {
                        word = word.slice(0, -suffix.length);
                        break;
                    }
            if (/^[\x00-\x7f]+$/.test(word) && word.length > 4) {
                if (word.endsWith('e') && !word.endsWith('ee'))
                    word = word.slice(0, -1);
                else if (word.endsWith('y') && !/[aeiou]/.test(word.at(-2)))
                    word = word.slice(0, -1);
            }
        }
        if ((0, common_1.codepoints)(word) >= (hasHangul(word) ? 2 : 3) && (0, common_1.codepoints)(word) <= 40 && !(hangul && HANGUL_STOP.has(word)) && !out.includes(word))
            out.push(word);
    }
    return out;
}
function deriveSearchTerms(title, summary, values, maximum = 12) {
    const existing = new Set(canonicalTerms(title + ' ' + summary)), out = [];
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
const intersect = (a, b) => new Set([...a].filter(x => b.has(x)));
const CORE_SECTIONS = ['Decision', 'Rationale', 'Rejected alternatives'];
function decisionArea(root) { const area = (0, catalog_1.registeredAreas)(root).find(a => a.row.area === 'decision'); (0, common_1.check)(area, 'area_not_registered', 'Decision owner is not initialized.', {}, common_1.EXIT.notFound); return area; }
function prepareDecisionCheck(root, options) {
    const limit = options.limit ?? 8, statement = (0, common_1.shortText)(options.statement, 'statement', 1200, true), rationale = options.rationale?.trim() ?? '', query = options.query?.trim() ?? '';
    (0, common_1.check)(limit >= 1 && limit <= 12, 'usage_invalid', 'Check limit must be in 1..12.');
    (0, common_1.check)((options.scope === undefined) === (options.decisionKey === undefined), 'usage_invalid', 'Provide both scope and decisionKey, or neither.');
    if (rationale)
        (0, common_1.shortText)(rationale, 'rationale', 1200, true);
    if (query)
        (0, common_1.shortText)(query, 'query', 280, true);
    const exact = options.scope !== undefined, scope = exact ? (0, common_1.canonicalScope)(options.scope) : null, key = exact ? (0, common_1.canonicalKey)(options.decisionKey) : null, area = decisionArea(root), rows = (0, documents_1.parseAreaIndex)(area.text).current;
    const tokens = new Set(canonicalTerms([statement, rationale, query].filter(Boolean).join(' '))), metadata = rows.map(r => new Set(canonicalTerms([r.title, r.summary, r.decision_key, ...r.terms].join(' ')))), titles = rows.map(r => new Set(canonicalTerms(r.title))), keys = rows.map(r => new Set(canonicalTerms(r.decision_key))), frequency = new Map([...tokens].map(t => [t, metadata.filter(m => m.has(t)).length]));
    const distinctive = new Set([...tokens].filter(t => frequency.get(t) > 0 && frequency.get(t) <= Math.max(1, Math.floor((rows.length + 3) / 4)))), weights = new Map([...distinctive].map(t => { const weight = Math.log1p(rows.length / frequency.get(t)); return [t, exact ? weight : Math.max(1, Math.floor(weight))]; }));
    const mandatory = new Set();
    const ranked = rows.map((row, index) => {
        let score = 0, lexical = 0;
        const reasons = [];
        if (exact) {
            if (row.scope === scope && row.decision_key === key) {
                score += 100;
                reasons.push('exact_slot');
                mandatory.add(row.id);
            }
            else if (row.decision_key === key && (0, common_1.scopesOverlap)(row.scope, scope)) {
                score += 80;
                reasons.push('scope_overlap');
                mandatory.add(row.id);
            }
            else if (row.decision_key === key) {
                score += 40;
                reasons.push('same_decision_key');
            }
            else if (row.scope === scope) {
                score += 20;
                reasons.push('exact_scope');
            }
            else if ((0, common_1.scopesOverlap)(row.scope, scope)) {
                score += 20;
                reasons.push('related_scope');
            }
        }
        const hits = [...intersect(distinctive, metadata[index])].sort(common_1.compareText), heading = hits.filter(t => titles[index].has(t) || keys[index].has(t));
        if (hits.length && (score > 0 || hits.length >= 2 || heading.length || hits.some(t => frequency.get(t) === 1))) {
            lexical = hits.reduce((sum, t) => sum + weights.get(t) * (1 + Number(titles[index].has(t)) + Number(keys[index].has(t))), 0);
            score += 8 * lexical / (1 + lexical);
            reasons.push('lexical:' + hits.slice(0, 4).join(','));
        }
        return { row, index, score, lexical, reasons, hits: new Set(hits), metadata: metadata[index] };
    }).sort((a, b) => b.score - a.score || (0, common_1.compareText)(a.row.path, b.row.path) || (0, common_1.compareText)(a.row.id, b.row.id));
    (0, common_1.check)(mandatory.size <= limit, 'comparison_too_broad', 'Exact-slot and scope-overlap decisions exceed the check limit.', { required: mandatory.size, limit }, common_1.EXIT.conflict);
    const eligible = ranked.filter(x => x.score > 0);
    let selected = [];
    if (!exact && eligible.length) {
        selected = eligible.slice(0, Math.ceil(limit / 2));
        const remaining = eligible.slice(selected.length);
        while (remaining.length && selected.length < limit) {
            const priority = (item) => { const similarity = Math.max(...selected.map(s => intersect(item.hits, s.hits).size / Math.max(1, new Set([...item.hits, ...s.hits]).size))), overlap = Math.max(...selected.map(s => intersect(item.metadata, s.metadata).size / Math.max(1, s.metadata.size))); return [-item.lexical * (1 - .8 * similarity), overlap]; };
            remaining.sort((a, b) => { const aa = priority(a), bb = priority(b); return aa[0] - bb[0] || aa[1] - bb[1] || (0, common_1.compareText)(a.row.path, b.row.path) || (0, common_1.compareText)(a.row.id, b.row.id); });
            selected.push(remaining.shift());
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
    const proposal = { statement, rationale: rationale || null, scope, decision_key: key, query: query || null }, current = [], currentLinks = [];
    for (const item of selected) {
        const content = (0, filesystem_1.readText)(root, item.row.path), doc = (0, documents_1.parseDocument)(content, area.descriptor), fm = doc.frontmatter, sections = Object.fromEntries(CORE_SECTIONS.map(k => [k, (0, documents_1.sectionValue)(doc, k)])), revisit = (0, documents_1.sectionValue)(doc, 'Revisit conditions');
        if (revisit)
            sections['Revisit conditions'] = revisit;
        const record = { id: fm.id, path: item.row.path, sha256: (0, common_1.sha256)(Buffer.from(content)), title: fm.title, summary: fm.summary, scope: fm.scope, decision_key: fm.decision_key, sections, retrieval_reasons: item.reasons };
        if (Buffer.byteLength((0, common_1.canonicalJson)({ schema: 'context-decision-comparison-input/v1', proposal, current: [...current, record] })) > 24576) {
            (0, common_1.check)(!mandatory.has(fm.id), 'comparison_too_large', 'Mandatory actual bodies exceed the check byte limit.', { id: fm.id, max_bytes: 24576 }, common_1.EXIT.conflict);
            continue;
        }
        current.push(record);
        currentLinks.push({ id: fm.id, state: 'current', supersedes: fm.supersedes ?? [] });
    }
    const comparison = { schema: 'context-decision-comparison-input/v1', proposal, current }, extract = (items) => items.map(x => ({ id: x.id, path: x.path, sha256: x.sha256 })), selectedIds = new Set(current.map(x => x.id)), omitted = rows.length - current.length, sample = ranked.filter(x => !selectedIds.has(x.row.id)).slice(0, 8).map(x => x.row.id);
    const assessment = { relations: ['new', 'same', 'supporting', 'rationale_changed', 'conflict'], required_fields: ['relation', 'related_ids', 'reason'], actions: common_1.contracts.owners.decision.relation_actions, rule: 'Judge returned actual sections, not hashes or similarity. For rationale_changed/conflict, quote every returned non-empty actual section, hold action, and ask one explicit binary question: keep = not performed; supersede only after explicit choice. Revisit permits reassessment, not implementation. The explicit choice settles that decision payload and authorizes capture without a second storage question.' };
    if (current.some(x => x.sections['Revisit conditions']))
        assessment.conflict_revisit = { required_when: 'relation=conflict and a related Current DEC has non-empty Revisit conditions', required_fields: ['revisit_conditions', 'revisit_assessment'], classifications: ['satisfied', 'no evidence', 'ambiguous'], rule: 'Surface each relevant stored Revisit condition from comparison_input.sections and state the selected classification token verbatim in the user response. Do not invent evidence. Use satisfied only when user-supplied present facts directly establish it; the requested conflicting action itself is not evidence. Use no evidence when facts are absent or concern something other than the stored condition. Use ambiguous only when user-supplied condition facts are relevant but incomplete or conflicting.' };
    const result = { schema: 'context-decision-check/v1', coverage: exact ? 'exact_slot' : 'discovery_only', comparison_input: comparison, input_digest: (0, common_1.canonicalDigest)(comparison), deterministic: { exact_slot: extract(current.filter(x => exact && x.scope === scope && x.decision_key === key)), scope_overlap: extract(current.filter(x => exact && x.decision_key === key && x.scope !== scope && (0, common_1.scopesOverlap)(x.scope, scope))) }, assessment_contract: assessment, retrieval: { total_current: rows.length, metadata_matches: eligible.length, body_reads: current.length, selected_semantic_bytes: Buffer.byteLength((0, common_1.canonicalJson)(current)), index_sha256: (0, common_1.sha256)(Buffer.from(area.text)), returned: current.length, omitted, omitted_id_sample: sample, omitted_id_sample_truncated: omitted > sample.length, full_current_set: omitted === 0, bounded: true }, warning: 'relation=new is valid only within the queried Current set and does not prove global absence of conflict.', physical_write: false };
    result.current_links = currentLinks;
    if (!exact)
        result.caveat = 'no-conflict cannot be concluded; re-run with exact scope/decision_key before preview';
    (0, common_1.check)(Buffer.byteLength((0, common_1.canonicalJson)(result)) <= 32768, 'comparison_too_large', 'Decision check exceeds its output byte limit.', {}, common_1.EXIT.conflict);
    return result;
}
function decisionSpecView(root, scope, maxBytes = 32768) {
    (0, common_1.check)(maxBytes >= 512 && maxBytes <= 32768, 'usage_invalid', 'spec-view maxBytes must be in 512..32768.');
    scope = (0, common_1.canonicalScope)(scope);
    const area = decisionArea(root), rows = (0, documents_1.parseAreaIndex)(area.text).current, matched = rows.filter(r => (0, common_1.scopesOverlap)(scope, r.scope)).sort((a, b) => (0, common_1.compareText)(a.created_at, b.created_at) || (0, common_1.compareText)(a.id, b.id)), items = [];
    let bodyReads = 0;
    const result = () => ({ schema: 'context-decision-spec-view/v1', scope, items, returned: items.length, omitted_count: matched.length - items.length, truncated: matched.length > items.length, max_bytes: maxBytes, retrieval: { index_sha256: (0, common_1.sha256)(Buffer.from(area.text)), total_current: rows.length, metadata_matches: matched.length, body_reads: bodyReads, history_body_reads: 0 }, projection: 'ephemeral', physical_write: false });
    const size = () => Buffer.byteLength(JSON.stringify({ ok: true, result: result() }) + '\n');
    (0, common_1.check)(size() <= maxBytes, 'output_too_large', 'spec-view metadata exceeds the byte budget.', {}, common_1.EXIT.conflict);
    for (const row of matched) {
        const doc = (0, documents_1.parseDocument)((0, filesystem_1.readText)(root, row.path), area.descriptor), fm = doc.frontmatter;
        bodyReads++;
        items.push({ id: fm.id, path: row.path, created_at: fm.created_at, scope: fm.scope, decision_key: fm.decision_key, sections: { Decision: (0, documents_1.sectionValue)(doc, 'Decision'), Rationale: (0, documents_1.sectionValue)(doc, 'Rationale') } });
        if (size() > maxBytes) {
            items.pop();
            while (items.length && size() > maxBytes)
                items.pop();
            break;
        }
    }
    (0, common_1.check)(size() <= maxBytes, 'output_too_large', 'spec-view output exceeds the byte budget.', {}, common_1.EXIT.conflict);
    return result();
}
