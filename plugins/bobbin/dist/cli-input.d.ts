import { ObjectValue } from './common';
import { Kind } from './documents';
import { Attestation, Candidate } from './owners';
export declare const list: (value: any) => any[];
export declare function loadJson(value: string): any;
export declare function loadFile(value: string, maximum?: number): string;
export declare const loadBody: (value: string, maximum?: number) => string;
export declare const bodyItems: (value: any) => string[];
export declare function inlineInputs(kind: Kind, flags: ObjectValue): ObjectValue;
export declare function captureArguments(kind: Kind, flags: ObjectValue, prepareOnly?: boolean): {
    candidate: Candidate;
    attestation: Attestation;
};
