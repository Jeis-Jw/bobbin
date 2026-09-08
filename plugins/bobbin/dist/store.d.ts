import { ObjectValue } from './common';
import { Kind, ContextDocument } from './documents';
import { Candidate, Attestation, DraftOptions } from './owners';
import { DecisionCheckOptions } from './decision';
import { CandidateBatch } from './routing';
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
export declare class Bobbin {
    readonly vault: string;
    readonly project: string;
    private readonly storage;
    private readonly projectStorage;
    constructor(options: BobbinOptions);
    private locked;
    private policyBinding;
    adoptLegacy(confirmLegacyStopped: boolean): ObjectValue;
    recoverRuntime(): Promise<ObjectValue>;
    settings(): ObjectValue;
    route(batch: CandidateBatch | Candidate[], results: ObjectValue[] | ObjectValue): ObjectValue;
    previewOwnerResult(result: ObjectValue): Promise<Preview>;
    validate(value: Preview | Operation | ObjectValue): Promise<ObjectValue>;
    registerArea(descriptor: ObjectValue, indexSeed: string): Promise<ObjectValue>;
    initialize(options?: InitializeOptions): Promise<ObjectValue>;
    private readResult;
    read(id: string, options?: ReadOptions): Promise<ReadResult>;
    inspect(id: string): Promise<{
        path: string;
        content: string;
        digest: string;
        document: ContextDocument;
    }>;
    private normalizeOperation;
    private operationChanges;
    private checkChanges;
    prepareSameClaim(predecessorId: string, successor: CaptureOperation): Promise<ObjectValue>;
    private sameClaimInput;
    preview(input: Operation): Promise<Preview>;
    apply(preview: Preview, authorization: Authorization): Promise<ApplyResult>;
    refresh(fix?: boolean): Promise<ObjectValue>;
    recall(options?: RecallOptions): Promise<ObjectValue>;
    search(kind: Kind, options?: SearchOptions): Promise<ObjectValue>;
    checkDecision(options: DecisionCheckOptions): Promise<ObjectValue>;
    specView(scope: string, maxBytes?: number): Promise<ObjectValue>;
    revisitDecisions(options?: {
        ids?: string[];
        due?: boolean;
        asOf?: string;
    }): Promise<ObjectValue>;
    private recallUnlocked;
}
export declare function createBobbin(options: BobbinOptions): Bobbin;
export declare function fitSections(base: ObjectValue, available: Record<string, string>, maximum: number, completeFields: ObjectValue, truncatedFields: ObjectValue): ObjectValue;
export {};
