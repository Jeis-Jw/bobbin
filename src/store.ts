import * as fs from 'node:fs';
import * as path from 'node:path';
import { ObjectValue, contracts, check, fail, exact, object, canonicalDigest, canonicalJson, compactJson, strictJson, newPlanId, newId, timestamp, sha256, fileBytes, filename, normalizedKey, compareText, shortText, stringList, EXIT, VERSION, BobbinError, toBobbinError, withErrors } from './common';
import { Kind, kinds, ContextDocument, parseDocument, renderDocument, sectionValue, sectionName, existingStyle, parseAreaIndex, validateDescriptor } from './documents';
import { Candidate, Attestation, DraftOptions, draftCapture, validateAttestation, primaryClaim, sectionFields } from './owners';
import { Filesystem, FileChange, identity, realDirectory, contained, bytes, readText, utf8, digestOrNull } from './filesystem';
import { ROOT_INDEX, Area, RecordEntry, registeredAreas, newArea, renderRoot, scanRecords, findRecord, rebuildArea, projectEntry, validateRelations, validateSlots, listArtifactPaths, referenceIds } from './catalog';
import { DecisionCheckOptions, DecisionCompareOptions, prepareDecisionCheck, prepareDecisionCompare, decisionSpecView } from './decision';
import { runtimeDigest } from './runtime';
import { CandidateBatch, routeCandidates, operationFromOwnerResult } from './routing';
import { SNAP_TRANSPORT_MAX_BYTES } from './snapshot';
export type ApprovalMode = 'explicit' | 'auto' | 'adaptive';
export type Feature = 'decision' | 'assumption' | 'term' | 'intent' | 'document';
export interface BobbinOptions {
    vault?: string;
    project?: string;
    lockTimeoutMs?: number;
}
export interface InitializeOptions {
    features?: Feature[];
    approvalMode?: ApprovalMode;
    host?: 'codex' | 'claude-code';
}
export interface UserAuthorization {
    source: 'user';
}
export interface PolicyAuthorization {
    source: 'policy';
    decision?: 'record' | 'ask';
    reason?: string;
}
export type Authorization = UserAuthorization | PolicyAuthorization;
export interface CaptureOperation extends DraftOptions {
    action: 'capture';
    candidate: Candidate;
    attestation: Attestation;
    acknowledgements?: string[];
}
export interface SupersedeOperation extends DraftOptions {
    action: 'supersede';
    id: string;
    successor: CaptureOperation;
    sameClaim: Attestation;
    now?: string;
}
export interface MutationOperation {
    action: 'update' | 'annotate' | 'retire' | 'reverify' | 'rename' | 'discard';
    id: string;
    values?: ObjectValue;
    sections?: Record<string, string>;
    now?: string;
    filename?: string;
    merge?: boolean;
    clear?: string[];
}
export interface BatchOperation {
    action: 'batch';
    operations: (CaptureOperation | SupersedeOperation | MutationOperation)[];
}
export type Operation = CaptureOperation | SupersedeOperation | MutationOperation | BatchOperation;
interface PreviewChange {
    path: string;
    before_digest: string | null;
    after_digest: string | null;
    content: string | null;
}
export interface Preview {
    schema: 'bobbin-preview/v1';
    plan_id: string;
    vault_identity: ObjectValue;
    project_policy: ObjectValue;
    operation: Operation;
    changes: PreviewChange[];
    read_preconditions: {
        path: string;
        sha256: string;
    }[];
    approval_digest: string;
    state: 'awaiting_approval';
    applied: false;
}
export interface ReadOptions {
    sections?: string[];
    maxBytes?: number;
}
export interface ReadResult {
    id: string;
    kind: string;
    path: string;
    state: 'current' | 'history';
    frontmatter: ObjectValue;
    authority: string;
    do_not_follow: boolean;
    lifecycle_reason: string | null;
    sections: Record<string, string>;
    warnings: ObjectValue[];
    truncated: boolean;
    physical_write: false;
    use_as?: string;
    freshness?: 'authority_unknown' | 'anchored' | 'anchor_changed';
    full_read_hint?: string;
}
export interface ApplyResult {
    applied: boolean;
    already_applied?: boolean;
    plan_id: string;
    approval_digest: string;
    changed_paths: string[];
    index_paths: string[];
    warnings: string[];
    authorization: Authorization & {
        mode: ApprovalMode;
    };
}
export interface RecallOptions {
    query?: string;
    areas?: string[];
    includeHistory?: boolean;
    includeArchive?: boolean;
    facets?: [
        string,
        string
    ][];
    limit?: number;
    pack?: boolean;
    sections?: string[];
    readIds?: string[];
    strictIndex?: boolean;
    maxBytes?: number;
}
export interface SearchOptions {
    query?: string;
    scope?: string;
    key?: string;
    includeHistory?: boolean;
    limit?: number;
    signal?: 'assumption-relevant' | 'term-encountered';
}
interface Settings {
    project: string;
    config: ObjectValue | null;
    digest: string | null;
    mode: ApprovalMode;
    enabled: string[] | null;
}
const FEATURES: Feature[] = ['decision', 'assumption', 'term', 'intent', 'document'];
const BUILTINS = ['snapshot', 'observation', 'archive'];
function loadSettings(project: string): Settings {
    const raw = bytes(project, '.bobbin/config.json', 8192);
    if (!raw)
        return { project, config: null, digest: null, mode: 'explicit', enabled: null };
    const config = strictJson(utf8(raw), 'config_invalid');
    exact(config, ['schema', 'features', 'approval', 'vault'], 'config_invalid');
    exact(config.approval, ['mode'], 'config_invalid');
    check(config.schema === 'bobbin-project/v1' && Array.isArray(config.features) && config.features.every((x: string) => FEATURES.includes(x as Feature)) && new Set(config.features).size === config.features.length && ['explicit', 'auto', 'adaptive'].includes(config.approval.mode) && typeof config.vault === 'string' && !!config.vault && !config.vault.includes('\0'), 'config_invalid', 'Invalid Bobbin project configuration.');
    return { project, config, digest: sha256(raw), mode: config.approval.mode, enabled: [...BUILTINS, ...config.features] };
}
function previewDigest(preview: Omit<Preview, 'approval_digest' | 'state' | 'applied'>): string { return canonicalDigest(preview); }
export class Bobbin {
    readonly vault: string;
    readonly project: string;
    private readonly storage: Filesystem;
    private readonly projectStorage: Filesystem;
    constructor(options: BobbinOptions) {
        try {
            check(options.vault || options.project, 'usage_invalid', 'Provide a vault or project directory.');
            this.project = realDirectory(options.project ?? options.vault!);
            const settings = loadSettings(this.project);
            this.vault = realDirectory(options.vault ?? path.resolve(this.project, settings.config?.vault ?? '.'));
            this.storage = new Filesystem(this.vault, options);
            this.projectStorage = new Filesystem(this.project, options);
        }
        catch (error) {
            throw toBobbinError(error);
        }
    }
    private async locked<T>(fn: () => T | Promise<T>, projectToo = false): Promise<T> {
        try {
            if (!projectToo || this.project === this.vault)
                return await this.storage.locked(fn);
            const [first, second] = this.project < this.vault ? [this.projectStorage, this.storage] : [this.storage, this.projectStorage];
            return await first.locked(() => second.locked(fn));
        }
        catch (error) {
            throw toBobbinError(error);
        }
    }
    private policyBinding(): ObjectValue { const settings = loadSettings(this.project); return { project_identity: identity(this.project), config_digest: settings.digest, runtime: 'bobbin-typescript/v1', runtime_digest: runtimeDigest, owners: registeredAreas(this.vault).map(a => ({ area: a.row.area, descriptor: a.descriptor ?? a.row })) }; }
    adoptLegacy(confirmLegacyStopped: boolean): ObjectValue {
        return withErrors(() => {
            const roots = this.project === this.vault ? [this.storage] : [this.storage, this.projectStorage].sort((a, b) => compareText(a.root, b.root));
            const adopted = roots.map(root => root.adoptLegacy(confirmLegacyStopped).vault);
            return { adopted: true, vault: this.vault, project: this.project, adopted_roots: adopted, protocol: 'exclusive-node-writer/v1', records_changed: false };
        });
    }
    async recoverRuntime(): Promise<ObjectValue> {
        try {
            const roots = this.project === this.vault ? [this.storage] : [this.storage, this.projectStorage].sort((a, b) => compareText(a.root, b.root));
            const recovered: string[] = [];
            for (const root of roots) {
                await root.recoverAbandoned();
                recovered.push(root.root);
            }
            return { recovered: true, vault: this.vault, project: this.project, recovered_roots: recovered };
        }
        catch (error) {
            throw toBobbinError(error);
        }
    }
    settings(): ObjectValue { return withErrors(() => { const settings = loadSettings(this.project); return { project: this.project, vault: this.vault, config: settings.config, digest: settings.digest, mode: settings.mode, enabled: settings.enabled, registered_features: bytes(this.vault, ROOT_INDEX) ? registeredAreas(this.vault).map(a => a.row.area).filter(k => FEATURES.includes(k)) : [] }; }); }
    route(batch: CandidateBatch | Candidate[], results: ObjectValue[] | ObjectValue): ObjectValue { return withErrors(() => routeCandidates(batch, results, loadSettings(this.project).enabled)); }
    async previewOwnerResult(result: ObjectValue): Promise<Preview> { return this.preview(operationFromOwnerResult(result)); }
    async validate(value: Preview | Operation | ObjectValue): Promise<ObjectValue> {
        const input = value as ObjectValue;
        check(object(input), 'usage_invalid', 'Validation input must be an object.');
        if (input.schema === 'bobbin-preview/v1') {
            const { approval_digest, state, applied, ...base } = input;
            check(state === 'awaiting_approval' && applied === false && previewDigest(base as any) === approval_digest, 'approval_digest_mismatch', 'Preview bytes changed.', {}, EXIT.conflict);
            const current = await this.preview(input.operation);
            check(canonicalDigest(current.vault_identity) === canonicalDigest(input.vault_identity) && canonicalDigest(current.project_policy) === canonicalDigest(input.project_policy) && canonicalDigest(current.changes) === canonicalDigest(input.changes) && canonicalDigest(current.read_preconditions) === canonicalDigest(input.read_preconditions), 'stale_input', 'Preview no longer matches the current vault and runtime.', {}, EXIT.conflict);
            return { status: 'valid', approval_digest, physical_write: false };
        }
        const preview = await this.preview(input.schema === 'context-owner-result/v1' ? operationFromOwnerResult(input) : input as Operation);
        return { status: 'valid', approval_digest: preview.approval_digest, physical_write: false };
    }
    async registerArea(descriptor: ObjectValue, indexSeed: string): Promise<ObjectValue> {
        return this.locked(() => {
            validateDescriptor(descriptor);
            const seed = parseAreaIndex(indexSeed);
            check(seed.current.length === 0 && seed.history.length === 0, 'index_seed_invalid', 'Registration seed must have empty generated blocks.');
            const kind = descriptor.kind;
            check(seed.frontmatter.area === kind && seed.frontmatter.owner === descriptor.owner && seed.frontmatter.artifact_schema === descriptor.artifact_schema && seed.frontmatter.authority === descriptor.authority && (descriptor.schema !== 'context-owner-descriptor/v2' || canonicalDigest(seed.descriptor) === canonicalDigest(descriptor)), 'index_seed_invalid', 'Seed differs from descriptor.');
            const areas = registeredAreas(this.vault), existing = areas.find(a => a.row.area === kind);
            if (existing) {
                check(canonicalDigest(existing.descriptor ?? {}) === canonicalDigest(descriptor), 'owner_descriptor_conflict', 'An immutable registered descriptor cannot be replaced.', {}, EXIT.conflict);
                return { applied: false, changed_paths: [] };
            }
            const area: Area = { row: { area: kind, path: `context/${kind}/${kind}.index.md`, owner: descriptor.owner, claims: [kind], artifact_schema: descriptor.artifact_schema, authority: descriptor.authority }, metadata: seed.frontmatter, descriptor, text: indexSeed };
            check(bytes(this.vault, area.row.path) === null, 'owner_descriptor_conflict', 'An unregistered area index already exists.', {}, EXIT.conflict);
            const changed = this.storage.transaction([{ path: area.row.path, content: fileBytes(indexSeed), expected: null }, { path: ROOT_INDEX, content: fileBytes(renderRoot([...areas, area], readText(this.vault, ROOT_INDEX))), expected: digestOrNull(this.vault, ROOT_INDEX) }]);
            return { applied: changed.length > 0, changed_paths: changed, descriptor };
        });
    }
    async initialize(options: InitializeOptions = {}): Promise<ObjectValue> {
        return this.locked(() => {
            const before = loadSettings(this.project), hasRoot = !!bytes(this.vault, ROOT_INDEX), areas = hasRoot ? registeredAreas(this.vault) : [], byKind = new Map(areas.map(a => [a.row.area, a]));
            const features = options.features ?? before.config?.features ?? (hasRoot ? FEATURES.filter(k => byKind.has(k)) : ['decision']);
            check(Array.isArray(features) && features.every((x: string) => FEATURES.includes(x as Feature)) && new Set(features).size === features.length, 'config_invalid', 'Invalid feature selection.');
            const mode = options.approvalMode ?? before.mode;
            check(['explicit', 'auto', 'adaptive'].includes(mode), 'config_invalid', 'Invalid approval mode.');
            for (const kind of [...BUILTINS, ...features])
                if (!byKind.has(kind)) {
                    const a = newArea(kind);
                    check(bytes(this.vault, a.row.path) === null, 'area_exists', 'Area index already exists outside the root registry.', { area: kind }, EXIT.conflict);
                    byKind.set(kind, a);
                }
            const selected = [...byKind.values()], changes: FileChange[] = [];
            for (const a of selected)
                if (!areas.includes(a))
                    changes.push({ path: a.row.path, content: fileBytes(a.text), expected: null });
            const seed = hasRoot ? readText(this.vault, ROOT_INDEX) : contracts.root_seed;
            const rootText = renderRoot(selected, seed);
            changes.push({ path: ROOT_INDEX, content: fileBytes(rootText), expected: digestOrNull(this.vault, ROOT_INDEX) });
            const config = { schema: 'bobbin-project/v1', features, approval: { mode }, vault: path.relative(this.project, this.vault) || '.' };
            const projectChanges: FileChange[] = [];
            if (compactJson(before.config) !== compactJson(config))
                projectChanges.push({ path: '.bobbin/config.json', content: Buffer.from(JSON.stringify(config, null, 2) + '\n'), expected: before.digest });
            if (options.host) {
                const target = options.host === 'codex' ? 'AGENTS.md' : 'CLAUDE.md', original = bytes(this.project, target), text = original ? utf8(original) : '';
                const begin = '<!-- BEGIN context-core-policy (managed by context-core) -->', end = '<!-- END context-core-policy (managed by context-core) -->';
                check((text.includes(begin) === text.includes(end)) && text.split(begin).length <= 2 && text.split(end).length <= 2, 'policy_invalid', 'Managed guidance markers are malformed.');
                const policy = contracts.policy, updated = text.includes(begin) ? text.slice(0, text.indexOf(begin)) + policy + text.slice(text.indexOf(end) + end.length) : text.replace(/\n*$/, '') + (text ? '\n\n' : '') + policy + '\n';
                projectChanges.push({ path: target, content: fileBytes(updated), expected: original ? sha256(original) : null });
            }
            if (fs.existsSync(path.join(this.vault, '.git'))) {
                const relative = '.gitattributes', raw = bytes(this.vault, relative), original = raw ? utf8(raw) : '', begin = '# BEGIN context-core-merge (managed by context-core)', end = '# END context-core-merge (managed by context-core)';
                check(original.includes(begin) === original.includes(end) && original.split(begin).length <= 2 && original.split(end).length <= 2, 'policy_invalid', 'Managed merge markers are malformed.');
                const block = contracts.merge_attributes, updated = original.includes(begin) ? original.slice(0, original.indexOf(begin)) + block + original.slice(original.indexOf(end) + end.length) : original.replace(/\n*$/, '') + (original ? '\n\n' : '') + block + '\n';
                changes.push({ path: relative, content: fileBytes(updated), expected: raw ? sha256(raw) : null });
            }
            const changed = this.project === this.vault ? this.storage.transaction([...changes, ...projectChanges]) : [...this.storage.transaction(changes), ...this.projectStorage.transaction(projectChanges).map(p => path.join(this.project, p))];
            return { schema: 'bobbin-init-result/v1', version: VERSION, project: this.project, vault: this.vault, config, enabled: [...BUILTINS, ...features], changed_paths: changed, host_configuration_changed: false, records_migrated: false, applied: changed.length > 0 };
        }, true);
    }
    private readResult(record: RecordEntry, options: ReadOptions = {}): ReadResult {
        const doc = record.document, schema = doc.frontmatter.schema, chosen = options.sections?.length ? options.sections.map(k => sectionName(schema, k)) : Object.keys(doc.sections).map(k => sectionName(schema, k));
        check(chosen.every(k => Object.keys(doc.sections).some(s => sectionName(schema, s) === k)), 'section_invalid', 'Requested section does not exist.', { sections: chosen });
        const available = Object.fromEntries(chosen.map(k => [k, sectionValue(doc, k)]));
        const history = record.row.state === 'history';
        const base: ObjectValue = { id: doc.frontmatter.id, kind: record.kind, path: record.path, state: record.row.state, frontmatter: doc.frontmatter, authority: history ? 'historical' : record.area.row.authority, do_not_follow: history, lifecycle_reason: doc.frontmatter.retired_reason ?? null, warnings: doc.warnings, physical_write: false };
        if (record.kind === 'snapshot') {
            base.use_as = 'resume_context';
            base.freshness = 'authority_unknown';
            const anchors = doc.frontmatter.anchors ?? [];
            if (anchors.length) {
                base.freshness = 'anchored';
                for (const id of anchors) {
                    try {
                        if (findRecord(this.vault, id).row.state !== 'current')
                            base.freshness = 'anchor_changed';
                    }
                    catch (e) {
                        if (e instanceof BobbinError && e.code === 'not_found')
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
            return { ...base, sections: available, truncated: false } as ReadResult;
        check(Number.isSafeInteger(options.maxBytes) && options.maxBytes >= 1 && options.maxBytes <= 32768, 'usage_invalid', 'maxBytes must be 1..32768.');
        return fitSections(base, available, options.maxBytes, { truncated: false }, { truncated: true, full_read_hint: `bobbin read ${doc.frontmatter.id}` }) as ReadResult;
    }
    async read(id: string, options: ReadOptions = {}): Promise<ReadResult> { return this.locked(() => this.readResult(findRecord(this.vault, id), options)); }
    async inspect(id: string): Promise<{
        path: string;
        content: string;
        digest: string;
        document: ContextDocument;
    }> { return this.locked(() => { const r = findRecord(this.vault, id); return { path: r.path, content: r.content, digest: sha256(Buffer.from(r.content)), document: r.document }; }); }
    private normalizeOperation(input: Operation): Operation {
        const operation = structuredClone(input);
        check(object(operation), 'usage_invalid', 'Operation must be an object.');
        if (operation.action === 'batch') {
            check(Array.isArray(operation.operations) && operation.operations.length >= 1 && operation.operations.length <= 8 && operation.operations.every(op => op.action !== 'batch' as any), 'candidate_batch_too_large', 'Batch requires 1..8 non-batch operations.');
            operation.operations = operation.operations.map(op => this.normalizeOperation(op)) as BatchOperation['operations'];
            return operation;
        }
        if (operation.action === 'capture') {
            operation.id ??= newId();
            operation.now ??= timestamp();
        }
        else if (operation.action === 'supersede') {
            operation.now ??= timestamp();
            operation.successor = this.normalizeOperation(operation.successor) as CaptureOperation;
        }
        else if (['update', 'annotate', 'retire'].includes(operation.action))
            operation.now ??= timestamp();
        return operation;
    }
    private operationChanges(operation: Operation, areas: Area[]): PreviewChange[] {
        if (operation.action === 'batch')
            return operation.operations.flatMap(op => this.operationChanges(op, areas));
        if (operation.action === 'capture') {
            const draft = draftCapture(operation.candidate, operation.attestation, operation);
            check(areas.some(a => a.row.area === (operation.targetKind ?? operation.candidate.requested_kind)), 'area_not_registered', 'Initialize the requested feature first.', {}, EXIT.conflict);
            return [{ path: draft.path, before_digest: null, after_digest: sha256(fileBytes(draft.content)), content: draft.content }];
        }
        const r = findRecord(this.vault, operation.id, areas), fm = structuredClone(r.document.frontmatter), sections = { ...r.document.sections }, before = sha256(Buffer.from(r.content));
        const change = (content: string | null, relative = r.path, expected: string | null = before): PreviewChange => ({ path: relative, before_digest: expected, after_digest: content === null ? null : sha256(fileBytes(content)), content });
        if (operation.action === 'rename') {
            check(['snapshot', 'observation'].includes(r.kind), 'lifecycle_invalid', 'Rename is supported for SNAP and OBS. Other kinds retain their owner lifecycle.');
            check(operation.filename, 'filename_required', 'Provide a new filename.');
            const destination = path.posix.dirname(r.path) + '/' + filename(operation.filename);
            check(destination !== r.path, 'no_change', 'Filename is unchanged.');
            return [change(null), change(r.content, destination, null)];
        }
        if (operation.action === 'discard') {
            check(['snapshot', 'observation', 'archive'].includes(r.kind), 'lifecycle_invalid', 'Authoritative records must use their retirement lifecycle.');
            const inbound = scanRecords(this.vault, areas).filter(x => x.row.id !== fm.id && referenceIds(x).has(fm.id));
            check(!inbound.length, 'inbound_reference', 'Referenced artifacts cannot be discarded.', { ids: inbound.map(x => x.row.id) }, EXIT.conflict);
            return [change(null)];
        }
        check(r.row.state === 'current', 'lifecycle_invalid', 'This lifecycle requires a Current artifact.', {}, EXIT.conflict);
        if (operation.action === 'supersede') {
            check(['observation', 'decision', 'assumption', 'term', 'intent'].includes(r.kind), 'lifecycle_invalid', 'This artifact does not support supersession.');
            const successor = draftCapture(operation.successor.candidate, operation.successor.attestation, operation.successor), successorKind = operation.successor.targetKind ?? operation.successor.candidate.requested_kind;
            check(successorKind === r.kind || (r.kind === 'observation' && successorKind === 'decision'), 'lifecycle_invalid', 'Unsupported cross-kind supersession.');
            if (r.kind === 'observation' && successorKind === 'decision') {
                check(fm.kind_hint === 'decision', 'lifecycle_invalid', 'Only a decision-like fallback OBS can be imported as DEC.');
                check(!Object.values(successor.document.frontmatter.relations ?? {}).some((ids: any) => ids.includes(fm.id)), 'fallback_relation_conflict', 'Fallback import uses lifecycle edges rather than an evidence relation.');
            }
            const semantic = this.sameClaimInput(r, successor.document, operation.successor.candidate);
            validateAttestation(operation.sameClaim, semantic, ['same_semantic_claim'], 'same_claim');
            if (fm.scope)
                check(successor.document.frontmatter.scope === fm.scope, 'scope_mismatch', 'Supersession cannot change the record scope.', {}, EXIT.conflict);
            if (['decision', 'intent', 'term'].includes(r.kind))
                check(successor.document.frontmatter[r.kind + '_key'] === fm[r.kind + '_key'], 'slot_mismatch', 'Supersession must retain the semantic slot key.', {}, EXIT.conflict);
            const sfm = successor.document.frontmatter;
            check(sfm.id !== fm.id, 'lifecycle_invalid', 'Successor must have a new ID.');
            fm.retired_at = timestamp(operation.now);
            fm.retired_reason = 'superseded';
            fm.superseded_by = sfm.id;
            sfm.supersedes = [...new Set([...(sfm.supersedes ?? []), fm.id])];
            const retired = `context/${r.kind}/retired/${path.posix.basename(r.path, '.md')}--${fm.id.slice(4, 16)}.md`;
            return [change(null), change(renderDocument(fm, sections), retired, null), change(renderDocument(sfm, successor.document.sections), successor.path, null)];
        }
        const values = operation.values ?? {};
        if (operation.action === 'retire') {
            const reasons: Record<string, string[]> = { observation: ['invalidated'], decision: ['withdrawn'], assumption: ['confirmed', 'refuted'], term: ['deprecated'] };
            check(reasons[r.kind]?.includes(values.reason), 'lifecycle_invalid', 'Unsupported retirement reason.');
            fm.retired_at = timestamp(operation.now);
            fm.retired_reason = values.reason;
            if (['observation', 'decision'].includes(r.kind))
                fm.retirement_note = shortText(values.note, 'note', 500);
            if (r.kind === 'assumption') {
                fm.evidence_refs = stringList(values.evidence_refs, 'evidence_refs', 1);
                if (values.reason === 'refuted') {
                    fm.refutation_reason = shortText(values.refutation_reason, 'refutation_reason', 800);
                    fm.impacted_decisions = values.impacted_decisions ?? [];
                }
            }
            if (r.kind === 'term') {
                fm.deprecation_reason = shortText(values.deprecation_reason, 'deprecation_reason', 800);
                if (values.replacement_term)
                    fm.replacement_term = values.replacement_term;
            }
            const retired = `context/${r.kind}/retired/${path.posix.basename(r.path, '.md')}--${fm.id.slice(4, 16)}.md`;
            return [change(null), change(renderDocument(fm, sections), retired, null)];
        }
        if (operation.action === 'reverify') {
            check(r.kind === 'observation', 'lifecycle_invalid', 'Only observations can be reverified.');
            fm.verified_at = timestamp(values.verified_at);
            const evidenceRef = shortText(values.evidence_ref, 'evidence_ref', 500);
            fm.source_refs = [...new Set([...(fm.source_refs ?? []), evidenceRef])];
        }
        else {
            check(operation.action === 'update' || operation.action === 'annotate', 'usage_invalid', 'Unknown mutation action.');
            check(operation.action === 'update' ? ['snapshot', 'document'].includes(r.kind) : ['observation', 'decision', 'assumption', 'term'].includes(r.kind), 'lifecycle_invalid', 'Unsupported mutation for this record kind.');
            const allowed: Record<string, string[]> = { snapshot: Object.values(sectionFields.snapshot), document: ['Content'], observation: [], decision: [], assumption: [], term: [] };
            if (r.kind === 'snapshot' && !operation.merge) {
                const supplied = Object.keys(operation.sections ?? {}).map(k => sectionName(fm.schema, k));
                check(['Current context', 'Open items', 'Next steps'].every(k => supplied.includes(k)), 'snapshot_full_update_required', 'Full snapshot update requires all required sections.');
                for (const key of Object.keys(sections))
                    delete sections[key];
                for (const field of ['anchors', 'tags', 'search_terms', 'source_refs'])
                    if (!Object.hasOwn(values, field))
                        delete fm[field];
            }
            for (const [key, value] of Object.entries(operation.sections ?? {})) {
                const name = sectionName(fm.schema, key);
                check(allowed[r.kind].includes(name), 'immutable_primary', 'Primary meaning changes require a successor.', { section: name }, EXIT.conflict);
                Object.assign(sections, existingStyle(fm.schema, { [name]: value }, r.document.sections));
            }
            const metadataAllowed = operation.action === 'annotate' ? ['title', 'summary', 'tags', 'search_terms', 'source_refs', ...(r.kind === 'observation' ? ['related'] : [])] : r.kind === 'snapshot' ? ['title', 'summary', 'tags', 'search_terms', 'source_refs', 'anchors'] : [];
            check(Object.keys(values).every(k => metadataAllowed.includes(k)), 'immutable_field', 'Mutation contains immutable or unknown fields.', {}, EXIT.conflict);
            Object.assign(fm, Object.fromEntries(Object.entries(values).filter(([k]) => k !== 'related')));
            if (values.related)
                fm.relations = { ...fm.relations, related: values.related };
            for (const key of operation.clear ?? []) {
                check(['tags', 'search_terms', 'source_refs', ...(r.kind === 'snapshot' ? ['anchors'] : [])].includes(key), 'usage_invalid', 'Invalid clear target.');
                delete fm[key];
            }
            if (!['decision', 'observation'].includes(r.kind) && renderDocument(fm, sections) !== r.content)
                fm.updated_at = timestamp(operation.now);
        }
        return [change(renderDocument(fm, sections))];
    }
    private checkChanges(changes: PreviewChange[], areas: Area[], operation: Operation): RecordEntry[] {
        const overlay = new Map<string, string | null>();
        check(new Set(changes.map(c => c.path)).size === changes.length, 'plan_conflict', 'Batch operations cannot mutate the same path more than once.', {}, EXIT.conflict);
        for (const c of changes) {
            contained(this.vault, c.path);
            check(c.after_digest === (c.content === null ? null : sha256(fileBytes(c.content))), 'approval_digest_mismatch', 'Rendered material bytes changed.', {}, EXIT.conflict);
            check(digestOrNull(this.vault, c.path) === c.before_digest, 'stale_input', 'Target content changed after preview.', { path: c.path }, EXIT.conflict);
            overlay.set(c.path, c.content);
        }
        for (const c of changes.filter(c => c.content !== null && c.before_digest === null)) {
            const directory = path.posix.dirname(c.path), basename = path.posix.basename(c.path);
            if (fs.existsSync(contained(this.vault, directory)))
                for (const name of fs.readdirSync(contained(this.vault, directory))) {
                    const existing = directory + '/' + name;
                    if (overlay.get(existing) === null)
                        continue;
                    check(normalizedKey(name) !== normalizedKey(basename), 'path_exists', 'A collision-equivalent path already exists.', { path: c.path }, EXIT.conflict);
                }
        }
        const records = scanRecords(this.vault, areas, overlay);
        validateRelations(records);
        const acknowledged = (operation.action === 'batch' ? operation.operations : [operation]).flatMap(op => op.action === 'capture' ? op.acknowledgements ?? [] : []);
        validateSlots(records, acknowledged, new Set(records.filter(r => overlay.has(r.path)).map(r => r.row.id)));
        return records;
    }
    async prepareSameClaim(predecessorId: string, successor: CaptureOperation): Promise<ObjectValue> {
        return this.locked(() => {
            const r = findRecord(this.vault, predecessorId), draft = draftCapture(successor.candidate, successor.attestation, successor);
            check(successor.id, 'id_required', 'Allocate the successor ID before assessing same_claim.');
            return this.sameClaimInput(r, draft.document, successor.candidate);
        });
    }
    private sameClaimInput(record: RecordEntry, successor: ContextDocument, candidate: Candidate): ObjectValue { return { schema: 'bobbin-same-claim-input/v1', predecessor: { id: record.row.id, path: record.path, sha256: sha256(Buffer.from(record.content)), primary_claim: primaryClaim(record.document), scope: record.document.frontmatter.scope ?? null, sections: record.document.sections }, successor: { id: successor.frontmatter.id, primary_claim: primaryClaim(successor), scope: successor.frontmatter.scope ?? null, candidate_digest: canonicalDigest(candidate) } }; }
    async preview(input: Operation): Promise<Preview> {
        return this.locked(() => {
            const operation = this.normalizeOperation(input), areas = registeredAreas(this.vault), changes = this.operationChanges(operation, areas);
            const records = this.checkChanges(changes, areas, operation);
            const changedPaths = new Set(changes.map(c => c.path)), ids = new Set<string>();
            for (const record of records)
                if (changedPaths.has(record.path)) {
                    for (const id of referenceIds(record))
                        ids.add(id);
                }
            for (const op of operation.action === 'batch' ? operation.operations : [operation])
                if (op.action === 'capture')
                    for (const id of op.acknowledgements ?? [])
                        ids.add(id);
            const read_preconditions = records.filter(r => ids.has(r.row.id) && !changedPaths.has(r.path)).map(r => ({ path: r.path, sha256: sha256(Buffer.from(r.content)) })).sort((a, b) => compareText(a.path, b.path));
            const base = { schema: 'bobbin-preview/v1' as const, plan_id: newPlanId(), vault_identity: identity(this.vault), project_policy: this.policyBinding(), operation, changes, read_preconditions };
            const snapshots = records.filter(r => r.kind === 'snapshot' && changedPaths.has(r.path));
            if (snapshots.length) {
                const ids = new Set(snapshots.map(r => r.row.id)), paths = new Set(snapshots.map(r => r.path));
                const ordinary = (operation.action === 'batch' ? operation.operations : [operation]).filter(op => !('id' in op && ids.has(op.id!)));
                check(Buffer.byteLength(canonicalJson({ ...base, operation: { action: 'batch', operations: ordinary }, changes: changes.filter(c => !paths.has(c.path)) })) <= 128 * 1024, 'approval_preview_too_large', 'Non-SNAP operations exceed their existing byte budget.');
            }
            check(Buffer.byteLength(snapshots.length ? JSON.stringify(base) : canonicalJson(base)) <= (snapshots.length ? SNAP_TRANSPORT_MAX_BYTES : operation.action === 'capture' && operation.candidate.requested_kind === 'archive' ? 1024 * 1024 : 128 * 1024), 'approval_preview_too_large', 'Preview exceeds the mutation byte budget.');
            return { ...base, approval_digest: previewDigest(base), state: 'awaiting_approval', applied: false };
        }, true);
    }
    async apply(preview: Preview, authorization: Authorization): Promise<ApplyResult> {
        return this.locked(() => {
            exact(preview, ['schema', 'plan_id', 'vault_identity', 'project_policy', 'operation', 'changes', 'read_preconditions', 'approval_digest', 'state', 'applied'], 'bundle_invalid');
            const { approval_digest, state, applied, ...base } = preview;
            check(preview.schema === 'bobbin-preview/v1' && state === 'awaiting_approval' && applied === false && previewDigest(base) === approval_digest, 'approval_digest_mismatch', 'Frozen preview changed; prepare a new preview.', {}, EXIT.conflict);
            for (const c of preview.changes)
                check(c.after_digest === (c.content === null ? null : sha256(fileBytes(c.content))), 'approval_digest_mismatch', 'Rendered material bytes changed.', {}, EXIT.conflict);
            check(canonicalDigest(preview.vault_identity) === canonicalDigest(identity(this.vault)), 'vault_identity_mismatch', 'Preview belongs to another vault.', {}, EXIT.conflict);
            check(canonicalDigest(preview.project_policy) === canonicalDigest(this.policyBinding()), 'project_policy_changed', 'Project or recording policy changed after preview.', {}, EXIT.conflict);
            for (const precondition of preview.read_preconditions)
                check(digestOrNull(this.vault, precondition.path) === precondition.sha256, 'stale_reference', 'Referenced content changed after preview.', { path: precondition.path }, EXIT.conflict);
            const areas = registeredAreas(this.vault), settings = loadSettings(this.project);
            const touched = [...new Set(preview.changes.map(c => c.path.split('/')[1]))];
            check(!settings.enabled || touched.every(k => settings.enabled!.includes(k)), 'feature_disabled', 'Enable this feature before recording.', { areas: touched }, EXIT.conflict);
            check(authorization?.source === 'user' || authorization?.source === 'policy', 'approval_required', 'Provide user or configured policy authorization.', {}, EXIT.conflict);
            if (authorization.source === 'policy') {
                check(settings.config && settings.mode !== 'explicit', 'approval_required', 'Explicit mode requires user authorization.', {}, EXIT.conflict);
                check(realDirectory(path.resolve(this.project, settings.config.vault)) === this.vault, 'vault_policy_mismatch', 'Automatic recording is limited to the configured vault.', {}, EXIT.conflict);
                if (settings.mode === 'adaptive') {
                    check(['record', 'ask'].includes(authorization.decision ?? '') && typeof authorization.reason === 'string' && authorization.reason.trim() && authorization.reason.length <= 1000, 'policy_assessment_required', 'Adaptive mode requires a record/ask decision and reason.', {}, EXIT.conflict);
                    check(authorization.decision === 'record', 'approval_required', authorization.reason!, {}, EXIT.conflict);
                }
            }
            if (preview.changes.every(c => digestOrNull(this.vault, c.path) === (c.content === null ? null : sha256(fileBytes(c.content)))))
                return { applied: false, already_applied: true, plan_id: preview.plan_id, approval_digest, changed_paths: [], index_paths: [], warnings: [], authorization: { ...authorization, mode: settings.mode } };
            const records = this.checkChanges(preview.changes, areas, preview.operation), fresh = this.operationChanges(preview.operation, areas);
            check(canonicalDigest(fresh) === canonicalDigest(preview.changes), 'bundle_invalid', 'Preview differs from validated owner output.', {}, EXIT.conflict);
            const changes: FileChange[] = preview.changes.map(c => ({ path: c.path, content: c.content === null ? null : fileBytes(c.content), expected: c.before_digest })), warnings: string[] = [];
            for (const area of areas.filter(a => touched.includes(a.row.area))) {
                const current = scanRecords(this.vault, [area]), canonicalBefore = rebuildArea(area, current);
                if (canonicalBefore !== area.text)
                    warnings.push('index_regenerated:' + area.row.path);
                changes.push({ path: area.row.path, content: fileBytes(rebuildArea(area, records)), expected: sha256(Buffer.from(area.text)) });
            }
            const changed = this.storage.transaction(changes);
            return { applied: true, plan_id: preview.plan_id, approval_digest, changed_paths: changed, index_paths: changes.filter(c => c.path.endsWith('.index.md')).map(c => c.path), warnings, authorization: { ...authorization, mode: settings.mode } };
        }, true);
    }
    async refresh(fix = false): Promise<ObjectValue> {
        return this.locked(() => {
            const recovered: string[] = [];
            if (fix && !bytes(this.vault, ROOT_INDEX)) {
                const directory = contained(this.vault, 'context');
                check(fs.existsSync(directory), 'context_root_missing', 'No indexes are available for recovery.', {}, EXIT.notFound);
                const areas: Area[] = [];
                for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
                    if (!entry.isDirectory() || !(/^[a-z][a-z0-9_-]{0,79}$/.test(entry.name)))
                        continue;
                    const relative = `context/${entry.name}/${entry.name}.index.md`, raw = bytes(this.vault, relative);
                    if (!raw)
                        continue;
                    const text = utf8(raw), index = parseAreaIndex(text), fm = index.frontmatter;
                    areas.push({ row: { area: fm.area, path: relative, owner: fm.owner, claims: [fm.area], artifact_schema: fm.artifact_schema, authority: fm.authority }, metadata: fm, descriptor: index.descriptor, text });
                }
                check(areas.length > 0, 'context_root_missing', 'No valid area indexes are available for recovery.', {}, EXIT.notFound);
                scanRecords(this.vault, areas);
                recovered.push(...this.storage.transaction([{ path: ROOT_INDEX, content: fileBytes(renderRoot(areas)), expected: null }]));
            }
            const areas = registeredAreas(this.vault), records = scanRecords(this.vault, areas), issues: ObjectValue[] = [], changes: FileChange[] = [];
            try {
                validateRelations(records);
                validateSlots(records);
            }
            catch (e) {
                if (e instanceof BobbinError)
                    issues.push({ code: e.code, ...e.details });
                else
                    throw e;
            }
            for (const area of areas) {
                const text = rebuildArea(area, records);
                if (text !== area.text) {
                    issues.push({ code: 'index_content_drift', path: area.row.path });
                    changes.push({ path: area.row.path, content: fileBytes(text), expected: sha256(Buffer.from(area.text)) });
                }
            }
            const root = readText(this.vault, ROOT_INDEX), renderedRoot = renderRoot(areas, root);
            if (root !== renderedRoot) {
                issues.push({ code: 'index_content_drift', path: ROOT_INDEX });
                changes.push({ path: ROOT_INDEX, content: fileBytes(renderedRoot), expected: sha256(Buffer.from(root)) });
            }
            check(!fix || issues.every(i => i.code === 'index_content_drift'), 'integrity_error', 'Resolve record integrity errors before rebuilding indexes.', { issues }, EXIT.integrity);
            const changed = fix ? [...recovered, ...this.storage.transaction(changes)] : [];
            return { ok: issues.length === 0 || fix, issues, changed_paths: changed, record_count: records.length, index_fixed: fix };
        });
    }
    async recall(options: RecallOptions = {}): Promise<ObjectValue> { return this.locked(() => this.recallUnlocked(options)); }
    async search(kind: Kind, options: SearchOptions = {}): Promise<ObjectValue> {
        return this.locked(() => {
            const signal = kind === 'assumption' ? 'assumption-relevant' : kind === 'term' ? 'term-encountered' : null;
            if (signal)
                check(options.signal === signal, 'signal_required', 'Specialized recall requires a relevant signal.', { required: signal }, EXIT.conflict);
            const limit = options.limit ?? (kind === 'decision' ? 8 : 20);
            check(Number.isSafeInteger(limit) && limit >= 1 && limit <= (kind === 'decision' ? 20 : 50), 'usage_invalid', 'Search limit is outside the owner budget.');
            const area = registeredAreas(this.vault).find(a => a.row.area === kind);
            check(area, 'area_not_registered', 'Requested area is not registered.', {}, EXIT.notFound);
            const index = parseAreaIndex(area.text), needle = normalizedKey(options.query ?? '').trim();
            const rows = [...index.current, ...(options.includeHistory ? index.history : [])].filter(r => (!options.scope || r.scope === options.scope) && (!options.key || r[kind + '_key'] === options.key));
            const fields = kind === 'decision' ? ['id', 'title', 'summary', 'path', 'scope', 'decision_key'] : kind === 'intent' ? ['title', 'summary', 'scope', 'intent_key'] : kind === 'document' ? ['title', 'summary', 'scope', 'document_key'] : ['title', 'summary', 'scope'];
            const selected = rows.filter(r => !needle || normalizedKey([...fields.map(f => r[f] ?? ''), ...(r.terms ?? [])].join(' ')).includes(needle)).sort((a, b) => compareText(b.updated_at ?? b.created_at, a.updated_at ?? a.created_at) || compareText(b.id, a.id));
            const items = selected.slice(0, limit).map(row => ({ ...row, kind, authority: row.state === 'history' ? 'historical' : area.row.authority, do_not_follow: row.state === 'history' }));
            return { schema: `context-${kind}-search/v1`, items, returned: items.length, omitted: selected.length - items.length, truncated: selected.length > items.length, metadata_only: true, ...(signal ? { signal } : {}), physical_write: false };
        });
    }
    async compareDecision(options: DecisionCompareOptions): Promise<ObjectValue> { return this.locked(() => prepareDecisionCompare(this.vault, options)); }
    async checkDecision(options: DecisionCheckOptions): Promise<ObjectValue> { return this.locked(() => prepareDecisionCheck(this.vault, options)); }
    async specView(scope: string, maxBytes?: number): Promise<ObjectValue> { return this.locked(() => decisionSpecView(this.vault, scope, maxBytes)); }
    async revisitDecisions(options: {
        ids?: string[];
        due?: boolean;
        asOf?: string;
    } = {}): Promise<ObjectValue> { return this.locked(() => { const area = registeredAreas(this.vault).find(a => a.row.area === 'decision'); check(area, 'area_not_registered', 'Decision owner is not initialized.'); const today = options.asOf ?? new Date().toISOString().slice(0, 10), rows = parseAreaIndex(area.text).current.filter(r => (!options.ids?.length || options.ids.includes(r.id)) && (!options.due || (r.revisit_on && r.revisit_on <= today))); return { items: rows.map(r => this.readResult(findRecord(this.vault, r.id))), as_of: today, returned: rows.length, physical_write: false }; }); }
    private recallUnlocked(options: RecallOptions): ObjectValue {
        const settings = loadSettings(this.project), query = options.query ?? '', limit = options.limit ?? 8, expanded = !!(options.pack || options.sections?.length || options.readIds?.length), budget = options.maxBytes ?? (expanded ? 8192 : 4096);
        check(limit >= 1 && limit <= 20 && budget >= 1 && budget <= 32768, 'usage_invalid', 'Recall limit or byte budget is outside the supported range.');
        const areas = registeredAreas(this.vault).filter(a => (a.row.area !== 'archive' || options.includeArchive) && (options.areas?.length ? options.areas.includes(a.row.area) : !settings.enabled || settings.enabled.includes(a.row.area)));
        let all: {
            row: ObjectValue;
            area: Area;
        }[] = [], fallback = false, remaining = 20;
        const warnings: string[] = [];
        const fallbackArea = (area: Area, exclude = new Set<string>()) => {
            const paths = listArtifactPaths(this.vault, area.row.area, options.includeHistory).filter(p => !exclude.has(p)), found: {
                row: ObjectValue;
                area: Area;
            }[] = [];
            for (const relative of paths.slice(0, remaining)) {
                const doc = parseDocument(readText(this.vault, relative), area.descriptor);
                found.push({ row: projectEntry(area, relative, doc), area });
            }
            remaining -= found.length;
            if (paths.length > found.length)
                warnings.push('index_fallback_truncated');
            return found;
        };
        for (const area of areas) {
            try {
                const parsed = parseAreaIndex(area.text);
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
        const tokens = [...new Set(normalizedKey(query).match(/[\p{L}\p{N}_]+(?:[.-][\p{L}\p{N}_]+)*/gu) ?? [])], minimum = Math.min(2, Math.ceil(tokens.length / 2));
        const matched = (entries: typeof all) => entries.map(x => ({ ...x, ...score(x.row, query) })).filter(x => (!options.readIds?.length || options.readIds.includes(x.row.id)) && (!query || (x.score > 0 && x.matched >= minimum)) && (options.facets ?? []).every(([key, val]) => Array.isArray(x.row[key]) ? x.row[key].some((v: string) => normalizedKey(v) === normalizedKey(val)) : typeof x.row[key] === 'string' && normalizedKey(x.row[key]) === normalizedKey(val)));
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
        filtered.sort((a, b) => b.score - a.score || compareText(b.row.created_at, a.row.created_at) || compareText(a.row.id, b.row.id));
        const output: ObjectValue[] = [], result = (items: ObjectValue[]) => ({ items, returned: items.length, omitted: Math.max(0, filtered.length - items.length), truncated: filtered.length > items.length, index_fallback: fallback, warnings: [...new Set(warnings)].sort() });
        for (const { row, area, score: points } of filtered.slice(0, limit)) {
            let item: ObjectValue = { id: row.id, kind: area.row.area, state: row.state, title: row.title, summary: row.summary, path: row.path, authority: row.state === 'history' ? 'historical' : area.row.authority, do_not_follow: row.state === 'history', score: points };
            for (const key of area.metadata.projection_fields ?? [])
                if (Object.hasOwn(row, key))
                    item[key] = row[key];
            if (expanded) {
                const document = parseDocument(readText(this.vault, row.path), area.descriptor);
                warnings.push(...document.warnings.map(x => x.code));
                const names = options.sections?.length ? options.sections : Object.keys(document.sections).map(k => sectionName(document.frontmatter.schema, k)), available = Object.fromEntries(names.map(k => [k, sectionValue(document, k)]));
                const overhead = Buffer.byteLength(canonicalJson(result([...output, {}]))) - 2;
                try {
                    item = fitSections(item, available, Math.min(2048, Math.min(budget, 8192) - overhead), {}, { section_truncated: true, full_read_hint: `context recall --read ${row.id}` });
                }
                catch (e) {
                    if (e instanceof BobbinError && e.code === 'usage_invalid')
                        break;
                    throw e;
                }
            }
            if (Buffer.byteLength(canonicalJson(result([...output, item]))) > budget)
                break;
            output.push(item);
        }
        return result(output);
    }
}
export function createBobbin(options: BobbinOptions): Bobbin { return new Bobbin(options); }
export function fitSections(base: ObjectValue, available: Record<string, string>, maximum: number, completeFields: ObjectValue, truncatedFields: ObjectValue): ObjectValue {
    const size = (x: ObjectValue) => Buffer.byteLength(canonicalJson(x)), complete = { ...base, sections: available, ...completeFields };
    if (size(complete) <= maximum)
        return complete;
    const result = { ...base, sections: {} as Record<string, string>, ...truncatedFields };
    check(size(result) <= maximum, 'usage_invalid', 'maxBytes is too small for the metadata envelope.', { minimum_bytes: size(result), max_bytes: maximum });
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
function score(row: ObjectValue, query: string): {
    score: number;
    matched: number;
    strong: number;
} {
    const q = normalizedKey(query.trim());
    if (!q)
        return { score: 0, matched: 0, strong: 0 };
    const tokens = [...new Set(q.match(/[\p{L}\p{N}_]+(?:[.-][\p{L}\p{N}_]+)*/gu) ?? [])], title = normalizedKey(row.title ?? ''), summary = normalizedKey(row.summary ?? ''), relative = normalizedKey(row.path ?? ''), terms = (row.terms ?? []).map(normalizedKey), exact = q === normalizedKey(row.id ?? '');
    let points = exact ? 100 : 0, matched = 0, strong = 0;
    if (title.includes(q))
        points += 40;
    if (summary.includes(q))
        points += 10;
    if (terms.includes(q))
        points += 12;
    for (const t of tokens) {
        const p = title.includes(t) ? 8 : terms.some((x: string) => x.includes(t)) ? 6 : summary.includes(t) ? 3 : relative.includes(t) ? 1 : 0;
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
