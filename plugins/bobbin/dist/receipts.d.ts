import { Bobbin, Preview, Authorization } from './store';
import { ObjectValue } from './common';
export declare function freezeReceipt(bobbin: Bobbin, preview: Preview, requested?: string): ObjectValue;
export declare function readReceipt(bobbin: Bobbin, filename: string): {
    receipt: ObjectValue;
    file: string;
    inode: number;
    device: number;
    sha256: string;
};
export declare function applyReceipt(bobbin: Bobbin, filename: string, digest: string, authorization: Authorization, keep?: boolean): Promise<ObjectValue>;
export declare function rejectReceipt(bobbin: Bobbin, filename: string): ObjectValue;
