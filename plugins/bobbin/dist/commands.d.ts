import { Bobbin, Operation, Authorization } from './store';
import { ObjectValue } from './common';
import { Kind } from './documents';
export declare function authorization(flags: ObjectValue): Authorization;
export declare function submit(bobbin: Bobbin, operation: Operation, flags: ObjectValue, record?: boolean): Promise<ObjectValue>;
export declare function kindCommand(bobbin: Bobbin, kind: Kind, command: string, words: string[], flags: ObjectValue): Promise<ObjectValue>;
