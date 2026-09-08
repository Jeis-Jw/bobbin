import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { Bobbin, Preview, Authorization } from './store';
import { ObjectValue, canonicalDigest, check, strictJson, sha256, EXIT } from './common';
import { bytes, utf8 } from './filesystem';
function receiptPath(bobbin: Bobbin, requested?: string): string {
    const directory = requested ? path.dirname(path.resolve(requested)) : path.join(os.tmpdir(), 'bobbin-receipts');
    if (!requested)
        fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    const real = fs.realpathSync(directory), s = fs.lstatSync(directory);
    check(s.isDirectory() && !s.isSymbolicLink() && (s.mode & 0o077) === 0 && (process.getuid === undefined || s.uid === process.getuid()), 'receipt_path_invalid', 'Receipts require a private directory owned by the caller.', {}, EXIT.conflict);
    check(real !== bobbin.vault && !real.startsWith(bobbin.vault + path.sep), 'receipt_path_invalid', 'Transient receipts must be outside the vault.', {}, EXIT.conflict);
    return requested ? path.join(real, path.basename(requested)) : real;
}
export function freezeReceipt(bobbin: Bobbin, preview: Preview, requested?: string): ObjectValue {
    const selected = receiptPath(bobbin, requested), file = requested ? selected : path.join(selected, preview.plan_id + '.json');
    const base = { schema: 'bobbin-receipt/v1', created_at: new Date().toISOString(), preview }, receipt = { ...base, receipt_digest: canonicalDigest(base) };
    const fd = fs.openSync(file, 'wx', 0o600);
    try {
        fs.writeFileSync(fd, JSON.stringify(receipt) + '\n');
        fs.fsyncSync(fd);
    }
    finally {
        fs.closeSync(fd);
    }
    return { state: 'awaiting_approval', applied: false, receipt_file: file, approval_digest: preview.approval_digest, plan_id: preview.plan_id, approval_preview: preview };
}
export function readReceipt(bobbin: Bobbin, filename: string): {
    receipt: ObjectValue;
    file: string;
    inode: number;
    device: number;
    sha256: string;
} {
    check(typeof filename === 'string' && filename.length > 0, 'usage_invalid', 'Retain and provide the explicit receipt filename.');
    const file = receiptPath(bobbin, filename);
    check(fs.existsSync(file), 'receipt_missing', 'Receipt file was not found.', {}, EXIT.notFound);
    const before = fs.lstatSync(file), raw = bytes(path.dirname(file), path.basename(file), 2 * 1024 * 1024);
    check(raw, 'receipt_missing', 'Receipt file was not found.', {}, EXIT.notFound);
    const s = fs.lstatSync(file);
    check(before.ino === s.ino && before.dev === s.dev && before.size === s.size && before.mtimeMs === s.mtimeMs, 'receipt_changed', 'Receipt changed during reading.', {}, EXIT.conflict);
    check((s.mode & 0o077) === 0, 'receipt_unsafe', 'Receipt must be private.', {}, EXIT.conflict);
    const receipt = strictJson(utf8(raw));
    check(receipt.schema === 'bobbin-receipt/v1', 'receipt_incompatible', 'Python receipts cannot cross the runtime cutover; prepare a new preview.', {}, EXIT.conflict);
    const { receipt_digest, ...base } = receipt;
    check(canonicalDigest(base) === receipt_digest, 'receipt_invalid', 'Frozen receipt digest differs.', {}, EXIT.conflict);
    const age = Date.now() - Date.parse(receipt.created_at);
    check(Number.isFinite(age) && age >= -60000 && age <= 86400000, 'receipt_expired', 'Receipt expired; create a new preview.', {}, EXIT.conflict);
    return { receipt, file, inode: s.ino, device: s.dev, sha256: sha256(raw) };
}
export async function applyReceipt(bobbin: Bobbin, filename: string, digest: string, authorization: Authorization, keep = false): Promise<ObjectValue> {
    const loaded = readReceipt(bobbin, filename);
    check(digest === loaded.receipt.preview.approval_digest, 'approval_digest_mismatch', 'Forward the unchanged digest returned by preview.', {}, EXIT.conflict);
    const result: ObjectValue = await bobbin.apply(loaded.receipt.preview, authorization);
    if (!keep) {
        try {
            const s = fs.lstatSync(loaded.file);
            check(s.ino === loaded.inode && s.dev === loaded.device && sha256(bytes(path.dirname(loaded.file), path.basename(loaded.file))!) === loaded.sha256, 'receipt_changed', 'Receipt identity or bytes changed.');
            fs.unlinkSync(loaded.file);
        }
        catch (error) {
            result.warnings = [...(result.warnings ?? []), 'receipt_cleanup_failed'];
            result.receipt_cleanup_error = String(error);
        }
    }
    return result;
}
export function rejectReceipt(bobbin: Bobbin, filename: string): ObjectValue { const loaded = readReceipt(bobbin, filename); fs.unlinkSync(loaded.file); return { rejected: true, applied: false, receipt_file: loaded.file }; }
