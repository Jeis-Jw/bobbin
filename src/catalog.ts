import * as fs from 'node:fs';
import { ObjectValue, contracts, canonicalDigest, canonicalJson, compactJson, normalizedKey, canonicalKey, strictJson, compareText, check, requireId, EXIT, BobbinError, scopesOverlap, nfc } from './common';
import { parseFrontmatter, parseAreaIndex, parseDocument, ContextDocument, readProfile, descriptorFor, validateDescriptor, validateLifecycle, entryRow, markdownEscape, extractBlock, replaceBlock } from './documents';
import { contained, bytes, readText, utf8 } from './filesystem';
export interface Area {
    row: ObjectValue;
    metadata: ObjectValue;
    descriptor?: ObjectValue;
    text: string;
}
export interface RecordEntry {
    path: string;
    content: string;
    document: ContextDocument;
    row: ObjectValue;
    kind: string;
    area: Area;
}
export function referenceIds(record: RecordEntry): Set<string> {
    const fm = record.document.frontmatter, ids = new Set<string>(), add = (value: any) => { for (const id of Array.isArray(value) ? value : [value])
        if (typeof id === 'string' && /^ctx_[0-9a-f]{32}$/.test(id))
            ids.add(id); };
    for (const field of ['anchors', 'supersedes', 'superseded_by'])
        add(fm[field]);
    for (const value of Object.values(fm.relations ?? {}))
        add(value);
    for (const [field, spec] of Object.entries(record.area.descriptor?.structural_profile?.fields ?? {}) as [
        string,
        ObjectValue
    ][])
        if (['context_id', 'context_id_list'].includes(spec.type))
            add(fm[field]);
    if (record.kind === 'observation')
        for (const [key, value] of Object.entries(record.document.sections))
            if (key === 'Evidence' || key === '근거')
                for (const line of value.split('\n'))
                    add(line.replace(/^\s*-\s*/, '').trim());
    return ids;
}
export const ROOT_INDEX = 'context/context.index.md';
export function registeredAreas(root: string): Area[] {
    const raw = bytes(root, ROOT_INDEX);
    check(raw, 'context_root_missing', 'Context root index is missing.', { path: ROOT_INDEX }, EXIT.notFound);
    const rootText = utf8(raw), fm = parseFrontmatter(rootText).frontmatter;
    check(fm.schema === 'context-root-index/v1' && fm.index === true, 'index_noncanonical', 'Invalid root index metadata.', {}, EXIT.integrity);
    const seen = new Set<string>();
    return extractBlock(rootText, 'areas').map(line => {
        const match = /<!-- context-area (\{.*\}) -->$/.exec(line);
        check(match, 'index_noncanonical', 'Invalid root catalog row.', {}, EXIT.integrity);
        const row = strictJson(match[1]);
        check(typeof row.area === 'string' && /^[a-z][a-z0-9_-]{0,79}$/.test(row.area) && row.path === `context/${row.area}/${row.area}.index.md` && !seen.has(row.area), 'index_noncanonical', 'Invalid or duplicate root area.', {}, EXIT.integrity);
        seen.add(row.area);
        const text = readText(root, row.path), metadata = parseFrontmatter(text).frontmatter, descriptor = readProfile(text) ?? descriptorFor(row.area);
        check(metadata.area === row.area && metadata.owner === row.owner && metadata.artifact_schema === row.artifact_schema && metadata.authority === row.authority, 'index_stale', 'Area metadata differs from root catalog.', { area: row.area }, EXIT.integrity);
        if (descriptor) {
            validateDescriptor(descriptor);
            if (descriptor.schema === 'context-owner-descriptor/v2') {
                const profile = readProfile(text);
                check(profile, 'owner_profile_invalid', 'Profiled owner index has no descriptor.', {}, EXIT.integrity);
                const registry = extractBlock(rootText, 'owner-profiles').map(x => /<!-- context-owner-profile (\{.*\}) -->$/.exec(x)).map(m => m ? strictJson(m[1]) : null).find(x => x?.area === row.area);
                check(registry && registry.descriptor_digest === canonicalDigest(profile), 'owner_profile_invalid', 'Root registry and area descriptor differ.', {}, EXIT.integrity);
            }
        }
        return { row, metadata, descriptor, text };
    });
}
export function listArtifactPaths(root: string, area: string, includeHistory = true): string[] {
    const output: string[] = [];
    for (const directory of [`context/${area}`, ...(includeHistory ? [`context/${area}/retired`] : [])]) {
        const target = contained(root, directory);
        if (!fs.existsSync(target))
            continue;
        for (const item of fs.readdirSync(target, { withFileTypes: true })) {
            if (!item.name.endsWith('.md') || item.name.endsWith('.index.md'))
                continue;
            check(item.isFile() && !item.isSymbolicLink(), 'path_unsafe', 'Artifacts must be regular files.', { path: directory + '/' + item.name }, EXIT.integrity);
            output.push(directory + '/' + item.name);
        }
    }
    return output.sort(compareText);
}
function terms(fm: ObjectValue): string[] {
    const values = new Map<string, string>();
    for (const raw of [...(fm.tags ?? []), ...(fm.search_terms ?? [])]) {
        const value = nfc(raw.trim()), key = normalizedKey(value);
        if (value && (!values.has(key) || compareText(value, values.get(key)!) < 0))
            values.set(key, value);
    }
    return [...values.keys()].sort(compareText).map(k => values.get(k)!);
}
export function projectEntry(area: Area, relative: string, doc: ContextDocument): ObjectValue {
    const fm = doc.frontmatter, state = relative.startsWith(`context/${area.row.area}/retired/`) ? 'history' : 'current';
    check(fm.schema === area.metadata.artifact_schema, 'schema_area_mismatch', 'Artifact schema differs from its area.', { path: relative }, EXIT.integrity);
    validateLifecycle(fm, state, area.descriptor);
    const row: ObjectValue = { id: fm.id, path: relative, title: fm.title, summary: fm.summary, state, created_at: fm.created_at };
    if (fm.updated_at)
        row.updated_at = fm.updated_at;
    row.terms = terms(fm);
    if (state === 'history') {
        row.retired_at = fm.retired_at;
        row.retired_reason = fm.retired_reason;
        if (fm.superseded_by)
            row.superseded_by = fm.superseded_by;
    }
    for (const key of area.metadata.projection_fields ?? [])
        if (Object.hasOwn(fm, key))
            row[key] = fm[key];
    return row;
}
export function scanRecords(root: string, areas = registeredAreas(root), overlay = new Map<string, string | null>()): RecordEntry[] {
    const records: RecordEntry[] = [], ids = new Set<string>();
    for (const area of areas) {
        const paths = new Set([...listArtifactPaths(root, area.row.area), ...[...overlay.keys()].filter(p => p.startsWith(`context/${area.row.area}/`) && p.endsWith('.md') && !p.endsWith('.index.md'))]);
        for (const relative of paths) {
            const content = overlay.has(relative) ? overlay.get(relative)! : readText(root, relative);
            if (content === null)
                continue;
            const document = parseDocument(content, area.descriptor), row = projectEntry(area, relative, document);
            check(!ids.has(row.id), 'duplicate_id', 'Artifact ID is present more than once.', { id: row.id }, EXIT.integrity);
            ids.add(row.id);
            records.push({ path: relative, content, document, row, kind: area.row.area, area });
        }
    }
    return records;
}
export function rebuildArea(area: Area, records: RecordEntry[]): string {
    let text = area.text;
    for (const state of ['current', ...(area.row.area === 'snapshot' ? [] : ['history'])]) {
        const rows = records.filter(r => r.kind === area.row.area && r.row.state === state).map(r => r.row).sort((a, b) => compareText(a.created_at, b.created_at) || compareText(a.id, b.id));
        text = replaceBlock(text, state, rows.map(entryRow));
    }
    return text;
}
export function newArea(kind: string): Area {
    const builtin = contracts.builtin_specs.find((s: any[]) => s[0].area === kind), owner = contracts.owners[kind];
    check(builtin || owner, 'owner_unavailable', 'Unknown owner.', { kind });
    let text: string, row: ObjectValue, descriptor: ObjectValue | undefined;
    if (builtin) {
        row = structuredClone(builtin[0]);
        text = contracts.init_contents[row.path];
    }
    else {
        descriptor = structuredClone(owner.descriptor);
        text = owner.index_seed;
        const d = descriptor!;
        row = { area: kind, path: `context/${kind}/${kind}.index.md`, owner: d.owner, claims: [kind], artifact_schema: d.artifact_schema, authority: d.authority };
        if (d.schema === 'context-owner-descriptor/v2' && !readProfile(text)) {
            const { lines, closing } = parseFrontmatter(text);
            lines.splice(closing + 2, 0, '<!-- BEGIN CONTEXT GENERATED:owner-profile -->', canonicalJson(d), '<!-- END CONTEXT GENERATED:owner-profile -->', '');
            text = lines.join('\n');
        }
    }
    return { row, metadata: parseFrontmatter(text).frontmatter, descriptor, text };
}
export function renderRoot(areas: Area[], seed: string = contracts.root_seed): string {
    const sorted = [...areas].sort((a, b) => compareText(a.row.area, b.row.area));
    let text = replaceBlock(seed, 'areas', sorted.map(a => `- [[${a.row.path.slice(0, -3)}]] — ${markdownEscape(a.row.area[0].toUpperCase() + a.row.area.slice(1))}: ${markdownEscape(a.metadata.summary)} <!-- context-area ${compactJson(a.row)} -->`));
    const profiles = sorted.filter(a => a.descriptor?.schema === 'context-owner-descriptor/v2').map(a => `<!-- context-owner-profile ${compactJson({ area: a.row.area, descriptor_schema: a.descriptor!.schema, descriptor_digest: canonicalDigest(a.descriptor) })} -->`);
    if (text.includes('CONTEXT GENERATED:owner-profiles'))
        text = replaceBlock(text, 'owner-profiles', profiles);
    else if (profiles.length)
        text = text.replace(/\n*$/, '') + '\n\n## Owner Profiles\n<!-- BEGIN CONTEXT GENERATED:owner-profiles -->\n' + profiles.join('\n') + '\n<!-- END CONTEXT GENERATED:owner-profiles -->\n';
    return text;
}
export function findRecord(root: string, id: string, areas = registeredAreas(root)): RecordEntry {
    requireId(id);
    // Use the index first. Explicit ID lookup may scan all files to recover a moved target;
    // discovery recall has a separate bounded fallback budget.
    let selected: RecordEntry | undefined;
    for (const area of areas) {
        try {
            const index = parseAreaIndex(area.text);
            for (const row of [...index.current, ...index.history])
                if (row.id === id && bytes(root, row.path)) {
                    const content = readText(root, row.path), document = parseDocument(content, area.descriptor);
                    if (document.frontmatter.id !== id)
                        continue;
                    check(!selected, 'duplicate_id', 'Requested ID appears in multiple areas.', { id }, EXIT.integrity);
                    selected = { path: row.path, content, document, row: projectEntry(area, row.path, document), kind: area.row.area, area };
                }
        }
        catch (e) {
            if (!(e instanceof BobbinError) || ['path_escape', 'symlink_path', 'duplicate_id'].includes(e.code))
                throw e;
        }
    }
    if (selected)
        return selected;
    const matches = scanRecords(root, areas).filter(r => r.row.id === id);
    check(matches.length === 1, 'not_found', 'Artifact ID was not found.', { id }, EXIT.notFound);
    return matches[0];
}
export function validateRelations(records: RecordEntry[]): void {
    const byId = new Map(records.map(r => [r.row.id, r]));
    for (const record of records) {
        const fm = record.document.frontmatter;
        for (const [predicate, ids] of Object.entries(fm.relations ?? {}))
            if (predicate.includes(':'))
                for (const id of ids as string[]) {
                    const target = byId.get(id);
                    check(target && target.kind === predicate.split(':')[1], 'typed_relation_invalid', 'Typed relation target is missing or has the wrong kind.', { id, predicate }, EXIT.integrity);
                }
        if (fm.superseded_by) {
            const successor = byId.get(fm.superseded_by);
            check(successor && successor.document.frontmatter.supersedes?.includes(fm.id), 'lifecycle_invalid', 'Successor edge is not reciprocal.', { id: fm.id }, EXIT.integrity);
        }
        for (const id of fm.supersedes ?? []) {
            const predecessor = byId.get(id);
            check(predecessor && predecessor.document.frontmatter.superseded_by === fm.id, 'lifecycle_invalid', 'Predecessor edge is not reciprocal.', { id: fm.id }, EXIT.integrity);
        }
    }
    for (const record of records) {
        const visited = new Set<string>();
        let current: RecordEntry | undefined = record;
        while (current) {
            check(!visited.has(current.row.id), 'lifecycle_cycle', 'Supersession contains a cycle.', {}, EXIT.integrity);
            visited.add(current.row.id);
            current = byId.get(current.document.frontmatter.superseded_by);
        }
    }
}
export function validateSlots(records: RecordEntry[], acknowledged: string[] = [], changedIds?: Set<string>): void {
    const current = records.filter(r => r.row.state === 'current');
    for (let i = 0; i < current.length; i++) {
        const a = current[i], fm = a.document.frontmatter, key = `${a.kind}_key`;
        if (!['decision', 'term', 'intent', 'document'].includes(a.kind))
            continue;
        for (const b of current.slice(i + 1).filter(b => b.kind === a.kind)) {
            if (changedIds && !changedIds.has(a.row.id) && !changedIds.has(b.row.id))
                continue;
            const bf = b.document.frontmatter;
            if (a.kind === 'term') {
                const av = [fm.term, ...(fm.aliases ?? []), ...(fm.deprecated_terms ?? [])].map(canonicalKey), bv = [bf.term, ...(bf.aliases ?? []), ...(bf.deprecated_terms ?? [])].map(canonicalKey);
                check(!scopesOverlap(fm.scope, bf.scope) || !av.some(x => bv.includes(x)), 'term_conflict', 'Current terminology overlaps in related scopes.', { ids: [fm.id, bf.id] }, EXIT.conflict);
            }
            else if (fm[key] === bf[key] && (a.kind === 'decision' ? scopesOverlap(fm.scope, bf.scope) : fm.scope === bf.scope)) {
                const permitted = a.kind === 'decision' && fm.scope !== bf.scope && (acknowledged.includes(fm.id) || acknowledged.includes(bf.id));
                check(permitted, 'duplicate_current_slot', 'Current records occupy the same or overlapping slot.', { ids: [fm.id, bf.id] }, EXIT.conflict);
            }
        }
    }
}
