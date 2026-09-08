import { ObjectValue } from './common';
import { Kind, ContextDocument } from './documents';
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
export declare const primaryFields: Record<Kind, string>;
export declare const sectionFields: Record<Kind, Record<string, string>>;
export declare const relationFields: Record<string, string>;
export declare const assertionPointers: Record<Kind, Record<string, string[]>>;
export declare function validateOwnerInputs(kind: Kind, value: unknown): ObjectValue;
export declare function createCandidate<K extends Kind>(input: CaptureInput<K>): Candidate;
export declare function validateCandidate(candidate: Candidate, targetKind?: Kind): {
    kind: Kind;
    values: ObjectValue;
};
export declare function pointer(value: unknown, reference: string): unknown;
/** The caller supplies assertions after examining meaning; this does not infer approval. */
export declare function createAttestation(input: ObjectValue, assertions: Assertion[], operation?: string): Attestation;
export declare function validateAttestation(attestation: Attestation, input: ObjectValue, required: string[], operation?: string, targetKind?: Kind): void;
export interface DraftOptions {
    id?: string;
    now?: string;
    filename?: string;
    targetKind?: Kind;
}
export declare function draftCapture(candidate: Candidate, attestation: Attestation, options?: DraftOptions): {
    path: string;
    content: string;
    document: ContextDocument;
};
export declare function primaryClaim(document: ContextDocument): string;
