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
exports.Bobbin = void 0;
exports.createBobbin = createBobbin;
exports.fitSections = fitSections;
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
const common_1 = require("./common");
const documents_1 = require("./documents");
const owners_1 = require("./owners");
const filesystem_1 = require("./filesystem");
const catalog_1 = require("./catalog");
const decision_1 = require("./decision");
const runtime_1 = require("./runtime");
const routing_1 = require("./routing");
const FEATURES = ['decision', 'assumption', 'term', 'intent', 'document'];
const BUILTINS = ['snapshot', 'observation', 'archive'];
function loadSettings(project) {
    const raw = (0, filesystem_1.bytes)(project, '.bobbin/config.json', 8192);
    if (!raw)
        return { project, config: null, digest: null, mode: 'explicit', enabled: null };
    const config = (0, common_1.strictJson)((0, filesystem_1.utf8)(raw), 'config_invalid');
    (0, common_1.exact)(config, ['schema', 'features', 'approval', 'vault'], 'config_invalid');
    (0, common_1.exact)(config.approval, ['mode'], 'config_invalid');
    (0, common_1.check)(config.schema === 'bobbin-project/v1' && Array.isArray(config.features) && config.features.every((x) => FEATURES.includes(x)) && new Set(config.features).size === config.features.length && ['explicit', 'auto', 'adaptive'].includes(config.approval.mode) && typeof config.vault === 'string' && !!config.vault && !config.vault.includes('\0'), 'config_invalid', 'Invalid Bobbin project configuration.');
    return { project, config, digest: (0, common_1.sha256)(raw), mode: config.approval.mode, enabled: [...BUILTINS, ...config.features] };
}
function previewDigest(preview) { return (0, common_1.canonicalDigest)(preview); }
class Bobbin {
    vault;
    project;
    storage;
    projectStorage;
    constructor(options) {
        try {
            (0, common_1.check)(options.vault || options.project, 'usage_invalid', 'Provide a vault or project directory.');
            this.project = (0, filesystem_1.realDirectory)(options.project ?? options.vault);
            const settings = loadSettings(this.project);
            this.vault = (0, filesystem_1.realDirectory)(options.vault ?? path.resolve(this.project, settings.config?.vault ?? '.'));
            this.storage = new filesystem_1.Filesystem(this.vault, options);
            this.projectStorage = new filesystem_1.Filesystem(this.project, options);
        }
        catch (error) {
            throw (0, common_1.toBobbinError)(error);
        }
    }
    async locked(fn, projectToo = false) {
        try {
            if (!projectToo || this.project === this.vault)
                return await this.storage.locked(fn);
            const [first, second] = this.project < this.vault ? [this.projectStorage, this.storage] : [this.storage, this.projectStorage];
            return await first.locked(() => second.locked(fn));
        }
        catch (error) {
            throw (0, common_1.toBobbinError)(error);
        }
    }
    policyBinding() { const settings = loadSettings(this.project); return { project_identity: (0, filesystem_1.identity)(this.project), config_digest: settings.digest, runtime: 'bobbin-typescript/v1', runtime_digest: runtime_1.runtimeDigest, owners: (0, catalog_1.registeredAreas)(this.vault).map(a => ({ area: a.row.area, descriptor: a.descriptor ?? a.row })) }; }
    adoptLegacy(confirmLegacyStopped) {
        return (0, common_1.withErrors)(() => {
            const roots = this.project === this.vault ? [this.storage] : [this.storage, this.projectStorage].sort((a, b) => (0, common_1.compareText)(a.root, b.root));
            const adopted = roots.map(root => root.adoptLegacy(confirmLegacyStopped).vault);
            return { adopted: true, vault: this.vault, project: this.project, adopted_roots: adopted, protocol: 'exclusive-node-writer/v1', records_changed: false };
        });
    }
    async recoverRuntime() {
        try {
            const roots = this.project === this.vault ? [this.storage] : [this.storage, this.projectStorage].sort((a, b) => (0, common_1.compareText)(a.root, b.root));
            const recovered = [];
            for (const root of roots) {
                await root.recoverAbandoned();
                recovered.push(root.root);
            }
            return { recovered: true, vault: this.vault, project: this.project, recovered_roots: recovered };
        }
        catch (error) {
            throw (0, common_1.toBobbinError)(error);
        }
    }
    settings() { return (0, common_1.withErrors)(() => { const settings = loadSettings(this.project); return { project: this.project, vault: this.vault, config: settings.config, digest: settings.digest, mode: settings.mode, enabled: settings.enabled, registered_features: (0, filesystem_1.bytes)(this.vault, catalog_1.ROOT_INDEX) ? (0, catalog_1.registeredAreas)(this.vault).map(a => a.row.area).filter(k => FEATURES.includes(k)) : [] }; }); }
    route(batch, results) { return (0, common_1.withErrors)(() => (0, routing_1.routeCandidates)(batch, results, loadSettings(this.project).enabled)); }
    async previewOwnerResult(result) { return this.preview((0, routing_1.operationFromOwnerResult)(result)); }
    async validate(value) {
        const input = value;
        (0, common_1.check)((0, common_1.object)(input), 'usage_invalid', 'Validation input must be an object.');
        if (input.schema === 'bobbin-preview/v1') {
            const { approval_digest, state, applied, ...base } = input;
            (0, common_1.check)(state === 'awaiting_approval' && applied === false && previewDigest(base) === approval_digest, 'approval_digest_mismatch', 'Preview bytes changed.', {}, common_1.EXIT.conflict);
            const current = await this.preview(input.operation);
            (0, common_1.check)((0, common_1.canonicalDigest)(current.vault_identity) === (0, common_1.canonicalDigest)(input.vault_identity) && (0, common_1.canonicalDigest)(current.project_policy) === (0, common_1.canonicalDigest)(input.project_policy) && (0, common_1.canonicalDigest)(current.changes) === (0, common_1.canonicalDigest)(input.changes) && (0, common_1.canonicalDigest)(current.read_preconditions) === (0, common_1.canonicalDigest)(input.read_preconditions), 'stale_input', 'Preview no longer matches the current vault and runtime.', {}, common_1.EXIT.conflict);
            return { status: 'valid', approval_digest, physical_write: false };
        }
        const preview = await this.preview(input.schema === 'context-owner-result/v1' ? (0, routing_1.operationFromOwnerResult)(input) : input);
        return { status: 'valid', approval_digest: preview.approval_digest, physical_write: false };
    }
    async registerArea(descriptor, indexSeed) {
        return this.locked(() => {
            (0, documents_1.validateDescriptor)(descriptor);
            const seed = (0, documents_1.parseAreaIndex)(indexSeed);
            (0, common_1.check)(seed.current.length === 0 && seed.history.length === 0, 'index_seed_invalid', 'Registration seed must have empty generated blocks.');
            const kind = descriptor.kind;
            (0, common_1.check)(seed.frontmatter.area === kind && seed.frontmatter.owner === descriptor.owner && seed.frontmatter.artifact_schema === descriptor.artifact_schema && seed.frontmatter.authority === descriptor.authority && (descriptor.schema !== 'context-owner-descriptor/v2' || (0, common_1.canonicalDigest)(seed.descriptor) === (0, common_1.canonicalDigest)(descriptor)), 'index_seed_invalid', 'Seed differs from descriptor.');
            const areas = (0, catalog_1.registeredAreas)(this.vault), existing = areas.find(a => a.row.area === kind);
            if (existing) {
                (0, common_1.check)((0, common_1.canonicalDigest)(existing.descriptor ?? {}) === (0, common_1.canonicalDigest)(descriptor), 'owner_descriptor_conflict', 'An immutable registered descriptor cannot be replaced.', {}, common_1.EXIT.conflict);
                return { applied: false, changed_paths: [] };
            }
            const area = { row: { area: kind, path: `context/${kind}/${kind}.index.md`, owner: descriptor.owner, claims: [kind], artifact_schema: descriptor.artifact_schema, authority: descriptor.authority }, metadata: seed.frontmatter, descriptor, text: indexSeed };
            (0, common_1.check)((0, filesystem_1.bytes)(this.vault, area.row.path) === null, 'owner_descriptor_conflict', 'An unregistered area index already exists.', {}, common_1.EXIT.conflict);
            const changed = this.storage.transaction([{ path: area.row.path, content: (0, common_1.fileBytes)(indexSeed), expected: null }, { path: catalog_1.ROOT_INDEX, content: (0, common_1.fileBytes)((0, catalog_1.renderRoot)([...areas, area], (0, filesystem_1.readText)(this.vault, catalog_1.ROOT_INDEX))), expected: (0, filesystem_1.digestOrNull)(this.vault, catalog_1.ROOT_INDEX) }]);
            return { applied: changed.length > 0, changed_paths: changed, descriptor };
        });
    }
    async initialize(options = {}) {
        return this.locked(() => {
            const before = loadSettings(this.project), hasRoot = !!(0, filesystem_1.bytes)(this.vault, catalog_1.ROOT_INDEX), areas = hasRoot ? (0, catalog_1.registeredAreas)(this.vault) : [], byKind = new Map(areas.map(a => [a.row.area, a]));
            const features = options.features ?? before.config?.features ?? (hasRoot ? FEATURES.filter(k => byKind.has(k)) : ['decision']);
            (0, common_1.check)(Array.isArray(features) && features.every((x) => FEATURES.includes(x)) && new Set(features).size === features.length, 'config_invalid', 'Invalid feature selection.');
            const mode = options.approvalMode ?? before.mode;
            (0, common_1.check)(['explicit', 'auto', 'adaptive'].includes(mode), 'config_invalid', 'Invalid approval mode.');
            for (const kind of [...BUILTINS, ...features])
                if (!byKind.has(kind)) {
                    const a = (0, catalog_1.newArea)(kind);
                    (0, common_1.check)((0, filesystem_1.bytes)(this.vault, a.row.path) === null, 'area_exists', 'Area index already exists outside the root registry.', { area: kind }, common_1.EXIT.conflict);
                    byKind.set(kind, a);
                }
            const selected = [...byKind.values()], changes = [];
            for (const a of selected)
                if (!areas.includes(a))
                    changes.push({ path: a.row.path, content: (0, common_1.fileBytes)(a.text), expected: null });
            const seed = hasRoot ? (0, filesystem_1.readText)(this.vault, catalog_1.ROOT_INDEX) : common_1.contracts.root_seed;
            const rootText = (0, catalog_1.renderRoot)(selected, seed);
            changes.push({ path: catalog_1.ROOT_INDEX, content: (0, common_1.fileBytes)(rootText), expected: (0, filesystem_1.digestOrNull)(this.vault, catalog_1.ROOT_INDEX) });
            const config = { schema: 'bobbin-project/v1', features, approval: { mode }, vault: path.relative(this.project, this.vault) || '.' };
            const projectChanges = [];
            if ((0, common_1.compactJson)(before.config) !== (0, common_1.compactJson)(config))
                projectChanges.push({ path: '.bobbin/config.json', content: Buffer.from(JSON.stringify(config, null, 2) + '\n'), expected: before.digest });
            if (options.host) {
                const target = options.host === 'codex' ? 'AGENTS.md' : 'CLAUDE.md', original = (0, filesystem_1.bytes)(this.project, target), text = original ? (0, filesystem_1.utf8)(original) : '';
                const begin = '<!-- BEGIN context-core-policy (managed by context-core) -->', end = '<!-- END context-core-policy (managed by context-core) -->';
                (0, common_1.check)((text.includes(begin) === text.includes(end)) && text.split(begin).length <= 2 && text.split(end).length <= 2, 'policy_invalid', 'Managed guidance markers are malformed.');
                const policy = common_1.contracts.policy, updated = text.includes(begin) ? text.slice(0, text.indexOf(begin)) + policy + text.slice(text.indexOf(end) + end.length) : text.replace(/\n*$/, '') + (text ? '\n\n' : '') + policy + '\n';
                projectChanges.push({ path: target, content: (0, common_1.fileBytes)(updated), expected: original ? (0, common_1.sha256)(original) : null });
            }
            if (fs.existsSync(path.join(this.vault, '.git'))) {
                const relative = '.gitattributes', raw = (0, filesystem_1.bytes)(this.vault, relative), original = raw ? (0, filesystem_1.utf8)(raw) : '', begin = '# BEGIN context-core-merge (managed by context-core)', end = '# END context-core-merge (managed by context-core)';
                (0, common_1.check)(original.includes(begin) === original.includes(end) && original.split(begin).length <= 2 && original.split(end).length <= 2, 'policy_invalid', 'Managed merge markers are malformed.');
                const block = common_1.contracts.merge_attributes, updated = original.includes(begin) ? original.slice(0, original.indexOf(begin)) + block + original.slice(original.indexOf(end) + end.length) : original.replace(/\n*$/, '') + (original ? '\n\n' : '') + block + '\n';
                changes.push({ path: relative, content: (0, common_1.fileBytes)(updated), expected: raw ? (0, common_1.sha256)(raw) : null });
            }
            const changed = this.project === this.vault ? this.storage.transaction([...changes, ...projectChanges]) : [...this.storage.transaction(changes), ...this.projectStorage.transaction(projectChanges).map(p => path.join(this.project, p))];
            return { schema: 'bobbin-init-result/v1', version: common_1.VERSION, project: this.project, vault: this.vault, config, enabled: [...BUILTINS, ...features], changed_paths: changed, host_configuration_changed: false, records_migrated: false, applied: changed.length > 0 };
        }, true);
    }
    readResult(record, options = {}) {
        const doc = record.document, schema = doc.frontmatter.schema, chosen = options.sections?.length ? options.sections.map(k => (0, documents_1.sectionName)(schema, k)) : Object.keys(doc.sections).map(k => (0, documents_1.sectionName)(schema, k));
        (0, common_1.check)(chosen.every(k => Object.keys(doc.sections).some(s => (0, documents_1.sectionName)(schema, s) === k)), 'section_invalid', 'Requested section does not exist.', { sections: chosen });
        const available = Object.fromEntries(chosen.map(k => [k, (0, documents_1.sectionValue)(doc, k)]));
        const history = record.row.state === 'history';
        const base = { id: doc.frontmatter.id, kind: record.kind, path: record.path, state: record.row.state, frontmatter: doc.frontmatter, authority: history ? 'historical' : record.area.row.authority, do_not_follow: history, lifecycle_reason: doc.frontmatter.retired_reason ?? null, warnings: doc.warnings, physical_write: false };
        if (record.kind === 'snapshot') {
            base.use_as = 'resume_context';
            base.freshness = 'authority_unknown';
            const anchors = doc.frontmatter.anchors ?? [];
            if (anchors.length) {
                base.freshness = 'anchored';
                for (const id of anchors) {
                    try {
                        if ((0, catalog_1.findRecord)(this.vault, id).row.state !== 'current')
                            base.freshness = 'anchor_changed';
                    }
                    catch (e) {
                        if (e instanceof common_1.BobbinError && e.code === 'not_found')
                            base.freshness = 'anchor_changed';
                        else
                            throw e;
                    }
                }
            }
        }
        if (record.kind === 'observation')
            base.use_as = 'investigate_or_support';
        if (record.kind === 'archive')
            base.use_as = 'source_evidence';
        if (options.maxBytes === undefined)
            return { ...base, sections: available, truncated: false };
        (0, common_1.check)(Number.isSafeInteger(options.maxBytes) && options.maxBytes >= 1 && options.maxBytes <= 32768, 'usage_invalid', 'maxBytes must be 1..32768.');
        return fitSections(base, available, options.maxBytes, { truncated: false }, { truncated: true, full_read_hint: `bobbin read ${doc.frontmatter.id}` });
    }
    async read(id, options = {}) { return this.locked(() => this.readResult((0, catalog_1.findRecord)(this.vault, id), options)); }
    async inspect(id) { return this.locked(() => { const r = (0, catalog_1.findRecord)(this.vault, id); return { path: r.path, content: r.content, digest: (0, common_1.sha256)(Buffer.from(r.content)), document: r.document }; }); }
    normalizeOperation(input) {
        const operation = structuredClone(input);
        (0, common_1.check)((0, common_1.object)(operation), 'usage_invalid', 'Operation must be an object.');
        if (operation.action === 'batch') {
            (0, common_1.check)(Array.isArray(operation.operations) && operation.operations.length >= 1 && operation.operations.length <= 8 && operation.operations.every(op => op.action !== 'batch'), 'candidate_batch_too_large', 'Batch requires 1..8 non-batch operations.');
            operation.operations = operation.operations.map(op => this.normalizeOperation(op));
            return operation;
        }
        if (operation.action === 'capture') {
            operation.id ??= (0, common_1.newId)();
            operation.now ??= (0, common_1.timestamp)();
        }
        else if (operation.action === 'supersede') {
            operation.now ??= (0, common_1.timestamp)();
            operation.successor = this.normalizeOperation(operation.successor);
        }
        else if (['update', 'annotate', 'retire'].includes(operation.action))
            operation.now ??= (0, common_1.timestamp)();
        return operation;
    }
    operationChanges(operation, areas) {
        if (operation.action === 'batch')
            return operation.operations.flatMap(op => this.operationChanges(op, areas));
        if (operation.action === 'capture') {
            const draft = (0, owners_1.draftCapture)(operation.candidate, operation.attestation, operation);
            (0, common_1.check)(areas.some(a => a.row.area === (operation.targetKind ?? operation.candidate.requested_kind)), 'area_not_registered', 'Initialize the requested feature first.', {}, common_1.EXIT.conflict);
            return [{ path: draft.path, before_digest: null, after_digest: (0, common_1.sha256)((0, common_1.fileBytes)(draft.content)), content: draft.content }];
        }
        const r = (0, catalog_1.findRecord)(this.vault, operation.id, areas), fm = structuredClone(r.document.frontmatter), sections = { ...r.document.sections }, before = (0, common_1.sha256)(Buffer.from(r.content));
        const change = (content, relative = r.path, expected = before) => ({ path: relative, before_digest: expected, after_digest: content === null ? null : (0, common_1.sha256)((0, common_1.fileBytes)(content)), content });
        if (operation.action === 'rename') {
            (0, common_1.check)(['snapshot', 'observation'].includes(r.kind), 'lifecycle_invalid', 'Rename is supported for SNAP and OBS. Other kinds retain their owner lifecycle.');
            (0, common_1.check)(operation.filename, 'filename_required', 'Provide a new filename.');
            const destination = path.posix.dirname(r.path) + '/' + (0, common_1.filename)(operation.filename);
            (0, common_1.check)(destination !== r.path, 'no_change', 'Filename is unchanged.');
            return [change(null), change(r.content, destination, null)];
        }
        if (operation.action === 'discard') {
            (0, common_1.check)(['snapshot', 'observation', 'archive'].includes(r.kind), 'lifecycle_invalid', 'Authoritative records must use their retirement lifecycle.');
            const inbound = (0, catalog_1.scanRecords)(this.vault, areas).filter(x => x.row.id !== fm.id && (0, catalog_1.referenceIds)(x).has(fm.id));
            (0, common_1.check)(!inbound.length, 'inbound_reference', 'Referenced artifacts cannot be discarded.', { ids: inbound.map(x => x.row.id) }, common_1.EXIT.conflict);
            return [change(null)];
        }
        (0, common_1.check)(r.row.state === 'current', 'lifecycle_invalid', 'This lifecycle requires a Current artifact.', {}, common_1.EXIT.conflict);
        if (operation.action === 'supersede') {
            (0, common_1.check)(['observation', 'decision', 'assumption', 'term', 'intent'].includes(r.kind), 'lifecycle_invalid', 'This artifact does not support supersession.');
            const successor = (0, owners_1.draftCapture)(operation.successor.candidate, operation.successor.attestation, operation.successor), successorKind = operation.successor.targetKind ?? operation.successor.candidate.requested_kind;
            (0, common_1.check)(successorKind === r.kind || (r.kind === 'observation' && successorKind === 'decision'), 'lifecycle_invalid', 'Unsupported cross-kind supersession.');
            if (r.kind === 'observation' && successorKind === 'decision') {
                (0, common_1.check)(fm.kind_hint === 'decision', 'lifecycle_invalid', 'Only a decision-like fallback OBS can be imported as DEC.');
                (0, common_1.check)(!Object.values(successor.document.frontmatter.relations ?? {}).some((ids) => ids.includes(fm.id)), 'fallback_relation_conflict', 'Fallback import uses lifecycle edges rather than an evidence relation.');
            }
            const semantic = this.sameClaimInput(r, successor.document, operation.successor.candidate);
            (0, owners_1.validateAttestation)(operation.sameClaim, semantic, ['same_semantic_claim'], 'same_claim');
            if (fm.scope)
                (0, common_1.check)(successor.document.frontmatter.scope === fm.scope, 'scope_mismatch', 'Supersession cannot change the record scope.', {}, common_1.EXIT.conflict);
            if (['decision', 'intent', 'term'].includes(r.kind))
                (0, common_1.check)(successor.document.frontmatter[r.kind + '_key'] === fm[r.kind + '_key'], 'slot_mismatch', 'Supersession must retain the semantic slot key.', {}, common_1.EXIT.conflict);
            const sfm = successor.document.frontmatter;
            (0, common_1.check)(sfm.id !== fm.id, 'lifecycle_invalid', 'Successor must have a new ID.');
            fm.retired_at = (0, common_1.timestamp)(operation.now);
            fm.retired_reason = 'superseded';
            fm.superseded_by = sfm.id;
            sfm.supersedes = [...new Set([...(sfm.supersedes ?? []), fm.id])];
            const retired = `context/${r.kind}/retired/${path.posix.basename(r.path, '.md')}--${fm.id.slice(4, 16)}.md`;
            return [change(null), change((0, documents_1.renderDocument)(fm, sections), retired, null), change((0, documents_1.renderDocument)(sfm, successor.document.sections), successor.path, null)];
        }
        const values = operation.values ?? {};
        if (operation.action === 'retire') {
            const reasons = { observation: ['invalidated'], decision: ['withdrawn'], assumption: ['confirmed', 'refuted'], term: ['deprecated'] };
            (0, common_1.check)(reasons[r.kind]?.includes(values.reason), 'lifecycle_invalid', 'Unsupported retirement reason.');
            fm.retired_at = (0, common_1.timestamp)(operation.now);
            fm.retired_reason = values.reason;
            if (['observation', 'decision'].includes(r.kind))
                fm.retirement_note = (0, common_1.shortText)(values.note, 'note', 500);
            if (r.kind === 'assumption') {
                fm.evidence_refs = (0, common_1.stringList)(values.evidence_refs, 'evidence_refs', 1);
                if (values.reason === 'refuted') {
                    fm.refutation_reason = (0, common_1.shortText)(values.refutation_reason, 'refutation_reason', 800);
                    fm.impacted_decisions = values.impacted_decisions ?? [];
                }
            }
            if (r.kind === 'term') {
                fm.deprecation_reason = (0, common_1.shortText)(values.deprecation_reason, 'deprecation_reason', 800);
                if (values.replacement_term)
                    fm.replacement_term = values.replacement_term;
            }
            const retired = `context/${r.kind}/retired/${path.posix.basename(r.path, '.md')}--${fm.id.slice(4, 16)}.md`;
            return [change(null), change((0, documents_1.renderDocument)(fm, sections), retired, null)];
        }
        if (operation.action === 'reverify') {
            (0, common_1.check)(r.kind === 'observation', 'lifecycle_invalid', 'Only observations can be reverified.');
            fm.verified_at = (0, common_1.timestamp)(values.verified_at);
            const evidenceRef = (0, common_1.shortText)(values.evidence_ref, 'evidence_ref', 500);
            fm.source_refs = [...new Set([...(fm.source_refs ?? []), evidenceRef])];
        }
        else {
            (0, common_1.check)(operation.action === 'update' || operation.action === 'annotate', 'usage_invalid', 'Unknown mutation action.');
            (0, common_1.check)(operation.action === 'update' ? ['snapshot', 'document'].includes(r.kind) : ['observation', 'decision', 'assumption', 'term'].includes(r.kind), 'lifecycle_invalid', 'Unsupported mutation for this record kind.');
            const allowed = { snapshot: Object.values(owners_1.sectionFields.snapshot), document: ['Content'], observation: [], decision: [], assumption: [], term: [] };
            if (r.kind === 'snapshot' && !operation.merge) {
                const supplied = Object.keys(operation.sections ?? {}).map(k => (0, documents_1.sectionName)(fm.schema, k));
                (0, common_1.check)(['Current context', 'Open items', 'Next steps'].every(k => supplied.includes(k)), 'snapshot_full_update_required', 'Full snapshot update requires all required sections.');
                for (const key of Object.keys(sections))
                    delete sections[key];
                for (const field of ['anchors', 'tags', 'search_terms', 'source_refs'])
                    if (!Object.hasOwn(values, field))
                        delete fm[field];
            }
            for (const [key, value] of Object.entries(operation.sections ?? {})) {
                const name = (0, documents_1.sectionName)(fm.schema, key);
                (0, common_1.check)(allowed[r.kind].includes(name), 'immutable_primary', 'Primary meaning changes require a successor.', { section: name }, common_1.EXIT.conflict);
                Object.assign(sections, (0, documents_1.existingStyle)(fm.schema, { [name]: value }, r.document.sections));
            }
            const metadataAllowed = operation.action === 'annotate' ? ['title', 'summary', 'tags', 'search_terms', 'source_refs', ...(r.kind === 'observation' ? ['related'] : [])] : r.kind === 'snapshot' ? ['title', 'summary', 'tags', 'search_terms', 'source_refs', 'anchors'] : [];
            (0, common_1.check)(Object.keys(values).every(k => metadataAllowed.includes(k)), 'immutable_field', 'Mutation contains immutable or unknown fields.', {}, common_1.EXIT.conflict);
            Object.assign(fm, Object.fromEntries(Object.entries(values).filter(([k]) => k !== 'related')));
            if (values.related)
                fm.relations = { ...fm.relations, related: values.related };
            for (const key of operation.clear ?? []) {
                (0, common_1.check)(['tags', 'search_terms', 'source_refs', ...(r.kind === 'snapshot' ? ['anchors'] : [])].includes(key), 'usage_invalid', 'Invalid clear target.');
                delete fm[key];
            }
            if (!['decision', 'observation'].includes(r.kind) && (0, documents_1.renderDocument)(fm, sections) !== r.content)
                fm.updated_at = (0, common_1.timestamp)(operation.now);
        }
        return [change((0, documents_1.renderDocument)(fm, sections))];
    }
    checkChanges(changes, areas, operation) {
        const overlay = new Map();
        (0, common_1.check)(new Set(changes.map(c => c.path)).size === changes.length, 'plan_conflict', 'Batch operations cannot mutate the same path more than once.', {}, common_1.EXIT.conflict);
        for (const c of changes) {
            (0, filesystem_1.contained)(this.vault, c.path);
            (0, common_1.check)(c.after_digest === (c.content === null ? null : (0, common_1.sha256)((0, common_1.fileBytes)(c.content))), 'approval_digest_mismatch', 'Rendered material bytes changed.', {}, common_1.EXIT.conflict);
            (0, common_1.check)((0, filesystem_1.digestOrNull)(this.vault, c.path) === c.before_digest, 'stale_input', 'Target content changed after preview.', { path: c.path }, common_1.EXIT.conflict);
            overlay.set(c.path, c.content);
        }
        for (const c of changes.filter(c => c.content !== null && c.before_digest === null)) {
            const directory = path.posix.dirname(c.path), basename = path.posix.basename(c.path);
            if (fs.existsSync((0, filesystem_1.contained)(this.vault, directory)))
                for (const name of fs.readdirSync((0, filesystem_1.contained)(this.vault, directory))) {
                    const existing = directory + '/' + name;
                    if (overlay.get(existing) === null)
                        continue;
                    (0, common_1.check)((0, common_1.normalizedKey)(name) !== (0, common_1.normalizedKey)(basename), 'path_exists', 'A collision-equivalent path already exists.', { path: c.path }, common_1.EXIT.conflict);
                }
        }
        const records = (0, catalog_1.scanRecords)(this.vault, areas, overlay);
        (0, catalog_1.validateRelations)(records);
        const acknowledged = (operation.action === 'batch' ? operation.operations : [operation]).flatMap(op => op.action === 'capture' ? op.acknowledgements ?? [] : []);
        (0, catalog_1.validateSlots)(records, acknowledged, new Set(records.filter(r => overlay.has(r.path)).map(r => r.row.id)));
        return records;
    }
    async prepareSameClaim(predecessorId, successor) {
        return this.locked(() => {
            const r = (0, catalog_1.findRecord)(this.vault, predecessorId), draft = (0, owners_1.draftCapture)(successor.candidate, successor.attestation, successor);
            (0, common_1.check)(successor.id, 'id_required', 'Allocate the successor ID before assessing same_claim.');
            return this.sameClaimInput(r, draft.document, successor.candidate);
        });
    }
    sameClaimInput(record, successor, candidate) { return { schema: 'bobbin-same-claim-input/v1', predecessor: { id: record.row.id, path: record.path, sha256: (0, common_1.sha256)(Buffer.from(record.content)), primary_claim: (0, owners_1.primaryClaim)(record.document), scope: record.document.frontmatter.scope ?? null, sections: record.document.sections }, successor: { id: successor.frontmatter.id, primary_claim: (0, owners_1.primaryClaim)(successor), scope: successor.frontmatter.scope ?? null, candidate_digest: (0, common_1.canonicalDigest)(candidate) } }; }
    async preview(input) {
        return this.locked(() => {
            const operation = this.normalizeOperation(input), areas = (0, catalog_1.registeredAreas)(this.vault), changes = this.operationChanges(operation, areas);
            const records = this.checkChanges(changes, areas, operation);
            const changedPaths = new Set(changes.map(c => c.path)), ids = new Set();
            for (const record of records)
                if (changedPaths.has(record.path)) {
                    for (const id of (0, catalog_1.referenceIds)(record))
                        ids.add(id);
                }
            for (const op of operation.action === 'batch' ? operation.operations : [operation])
                if (op.action === 'capture')
                    for (const id of op.acknowledgements ?? [])
                        ids.add(id);
            const read_preconditions = (0, catalog_1.scanRecords)(this.vault, areas).filter(r => ids.has(r.row.id) && !changedPaths.has(r.path)).map(r => ({ path: r.path, sha256: (0, common_1.sha256)(Buffer.from(r.content)) })).sort((a, b) => (0, common_1.compareText)(a.path, b.path));
            const base = { schema: 'bobbin-preview/v1', plan_id: (0, common_1.newPlanId)(), vault_identity: (0, filesystem_1.identity)(this.vault), project_policy: this.policyBinding(), operation, changes, read_preconditions };
            (0, common_1.check)(Buffer.byteLength((0, common_1.canonicalJson)(base)) <= (operation.action === 'capture' && operation.candidate.requested_kind === 'archive' ? 1024 * 1024 : 128 * 1024), 'approval_preview_too_large', 'Preview exceeds the mutation byte budget.');
            return { ...base, approval_digest: previewDigest(base), state: 'awaiting_approval', applied: false };
        }, true);
    }
    async apply(preview, authorization) {
        return this.locked(() => {
            (0, common_1.exact)(preview, ['schema', 'plan_id', 'vault_identity', 'project_policy', 'operation', 'changes', 'read_preconditions', 'approval_digest', 'state', 'applied'], 'bundle_invalid');
            const { approval_digest, state, applied, ...base } = preview;
            (0, common_1.check)(preview.schema === 'bobbin-preview/v1' && state === 'awaiting_approval' && applied === false && previewDigest(base) === approval_digest, 'approval_digest_mismatch', 'Frozen preview changed; prepare a new preview.', {}, common_1.EXIT.conflict);
            for (const c of preview.changes)
                (0, common_1.check)(c.after_digest === (c.content === null ? null : (0, common_1.sha256)((0, common_1.fileBytes)(c.content))), 'approval_digest_mismatch', 'Rendered material bytes changed.', {}, common_1.EXIT.conflict);
            (0, common_1.check)((0, common_1.canonicalDigest)(preview.vault_identity) === (0, common_1.canonicalDigest)((0, filesystem_1.identity)(this.vault)), 'vault_identity_mismatch', 'Preview belongs to another vault.', {}, common_1.EXIT.conflict);
            (0, common_1.check)((0, common_1.canonicalDigest)(preview.project_policy) === (0, common_1.canonicalDigest)(this.policyBinding()), 'project_policy_changed', 'Project or recording policy changed after preview.', {}, common_1.EXIT.conflict);
            for (const precondition of preview.read_preconditions)
                (0, common_1.check)((0, filesystem_1.digestOrNull)(this.vault, precondition.path) === precondition.sha256, 'stale_reference', 'Referenced content changed after preview.', { path: precondition.path }, common_1.EXIT.conflict);
            const areas = (0, catalog_1.registeredAreas)(this.vault), settings = loadSettings(this.project);
            const touched = [...new Set(preview.changes.map(c => c.path.split('/')[1]))];
            (0, common_1.check)(!settings.enabled || touched.every(k => settings.enabled.includes(k)), 'feature_disabled', 'Enable this feature before recording.', { areas: touched }, common_1.EXIT.conflict);
            (0, common_1.check)(authorization?.source === 'user' || authorization?.source === 'policy', 'approval_required', 'Provide user or configured policy authorization.', {}, common_1.EXIT.conflict);
            if (authorization.source === 'policy') {
                (0, common_1.check)(settings.config && settings.mode !== 'explicit', 'approval_required', 'Explicit mode requires user authorization.', {}, common_1.EXIT.conflict);
                (0, common_1.check)((0, filesystem_1.realDirectory)(path.resolve(this.project, settings.config.vault)) === this.vault, 'vault_policy_mismatch', 'Automatic recording is limited to the configured vault.', {}, common_1.EXIT.conflict);
                if (settings.mode === 'adaptive') {
                    (0, common_1.check)(['record', 'ask'].includes(authorization.decision ?? '') && typeof authorization.reason === 'string' && authorization.reason.trim() && authorization.reason.length <= 1000, 'policy_assessment_required', 'Adaptive mode requires a record/ask decision and reason.', {}, common_1.EXIT.conflict);
                    (0, common_1.check)(authorization.decision === 'record', 'approval_required', authorization.reason, {}, common_1.EXIT.conflict);
                }
            }
            if (preview.changes.every(c => (0, filesystem_1.digestOrNull)(this.vault, c.path) === (c.content === null ? null : (0, common_1.sha256)((0, common_1.fileBytes)(c.content)))))
                return { applied: false, already_applied: true, plan_id: preview.plan_id, approval_digest, changed_paths: [], index_paths: [], warnings: [], authorization: { ...authorization, mode: settings.mode } };
            const records = this.checkChanges(preview.changes, areas, preview.operation), fresh = this.operationChanges(preview.operation, areas);
            (0, common_1.check)((0, common_1.canonicalDigest)(fresh) === (0, common_1.canonicalDigest)(preview.changes), 'bundle_invalid', 'Preview differs from validated owner output.', {}, common_1.EXIT.conflict);
            const changes = preview.changes.map(c => ({ path: c.path, content: c.content === null ? null : (0, common_1.fileBytes)(c.content), expected: c.before_digest })), warnings = [];
            for (const area of areas.filter(a => touched.includes(a.row.area))) {
                const current = (0, catalog_1.scanRecords)(this.vault, [area]), canonicalBefore = (0, catalog_1.rebuildArea)(area, current);
                if (canonicalBefore !== area.text)
                    warnings.push('index_regenerated:' + area.row.path);
                changes.push({ path: area.row.path, content: (0, common_1.fileBytes)((0, catalog_1.rebuildArea)(area, records)), expected: (0, common_1.sha256)(Buffer.from(area.text)) });
            }
            const changed = this.storage.transaction(changes);
            return { applied: true, plan_id: preview.plan_id, approval_digest, changed_paths: changed, index_paths: changes.filter(c => c.path.endsWith('.index.md')).map(c => c.path), warnings, authorization: { ...authorization, mode: settings.mode } };
        }, true);
    }
    async refresh(fix = false) {
        return this.locked(() => {
            const recovered = [];
            if (fix && !(0, filesystem_1.bytes)(this.vault, catalog_1.ROOT_INDEX)) {
                const directory = (0, filesystem_1.contained)(this.vault, 'context');
                (0, common_1.check)(fs.existsSync(directory), 'context_root_missing', 'No indexes are available for recovery.', {}, common_1.EXIT.notFound);
                const areas = [];
                for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
                    if (!entry.isDirectory() || !(/^[a-z][a-z0-9_-]{0,79}$/.test(entry.name)))
                        continue;
                    const relative = `context/${entry.name}/${entry.name}.index.md`, raw = (0, filesystem_1.bytes)(this.vault, relative);
                    if (!raw)
                        continue;
                    const text = (0, filesystem_1.utf8)(raw), index = (0, documents_1.parseAreaIndex)(text), fm = index.frontmatter;
                    areas.push({ row: { area: fm.area, path: relative, owner: fm.owner, claims: [fm.area], artifact_schema: fm.artifact_schema, authority: fm.authority }, metadata: fm, descriptor: index.descriptor, text });
                }
                (0, common_1.check)(areas.length > 0, 'context_root_missing', 'No valid area indexes are available for recovery.', {}, common_1.EXIT.notFound);
                (0, catalog_1.scanRecords)(this.vault, areas);
                recovered.push(...this.storage.transaction([{ path: catalog_1.ROOT_INDEX, content: (0, common_1.fileBytes)((0, catalog_1.renderRoot)(areas)), expected: null }]));
            }
            const areas = (0, catalog_1.registeredAreas)(this.vault), records = (0, catalog_1.scanRecords)(this.vault, areas), issues = [], changes = [];
            try {
                (0, catalog_1.validateRelations)(records);
                (0, catalog_1.validateSlots)(records);
            }
            catch (e) {
                if (e instanceof common_1.BobbinError)
                    issues.push({ code: e.code, ...e.details });
                else
                    throw e;
            }
            for (const area of areas) {
                const text = (0, catalog_1.rebuildArea)(area, records);
                if (text !== area.text) {
                    issues.push({ code: 'index_content_drift', path: area.row.path });
                    changes.push({ path: area.row.path, content: (0, common_1.fileBytes)(text), expected: (0, common_1.sha256)(Buffer.from(area.text)) });
                }
            }
            const root = (0, filesystem_1.readText)(this.vault, catalog_1.ROOT_INDEX), renderedRoot = (0, catalog_1.renderRoot)(areas, root);
            if (root !== renderedRoot) {
                issues.push({ code: 'index_content_drift', path: catalog_1.ROOT_INDEX });
                changes.push({ path: catalog_1.ROOT_INDEX, content: (0, common_1.fileBytes)(renderedRoot), expected: (0, common_1.sha256)(Buffer.from(root)) });
            }
            (0, common_1.check)(!fix || issues.every(i => i.code === 'index_content_drift'), 'integrity_error', 'Resolve record integrity errors before rebuilding indexes.', { issues }, common_1.EXIT.integrity);
            const changed = fix ? [...recovered, ...this.storage.transaction(changes)] : [];
            return { ok: issues.length === 0 || fix, issues, changed_paths: changed, record_count: records.length, index_fixed: fix };
        });
    }
    async recall(options = {}) { return this.locked(() => this.recallUnlocked(options)); }
    async search(kind, options = {}) {
        return this.locked(() => {
            const signal = kind === 'assumption' ? 'assumption-relevant' : kind === 'term' ? 'term-encountered' : null;
            if (signal)
                (0, common_1.check)(options.signal === signal, 'signal_required', 'Specialized recall requires a relevant signal.', { required: signal }, common_1.EXIT.conflict);
            const limit = options.limit ?? (kind === 'decision' ? 8 : 20);
            (0, common_1.check)(Number.isSafeInteger(limit) && limit >= 1 && limit <= (kind === 'decision' ? 20 : 50), 'usage_invalid', 'Search limit is outside the owner budget.');
            const area = (0, catalog_1.registeredAreas)(this.vault).find(a => a.row.area === kind);
            (0, common_1.check)(area, 'area_not_registered', 'Requested area is not registered.', {}, common_1.EXIT.notFound);
            const index = (0, documents_1.parseAreaIndex)(area.text), needle = (0, common_1.normalizedKey)(options.query ?? '').trim();
            const rows = [...index.current, ...(options.includeHistory ? index.history : [])].filter(r => (!options.scope || r.scope === options.scope) && (!options.key || r[kind + '_key'] === options.key));
            const fields = kind === 'decision' ? ['id', 'title', 'summary', 'path', 'scope', 'decision_key'] : kind === 'intent' ? ['title', 'summary', 'scope', 'intent_key'] : kind === 'document' ? ['title', 'summary', 'scope', 'document_key'] : ['title', 'summary', 'scope'];
            const selected = rows.filter(r => !needle || (0, common_1.normalizedKey)([...fields.map(f => r[f] ?? ''), ...(r.terms ?? [])].join(' ')).includes(needle)).sort((a, b) => (0, common_1.compareText)(b.updated_at ?? b.created_at, a.updated_at ?? a.created_at) || (0, common_1.compareText)(b.id, a.id));
            const items = selected.slice(0, limit).map(row => ({ ...row, kind, authority: row.state === 'history' ? 'historical' : area.row.authority, do_not_follow: row.state === 'history' }));
            return { schema: `context-${kind}-search/v1`, items, returned: items.length, omitted: selected.length - items.length, truncated: selected.length > items.length, metadata_only: true, ...(signal ? { signal } : {}), physical_write: false };
        });
    }
    async checkDecision(options) { return this.locked(() => (0, decision_1.prepareDecisionCheck)(this.vault, options)); }
    async specView(scope, maxBytes) { return this.locked(() => (0, decision_1.decisionSpecView)(this.vault, scope, maxBytes)); }
    async revisitDecisions(options = {}) { return this.locked(() => { const area = (0, catalog_1.registeredAreas)(this.vault).find(a => a.row.area === 'decision'); (0, common_1.check)(area, 'area_not_registered', 'Decision owner is not initialized.'); const today = options.asOf ?? new Date().toISOString().slice(0, 10), rows = (0, documents_1.parseAreaIndex)(area.text).current.filter(r => (!options.ids?.length || options.ids.includes(r.id)) && (!options.due || (r.revisit_on && r.revisit_on <= today))); return { items: rows.map(r => this.readResult((0, catalog_1.findRecord)(this.vault, r.id))), as_of: today, returned: rows.length, physical_write: false }; }); }
    recallUnlocked(options) {
        const settings = loadSettings(this.project), query = options.query ?? '', limit = options.limit ?? 8, expanded = !!(options.pack || options.sections?.length || options.readIds?.length), budget = options.maxBytes ?? (expanded ? 8192 : 4096);
        (0, common_1.check)(limit >= 1 && limit <= 20 && budget >= 1 && budget <= 32768, 'usage_invalid', 'Recall limit or byte budget is outside the supported range.');
        const areas = (0, catalog_1.registeredAreas)(this.vault).filter(a => (a.row.area !== 'archive' || options.includeArchive) && (options.areas?.length ? options.areas.includes(a.row.area) : !settings.enabled || settings.enabled.includes(a.row.area)));
        let all = [], fallback = false, remaining = 20;
        const warnings = [];
        const fallbackArea = (area, exclude = new Set()) => {
            const paths = (0, catalog_1.listArtifactPaths)(this.vault, area.row.area, options.includeHistory).filter(p => !exclude.has(p)), found = [];
            for (const relative of paths.slice(0, remaining)) {
                const doc = (0, documents_1.parseDocument)((0, filesystem_1.readText)(this.vault, relative), area.descriptor);
                found.push({ row: (0, catalog_1.projectEntry)(area, relative, doc), area });
            }
            remaining -= found.length;
            if (paths.length > found.length)
                warnings.push('index_fallback_truncated');
            return found;
        };
        for (const area of areas) {
            try {
                const parsed = (0, documents_1.parseAreaIndex)(area.text);
                all.push(...[...parsed.current, ...(options.includeHistory ? parsed.history : [])].map(row => ({ row, area })));
            }
            catch (e) {
                if (options.strictIndex)
                    throw e;
                fallback = true;
                warnings.push('area_index_invalid');
                all.push(...fallbackArea(area));
            }
        }
        const tokens = [...new Set((0, common_1.normalizedKey)(query).match(/[\p{L}\p{N}_]+(?:[.-][\p{L}\p{N}_]+)*/gu) ?? [])], minimum = Math.min(2, Math.ceil(tokens.length / 2));
        const matched = (entries) => entries.map(x => ({ ...x, ...score(x.row, query) })).filter(x => (!options.readIds?.length || options.readIds.includes(x.row.id)) && (!query || (x.score > 0 && x.matched >= minimum)) && (options.facets ?? []).every(([key, val]) => Array.isArray(x.row[key]) ? x.row[key].some((v) => (0, common_1.normalizedKey)(v) === (0, common_1.normalizedKey)(val)) : typeof x.row[key] === 'string' && (0, common_1.normalizedKey)(x.row[key]) === (0, common_1.normalizedKey)(val)));
        let filtered = matched(all);
        if (query && !filtered.length && !fallback && !options.strictIndex) {
            const recovered = areas.flatMap(area => fallbackArea(area, new Set(all.filter(x => x.area === area).map(x => x.row.path))));
            if (recovered.length) {
                fallback = true;
                warnings.push('index_miss_fallback');
                filtered = matched(recovered);
            }
        }
        if (query && filtered.some(x => x.strong > 0))
            filtered = filtered.filter(x => x.strong > 0);
        filtered.sort((a, b) => b.score - a.score || (0, common_1.compareText)(b.row.created_at, a.row.created_at) || (0, common_1.compareText)(a.row.id, b.row.id));
        const output = [], result = (items) => ({ items, returned: items.length, omitted: Math.max(0, filtered.length - items.length), truncated: filtered.length > items.length, index_fallback: fallback, warnings: [...new Set(warnings)].sort() });
        for (const { row, area, score: points } of filtered.slice(0, limit)) {
            let item = { id: row.id, kind: area.row.area, state: row.state, title: row.title, summary: row.summary, path: row.path, authority: row.state === 'history' ? 'historical' : area.row.authority, do_not_follow: row.state === 'history', score: points };
            for (const key of area.metadata.projection_fields ?? [])
                if (Object.hasOwn(row, key))
                    item[key] = row[key];
            if (expanded) {
                const document = (0, documents_1.parseDocument)((0, filesystem_1.readText)(this.vault, row.path), area.descriptor);
                warnings.push(...document.warnings.map(x => x.code));
                const names = options.sections?.length ? options.sections : Object.keys(document.sections).map(k => (0, documents_1.sectionName)(document.frontmatter.schema, k)), available = Object.fromEntries(names.map(k => [k, (0, documents_1.sectionValue)(document, k)]));
                const overhead = Buffer.byteLength((0, common_1.canonicalJson)(result([...output, {}]))) - 2;
                try {
                    item = fitSections(item, available, Math.min(2048, Math.min(budget, 8192) - overhead), {}, { section_truncated: true, full_read_hint: `context recall --read ${row.id}` });
                }
                catch (e) {
                    if (e instanceof common_1.BobbinError && e.code === 'usage_invalid')
                        break;
                    throw e;
                }
            }
            if (Buffer.byteLength((0, common_1.canonicalJson)(result([...output, item]))) > budget)
                break;
            output.push(item);
        }
        return result(output);
    }
}
exports.Bobbin = Bobbin;
function createBobbin(options) { return new Bobbin(options); }
function fitSections(base, available, maximum, completeFields, truncatedFields) {
    const size = (x) => Buffer.byteLength((0, common_1.canonicalJson)(x)), complete = { ...base, sections: available, ...completeFields };
    if (size(complete) <= maximum)
        return complete;
    const result = { ...base, sections: {}, ...truncatedFields };
    (0, common_1.check)(size(result) <= maximum, 'usage_invalid', 'maxBytes is too small for the metadata envelope.', { minimum_bytes: size(result), max_bytes: maximum });
    for (const [key, value] of Object.entries(available)) {
        if (size({ ...result, sections: { ...result.sections, [key]: value } }) <= maximum) {
            result.sections[key] = value;
            continue;
        }
        const chars = [...value];
        let low = 0, high = chars.length, fit = '';
        while (low <= high) {
            const mid = Math.floor((low + high) / 2), candidate = chars.slice(0, mid).join('').trimEnd() + '…';
            if (size({ ...result, sections: { ...result.sections, [key]: candidate } }) <= maximum) {
                fit = candidate;
                low = mid + 1;
            }
            else
                high = mid - 1;
        }
        if (fit)
            result.sections[key] = fit;
        break;
    }
    return result;
}
function score(row, query) {
    const q = (0, common_1.normalizedKey)(query.trim());
    if (!q)
        return { score: 0, matched: 0, strong: 0 };
    const tokens = [...new Set(q.match(/[\p{L}\p{N}_]+(?:[.-][\p{L}\p{N}_]+)*/gu) ?? [])], title = (0, common_1.normalizedKey)(row.title ?? ''), summary = (0, common_1.normalizedKey)(row.summary ?? ''), relative = (0, common_1.normalizedKey)(row.path ?? ''), terms = (row.terms ?? []).map(common_1.normalizedKey), exact = q === (0, common_1.normalizedKey)(row.id ?? '');
    let points = exact ? 100 : 0, matched = 0, strong = 0;
    if (title.includes(q))
        points += 40;
    if (summary.includes(q))
        points += 10;
    if (terms.includes(q))
        points += 12;
    for (const t of tokens) {
        const p = title.includes(t) ? 8 : terms.some((x) => x.includes(t)) ? 6 : summary.includes(t) ? 3 : relative.includes(t) ? 1 : 0;
        if (p) {
            points += p;
            matched++;
            if (p > 1)
                strong++;
        }
    }
    if (exact) {
        matched = Math.max(matched, tokens.length);
        strong = Math.max(strong, tokens.length);
    }
    const all = [title, summary, relative, ...terms].join(' ');
    if (tokens.length && tokens.every(t => all.includes(t)))
        points += 10;
    return { score: points, matched, strong };
}
