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
exports.ROOT_INDEX = void 0;
exports.referenceIds = referenceIds;
exports.registeredAreas = registeredAreas;
exports.listArtifactPaths = listArtifactPaths;
exports.projectEntry = projectEntry;
exports.scanRecords = scanRecords;
exports.rebuildArea = rebuildArea;
exports.newArea = newArea;
exports.renderRoot = renderRoot;
exports.findRecord = findRecord;
exports.validateRelations = validateRelations;
exports.validateSlots = validateSlots;
const fs = __importStar(require("node:fs"));
const common_1 = require("./common");
const documents_1 = require("./documents");
const filesystem_1 = require("./filesystem");
function referenceIds(record) {
    const fm = record.document.frontmatter, ids = new Set(), add = (value) => {
        for (const id of Array.isArray(value) ? value : [value])
            if (typeof id === 'string' && /^ctx_[0-9a-f]{32}$/.test(id))
                ids.add(id);
    };
    for (const field of ['anchors', 'supersedes', 'superseded_by'])
        add(fm[field]);
    for (const value of Object.values(fm.relations ?? {}))
        add(value);
    for (const [field, spec] of Object.entries(record.area.descriptor?.structural_profile?.fields ?? {}))
        if (['context_id', 'context_id_list'].includes(spec.type))
            add(fm[field]);
    if (record.kind === 'observation')
        for (const [key, value] of Object.entries(record.document.sections))
            if (key === 'Evidence' || key === '근거')
                for (const line of value.split('\n'))
                    add(line.replace(/^\s*-\s*/, '').trim());
    return ids;
}
exports.ROOT_INDEX = 'context/context.index.md';
function registeredAreas(root) {
    const raw = (0, filesystem_1.bytes)(root, exports.ROOT_INDEX);
    (0, common_1.check)(raw, 'context_root_missing', 'Context root index is missing.', { path: exports.ROOT_INDEX }, common_1.EXIT.notFound);
    const rootText = (0, filesystem_1.utf8)(raw), fm = (0, documents_1.parseFrontmatter)(rootText).frontmatter;
    (0, common_1.check)(fm.schema === 'context-root-index/v1' && fm.index === true, 'index_noncanonical', 'Invalid root index metadata.', {}, common_1.EXIT.integrity);
    const seen = new Set();
    return (0, documents_1.extractBlock)(rootText, 'areas').map(line => {
        const match = /<!-- context-area (\{.*\}) -->$/.exec(line);
        (0, common_1.check)(match, 'index_noncanonical', 'Invalid root catalog row.', {}, common_1.EXIT.integrity);
        const row = (0, common_1.strictJson)(match[1]);
        (0, common_1.check)(typeof row.area === 'string' && /^[a-z][a-z0-9_-]{0,79}$/.test(row.area) && row.path === `context/${row.area}/${row.area}.index.md` && !seen.has(row.area), 'index_noncanonical', 'Invalid or duplicate root area.', {}, common_1.EXIT.integrity);
        seen.add(row.area);
        const text = (0, filesystem_1.readText)(root, row.path), metadata = (0, documents_1.parseFrontmatter)(text).frontmatter, descriptor = (0, documents_1.readProfile)(text) ?? (0, documents_1.descriptorFor)(row.area);
        (0, common_1.check)(metadata.area === row.area && metadata.owner === row.owner && metadata.artifact_schema === row.artifact_schema && metadata.authority === row.authority, 'index_stale', 'Area metadata differs from root catalog.', { area: row.area }, common_1.EXIT.integrity);
        if (descriptor) {
            (0, documents_1.validateDescriptor)(descriptor);
            if (descriptor.schema === 'context-owner-descriptor/v2') {
                const profile = (0, documents_1.readProfile)(text);
                (0, common_1.check)(profile, 'owner_profile_invalid', 'Profiled owner index has no descriptor.', {}, common_1.EXIT.integrity);
                const registry = (0, documents_1.extractBlock)(rootText, 'owner-profiles').map(x => /<!-- context-owner-profile (\{.*\}) -->$/.exec(x)).map(m => m ? (0, common_1.strictJson)(m[1]) : null).find(x => x?.area === row.area);
                (0, common_1.check)(registry && registry.descriptor_digest === (0, common_1.canonicalDigest)(profile), 'owner_profile_invalid', 'Root registry and area descriptor differ.', {}, common_1.EXIT.integrity);
            }
        }
        return { row, metadata, descriptor, text };
    });
}
function listArtifactPaths(root, area, includeHistory = true) {
    const output = [];
    for (const directory of [`context/${area}`, ...(includeHistory ? [`context/${area}/retired`] : [])]) {
        const target = (0, filesystem_1.contained)(root, directory);
        if (!fs.existsSync(target))
            continue;
        for (const item of fs.readdirSync(target, { withFileTypes: true })) {
            if (!item.name.endsWith('.md') || item.name.endsWith('.index.md'))
                continue;
            (0, common_1.check)(item.isFile() && !item.isSymbolicLink(), 'path_unsafe', 'Artifacts must be regular files.', { path: directory + '/' + item.name }, common_1.EXIT.integrity);
            output.push(directory + '/' + item.name);
        }
    }
    return output.sort(common_1.compareText);
}
function terms(fm) {
    const values = new Map();
    for (const raw of [...(fm.tags ?? []), ...(fm.search_terms ?? [])]) {
        const value = (0, common_1.nfc)(raw.trim()), key = (0, common_1.normalizedKey)(value);
        if (value && (!values.has(key) || (0, common_1.compareText)(value, values.get(key)) < 0))
            values.set(key, value);
    }
    return [...values.keys()].sort(common_1.compareText).map(k => values.get(k));
}
function projectEntry(area, relative, doc) {
    const fm = doc.frontmatter, state = relative.startsWith(`context/${area.row.area}/retired/`) ? 'history' : 'current';
    (0, common_1.check)(fm.schema === area.metadata.artifact_schema, 'schema_area_mismatch', 'Artifact schema differs from its area.', { path: relative }, common_1.EXIT.integrity);
    (0, documents_1.validateLifecycle)(fm, state, area.descriptor);
    const row = { id: fm.id, path: relative, title: fm.title, summary: fm.summary, state, created_at: fm.created_at };
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
function scanRecords(root, areas = registeredAreas(root), overlay = new Map()) {
    const records = [], ids = new Set();
    for (const area of areas) {
        const paths = new Set([...listArtifactPaths(root, area.row.area), ...[...overlay.keys()].filter(p => p.startsWith(`context/${area.row.area}/`) && p.endsWith('.md') && !p.endsWith('.index.md'))]);
        for (const relative of paths) {
            const content = overlay.has(relative) ? overlay.get(relative) : (0, filesystem_1.readText)(root, relative);
            if (content === null)
                continue;
            const document = (0, documents_1.parseDocument)(content, area.descriptor), row = projectEntry(area, relative, document);
            (0, common_1.check)(!ids.has(row.id), 'duplicate_id', 'Artifact ID is present more than once.', { id: row.id }, common_1.EXIT.integrity);
            ids.add(row.id);
            records.push({ path: relative, content, document, row, kind: area.row.area, area });
        }
    }
    return records;
}
function rebuildArea(area, records) {
    let text = area.text;
    for (const state of ['current', ...(area.row.area === 'snapshot' ? [] : ['history'])]) {
        const rows = records.filter(r => r.kind === area.row.area && r.row.state === state).map(r => r.row).sort((a, b) => (0, common_1.compareText)(a.created_at, b.created_at) || (0, common_1.compareText)(a.id, b.id));
        text = (0, documents_1.replaceBlock)(text, state, rows.map(documents_1.entryRow));
    }
    return text;
}
function newArea(kind) {
    const builtin = common_1.contracts.builtin_specs.find((s) => s[0].area === kind), owner = common_1.contracts.owners[kind];
    (0, common_1.check)(builtin || owner, 'owner_unavailable', 'Unknown owner.', { kind });
    let text, row, descriptor;
    if (builtin) {
        row = structuredClone(builtin[0]);
        text = common_1.contracts.init_contents[row.path];
    }
    else {
        descriptor = structuredClone(owner.descriptor);
        text = owner.index_seed;
        const d = descriptor;
        row = { area: kind, path: `context/${kind}/${kind}.index.md`, owner: d.owner, claims: [kind], artifact_schema: d.artifact_schema, authority: d.authority };
        if (d.schema === 'context-owner-descriptor/v2' && !(0, documents_1.readProfile)(text)) {
            const { lines, closing } = (0, documents_1.parseFrontmatter)(text);
            lines.splice(closing + 2, 0, '<!-- BEGIN CONTEXT GENERATED:owner-profile -->', (0, common_1.canonicalJson)(d), '<!-- END CONTEXT GENERATED:owner-profile -->', '');
            text = lines.join('\n');
        }
    }
    return { row, metadata: (0, documents_1.parseFrontmatter)(text).frontmatter, descriptor, text };
}
function renderRoot(areas, seed = common_1.contracts.root_seed) {
    const sorted = [...areas].sort((a, b) => (0, common_1.compareText)(a.row.area, b.row.area));
    let text = (0, documents_1.replaceBlock)(seed, 'areas', sorted.map(a => `- [[${a.row.path.slice(0, -3)}]] — ${(0, documents_1.markdownEscape)(a.row.area[0].toUpperCase() + a.row.area.slice(1))}: ${(0, documents_1.markdownEscape)(a.metadata.summary)} <!-- context-area ${(0, common_1.compactJson)(a.row)} -->`));
    const profiles = sorted.filter(a => a.descriptor?.schema === 'context-owner-descriptor/v2').map(a => `<!-- context-owner-profile ${(0, common_1.compactJson)({ area: a.row.area, descriptor_schema: a.descriptor.schema, descriptor_digest: (0, common_1.canonicalDigest)(a.descriptor) })} -->`);
    if (text.includes('CONTEXT GENERATED:owner-profiles'))
        text = (0, documents_1.replaceBlock)(text, 'owner-profiles', profiles);
    else if (profiles.length)
        text = text.replace(/\n*$/, '') + '\n\n## Owner Profiles\n<!-- BEGIN CONTEXT GENERATED:owner-profiles -->\n' + profiles.join('\n') + '\n<!-- END CONTEXT GENERATED:owner-profiles -->\n';
    return text;
}
function findRecord(root, id, areas = registeredAreas(root)) {
    (0, common_1.requireId)(id);
    // Use the index first. Explicit ID lookup may scan all files to recover a moved target;
    // discovery recall has a separate bounded fallback budget.
    let selected;
    for (const area of areas) {
        try {
            const index = (0, documents_1.parseAreaIndex)(area.text);
            for (const row of [...index.current, ...index.history])
                if (row.id === id && (0, filesystem_1.bytes)(root, row.path)) {
                    const content = (0, filesystem_1.readText)(root, row.path), document = (0, documents_1.parseDocument)(content, area.descriptor);
                    if (document.frontmatter.id !== id)
                        continue;
                    (0, common_1.check)(!selected, 'duplicate_id', 'Requested ID appears in multiple areas.', { id }, common_1.EXIT.integrity);
                    selected = { path: row.path, content, document, row: projectEntry(area, row.path, document), kind: area.row.area, area };
                }
        }
        catch (e) {
            if (!(e instanceof common_1.BobbinError) || ['path_escape', 'symlink_path', 'duplicate_id'].includes(e.code))
                throw e;
        }
    }
    if (selected)
        return selected;
    const matches = scanRecords(root, areas).filter(r => r.row.id === id);
    (0, common_1.check)(matches.length === 1, 'not_found', 'Artifact ID was not found.', { id }, common_1.EXIT.notFound);
    return matches[0];
}
function validateRelations(records) {
    const byId = new Map(records.map(r => [r.row.id, r]));
    for (const record of records) {
        const fm = record.document.frontmatter;
        for (const [predicate, ids] of Object.entries(fm.relations ?? {}))
            if (predicate.includes(':'))
                for (const id of ids) {
                    const target = byId.get(id);
                    (0, common_1.check)(target && target.kind === predicate.split(':')[1], 'typed_relation_invalid', 'Typed relation target is missing or has the wrong kind.', { id, predicate }, common_1.EXIT.integrity);
                }
        if (fm.superseded_by) {
            const successor = byId.get(fm.superseded_by);
            (0, common_1.check)(successor && successor.document.frontmatter.supersedes?.includes(fm.id), 'lifecycle_invalid', 'Successor edge is not reciprocal.', { id: fm.id }, common_1.EXIT.integrity);
        }
        for (const id of fm.supersedes ?? []) {
            const predecessor = byId.get(id);
            (0, common_1.check)(predecessor && predecessor.document.frontmatter.superseded_by === fm.id, 'lifecycle_invalid', 'Predecessor edge is not reciprocal.', { id: fm.id }, common_1.EXIT.integrity);
        }
    }
    for (const record of records) {
        const visited = new Set();
        let current = record;
        while (current) {
            (0, common_1.check)(!visited.has(current.row.id), 'lifecycle_cycle', 'Supersession contains a cycle.', {}, common_1.EXIT.integrity);
            visited.add(current.row.id);
            current = byId.get(current.document.frontmatter.superseded_by);
        }
    }
}
function validateSlots(records, acknowledged = [], changedIds) {
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
                const av = [fm.term, ...(fm.aliases ?? []), ...(fm.deprecated_terms ?? [])].map(common_1.canonicalKey), bv = [bf.term, ...(bf.aliases ?? []), ...(bf.deprecated_terms ?? [])].map(common_1.canonicalKey);
                (0, common_1.check)(!(0, common_1.scopesOverlap)(fm.scope, bf.scope) || !av.some(x => bv.includes(x)), 'term_conflict', 'Current terminology overlaps in related scopes.', { ids: [fm.id, bf.id] }, common_1.EXIT.conflict);
            }
            else if (fm[key] === bf[key] && (a.kind === 'decision' ? (0, common_1.scopesOverlap)(fm.scope, bf.scope) : fm.scope === bf.scope)) {
                const permitted = a.kind === 'decision' && fm.scope !== bf.scope && (acknowledged.includes(fm.id) || acknowledged.includes(bf.id));
                (0, common_1.check)(permitted, 'duplicate_current_slot', 'Current records occupy the same or overlapping slot.', { ids: [fm.id, bf.id] }, common_1.EXIT.conflict);
            }
        }
    }
}
