import { ObjectValue } from './common';
import { Kind } from './documents';
import { Candidate, Attestation, DraftOptions } from './owners';
import type { CaptureOperation } from './store';
export interface CandidateBatch {
    schema: 'context-capture-batch/v1';
    audit_count: 1;
    candidates: Candidate[];
}
export declare function validateCandidateBatch(batch: CandidateBatch | Candidate[]): Candidate[];
export declare function draftOwnerResult(candidate: Candidate, attestation: Attestation, options?: DraftOptions): ObjectValue;
export declare function declineOwnerResult(candidate: Candidate, kind: Kind, reason: string): ObjectValue;
export declare function validateOwnerResult(result: ObjectValue): void;
export declare function operationFromOwnerResult(result: ObjectValue, validate?: boolean): CaptureOperation;
/** Pure routing: callers collect semantic owner decisions; this never invokes agents. */
export declare function routeCandidates(batch: CandidateBatch | Candidate[], claimResults: ObjectValue[] | ObjectValue, enabled?: string[] | null): ObjectValue;
