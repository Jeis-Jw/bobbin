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
export interface DecisionCompareOptions extends DecisionCheckOptions {
    scope: string;
    decisionKey: string;
}
export declare const DECISION_ASSESSMENT_CONTRACT = "context-decision-assessment/v1";
export declare function decisionAssessmentContract(withRevisit?: boolean): ObjectValue;
export declare function prepareDecisionCheck(root: string, options: DecisionCheckOptions): ObjectValue;
export declare function prepareDecisionCompare(root: string, options: DecisionCompareOptions): ObjectValue;
export declare function decisionSpecView(root: string, scope: string, maxBytes?: number): ObjectValue;
