import { ObjectValue } from './common';
export declare function canonicalTerms(text: string): string[];
export declare function deriveSearchTerms(title: string, summary: string, values: ObjectValue, maximum?: number): string[];
export interface DecisionCheckOptions {
    statement: string;
    scope?: string;
    decisionKey?: string;
    rationale?: string;
    query?: string;
    limit?: number;
    knownCurrent?: {
        id: string;
        sha256: string;
    }[];
}
export declare function prepareDecisionCheck(root: string, options: DecisionCheckOptions): ObjectValue;
export declare function decisionSpecView(root: string, scope: string, maxBytes?: number): ObjectValue;
