"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.freezeReceipt = freezeReceipt;
exports.readReceipt = readReceipt;
exports.applyReceipt = applyReceipt;
exports.rejectReceipt = rejectReceipt;
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
const os = __importStar(require("node:os"));
const common_1 = require("./common");
const filesystem_1 = require("./filesystem");
function receiptPath(bobbin, requested) {
    const directory = requested ? path.dirname(path.resolve(requested)) : path.join(os.tmpdir(), 'bobbin-receipts');
    if (!requested)
        fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    const real = fs.realpathSync(directory), s = fs.lstatSync(directory);
    (0, common_1.check)(s.isDirectory() && !s.isSymbolicLink() && (s.mode & 0o077) === 0 && (process.getuid === undefined || s.uid === process.getuid()), 'receipt_path_invalid', 'Receipts require a private directory owned by the caller.', {}, common_1.EXIT.conflict);
    (0, common_1.check)(real !== bobbin.vault && !real.startsWith(bobbin.vault + path.sep), 'receipt_path_invalid', 'Transient receipts must be outside the vault.', {}, common_1.EXIT.conflict);
    return requested ? path.join(real, path.basename(requested)) : real;
}
function freezeReceipt(bobbin, preview, requested) {
    const selected = receiptPath(bobbin, requested), file = requested ? selected : path.join(selected, preview.plan_id + '.json');
    const base = { schema: 'bobbin-receipt/v1', created_at: new Date().toISOString(), preview }, receipt = { ...base, receipt_digest: (0, common_1.canonicalDigest)(base) };
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
function readReceipt(bobbin, filename) {
    (0, common_1.check)(typeof filename === 'string' && filename.length > 0, 'usage_invalid', 'Retain and provide the explicit receipt filename.');
    const file = receiptPath(bobbin, filename);
    (0, common_1.check)(fs.existsSync(file), 'receipt_missing', 'Receipt file was not found.', {}, common_1.EXIT.notFound);
    const before = fs.lstatSync(file), raw = (0, filesystem_1.bytes)(path.dirname(file), path.basename(file), 2 * 1024 * 1024);
    (0, common_1.check)(raw, 'receipt_missing', 'Receipt file was not found.', {}, common_1.EXIT.notFound);
    const s = fs.lstatSync(file);
    (0, common_1.check)(before.ino === s.ino && before.dev === s.dev && before.size === s.size && before.mtimeMs === s.mtimeMs, 'receipt_changed', 'Receipt changed during reading.', {}, common_1.EXIT.conflict);
    (0, common_1.check)((s.mode & 0o077) === 0, 'receipt_unsafe', 'Receipt must be private.', {}, common_1.EXIT.conflict);
    const receipt = (0, common_1.strictJson)((0, filesystem_1.utf8)(raw));
    (0, common_1.check)(receipt.schema === 'bobbin-receipt/v1', 'receipt_incompatible', 'Python receipts cannot cross the runtime cutover; prepare a new preview.', {}, common_1.EXIT.conflict);
    const { receipt_digest, ...base } = receipt;
    (0, common_1.check)((0, common_1.canonicalDigest)(base) === receipt_digest, 'receipt_invalid', 'Frozen receipt digest differs.', {}, common_1.EXIT.conflict);
    const age = Date.now() - Date.parse(receipt.created_at);
    (0, common_1.check)(Number.isFinite(age) && age >= -60000 && age <= 86400000, 'receipt_expired', 'Receipt expired; create a new preview.', {}, common_1.EXIT.conflict);
    return { receipt, file, inode: s.ino, device: s.dev, sha256: (0, common_1.sha256)(raw) };
}
async function applyReceipt(bobbin, filename, digest, authorization, keep = false) {
    const loaded = readReceipt(bobbin, filename);
    (0, common_1.check)(digest === loaded.receipt.preview.approval_digest, 'approval_digest_mismatch', 'Forward the unchanged digest returned by preview.', {}, common_1.EXIT.conflict);
    const result = await bobbin.apply(loaded.receipt.preview, authorization);
    if (!keep) {
        try {
            const s = fs.lstatSync(loaded.file);
            (0, common_1.check)(s.ino === loaded.inode && s.dev === loaded.device && (0, common_1.sha256)((0, filesystem_1.bytes)(path.dirname(loaded.file), path.basename(loaded.file))) === loaded.sha256, 'receipt_changed', 'Receipt identity or bytes changed.');
            fs.unlinkSync(loaded.file);
        }
        catch (error) {
            result.warnings = [...(result.warnings ?? []), 'receipt_cleanup_failed'];
            result.receipt_cleanup_error = String(error);
        }
    }
    return result;
}
function rejectReceipt(bobbin, filename) { const loaded = readReceipt(bobbin, filename); fs.unlinkSync(loaded.file); return { rejected: true, applied: false, receipt_file: loaded.file }; }
