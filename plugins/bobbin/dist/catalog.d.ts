import { ObjectValue } from './common';
import { ContextDocument } from './documents';
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
export declare function referenceIds(record: RecordEntry): Set<string>;
export declare const ROOT_INDEX = "context/context.index.md";
export declare function registeredAreas(root: string): Area[];
export declare function listArtifactPaths(root: string, area: string, includeHistory?: boolean): string[];
export declare function projectEntry(area: Area, relative: string, doc: ContextDocument): ObjectValue;
export declare function scanRecords(root: string, areas?: Area[], overlay?: Map<string, string | null>): RecordEntry[];
export declare function rebuildArea(area: Area, records: RecordEntry[]): string;
export declare function newArea(kind: string): Area;
export declare function renderRoot(areas: Area[], seed?: string): string;
export declare function findRecord(root: string, id: string, areas?: Area[]): RecordEntry;
export declare function validateRelations(records: RecordEntry[]): void;
export declare function validateSlots(records: RecordEntry[], acknowledged?: string[], changedIds?: Set<string>): void;
