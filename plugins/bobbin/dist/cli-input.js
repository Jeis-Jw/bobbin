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
exports.bodyItems = exports.loadBody = exports.list = void 0;
exports.loadJson = loadJson;
exports.loadFile = loadFile;
exports.inlineInputs = inlineInputs;
exports.captureArguments = captureArguments;
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
const common_1 = require("./common");
const filesystem_1 = require("./filesystem");
const owners_1 = require("./owners");
const decision_1 = require("./decision");
const snapshot_1 = require("./snapshot");
const list = (value) => value === undefined ? [] : Array.isArray(value) ? value : [value];
exports.list = list;
function loadJson(value) {
    (0, common_1.check)(typeof value === 'string', 'usage_invalid', 'Missing JSON input.');
    const text = value === '-' ? (0, filesystem_1.utf8)(fs.readFileSync(0)) : value.startsWith('@') ? loadFile(value.slice(1), 2 * 1024 * 1024) : value;
    (0, common_1.check)(Buffer.byteLength(text) <= 2 * 1024 * 1024, 'input_too_large', 'JSON input exceeds the byte budget.');
    return (0, common_1.strictJson)(text);
}
function loadFile(value, maximum = 8192) {
    const absolute = path.resolve(value), parent = path.dirname(absolute), raw = (0, filesystem_1.bytes)(parent, path.basename(absolute), maximum);
    (0, common_1.check)(raw, 'input_not_found', 'Input file does not exist.', { path: absolute }, common_1.EXIT.notFound);
    return (0, filesystem_1.utf8)(raw);
}
const loadBody = (value, maximum = 8192) => value.startsWith('@@') ? value.slice(1) : value.startsWith('@') ? loadFile(value.slice(1), maximum) : value;
exports.loadBody = loadBody;
const bodyItems = (value) => (0, exports.list)(value).flatMap((x) => (0, exports.loadBody)(x).split(/\r?\n/).map(v => v.replace(/^\s*-\s?/, '').trim()).filter(Boolean));
exports.bodyItems = bodyItems;
function snapshotBody(value) {
    if (value.startsWith('@') && !value.startsWith('@@')) {
        const stat = fs.lstatSync(path.resolve(value.slice(1)), { throwIfNoEntry: false });
        if (stat?.isFile())
            (0, common_1.check)(stat.size <= snapshot_1.SNAP_TRANSPORT_MAX_BYTES, 'input_too_large', `SNAP transport input is ${stat.size} bytes; maximum is ${snapshot_1.SNAP_TRANSPORT_MAX_BYTES} bytes.`, { actual_bytes: stat.size, max_bytes: snapshot_1.SNAP_TRANSPORT_MAX_BYTES });
    }
    const text = value === '-' ? (0, filesystem_1.utf8)(fs.readFileSync(0)) : (0, exports.loadBody)(value, snapshot_1.SNAP_TRANSPORT_MAX_BYTES);
    const actual_bytes = Buffer.byteLength(text);
    (0, common_1.check)(actual_bytes <= snapshot_1.SNAP_TRANSPORT_MAX_BYTES, 'input_too_large', `SNAP transport input is ${actual_bytes} bytes; maximum is ${snapshot_1.SNAP_TRANSPORT_MAX_BYTES} bytes.`, { actual_bytes, max_bytes: snapshot_1.SNAP_TRANSPORT_MAX_BYTES });
    return (0, snapshot_1.snapshotText)(text, 'SNAP content');
}
function snapshotItems(value) {
    return (0, exports.list)(value).flatMap((item) => {
        const text = snapshotBody(item);
        // The existing one-item-per-line syntax remains available; JSON arrays
        // carry multiline items without destroying Markdown indentation.
        let parsed;
        if (text.trimStart().startsWith('[')) {
            try {
                parsed = (0, common_1.strictJson)(text);
            }
            catch (error) {
                if (!(error instanceof common_1.BobbinError))
                    throw error;
            }
        }
        return Array.isArray(parsed) ? (0, snapshot_1.snapshotList)(parsed, 'SNAP items') : text.split('\n').map(line => line.replace(/^\s*-\s?/, '').trim()).filter(Boolean);
    });
}
const mappings = {
    snapshot: { 'sec-context': 'current_context', 'sec-open-items': 'open_items', 'sec-next-steps': 'next_steps', 'sec-decided': 'decided', 'sec-refs': 'refs', 'sec-capture-candidates': 'capture_candidates', 'sec-candidates': 'capture_candidates', 'anchor': 'anchors' },
    observation: { 'sec-observation': 'observation', 'sec-evidence': 'evidence', 'sec-impact': 'impact', 'sec-handling': 'current_handling', 'sec-followup': 'followup_conditions' }, archive: { 'sec-content': 'content', content: 'content' },
    decision: { 'sec-decision': 'decision', 'sec-rationale': 'rationale', 'sec-alternatives': 'rejected_alternatives', 'decision-key': 'decision_key', 'sec-constraints': 'constraints', 'sec-tradeoffs': 'tradeoffs', 'sec-revisit': 'revisit_when', 'revisit-on': 'revisit_on', 'serves-intent': 'serves_intents', 'informed-by-observation': 'informed_by_observations', 'informed-by-assumption': 'informed_by_assumptions', 'affects-document': 'affects_documents' },
    assumption: { 'sec-assumption': 'assumption', 'sec-basis': 'basis', 'sec-confirm': 'confirm_conditions', 'sec-refute': 'refute_conditions', 'impacted-decision': 'impacted_decisions' },
    term: { term: 'term', 'sec-definition': 'definition', 'project-signal': 'project_signal', alias: 'aliases', 'deprecated-term': 'deprecated_terms', 'related-term': 'related' },
    intent: { 'sec-intent': 'intent', 'intent-key': 'intent_key', 'sec-success-criterion': 'success_criteria', 'sec-constraint': 'constraints', 'sec-revisit': 'revisit_conditions' }, document: { 'document-key': 'document_key', 'sec-content': 'content' }
};
const listFields = new Set('open_items next_steps decided refs capture_candidates anchors evidence followup_conditions rejected_alternatives constraints tradeoffs revisit_when serves_intents informed_by_observations informed_by_assumptions affects_documents basis confirm_conditions refute_conditions impacted_decisions aliases deprecated_terms related success_criteria revisit_conditions'.split(' '));
function inlineInputs(kind, flags) {
    const values = {};
    for (const [flag, key] of Object.entries(mappings[kind]))
        if (flags[flag] !== undefined)
            values[key] = kind === 'snapshot' && key !== 'anchors' ? listFields.has(key) ? snapshotItems(flags[flag]) : snapshotBody(flags[flag]) : listFields.has(key) ? (0, exports.bodyItems)(flags[flag]) : (0, exports.loadBody)(flags[flag], kind === 'archive' ? 260000 : 8192);
    if (kind === 'assumption')
        values.unverified_ok = flags['attest-unverified-ok'] === true;
    if (kind === 'term')
        values.project_signal ??= 'project-special-meaning';
    return values;
}
function captureArguments(kind, flags, prepareOnly = false) {
    if (flags.candidate) {
        (0, common_1.check)(!flags.inline, 'usage_invalid', 'Candidate file and inline flags cannot be mixed.');
        return { candidate: loadJson(flags.candidate), attestation: loadJson(flags.attestation) };
    }
    const values = inlineInputs(kind, flags), searchTerms = (0, exports.bodyItems)(flags['search-term']);
    if (kind === 'decision' && !searchTerms.length)
        searchTerms.push(...(0, decision_1.deriveSearchTerms)(flags.title ?? '', flags.summary ?? '', values));
    const candidate = (0, owners_1.createCandidate)({ kind, title: flags.title, summary: flags.summary, ownerInputs: values, scope: flags.scope, evidence: (0, exports.bodyItems)(flags['commitment-evidence'] ?? flags['evidence']), capturedFrom: flags['captured-from'] ?? 'conversation', sourceRefs: (0, exports.bodyItems)(flags['source-ref']), tags: (0, exports.bodyItems)(flags.tag), searchTerms, kindHint: flags['kind-hint'], candidateId: flags['candidate-id'] });
    if (prepareOnly)
        return { candidate, attestation: undefined };
    if (flags.attestation)
        return { candidate, attestation: loadJson(flags.attestation) };
    const aliases = { source_adopted_as_evidence: 'attest-source-adopted', immutable_original_present: 'attest-immutable-original' };
    const assertions = Object.entries(owners_1.assertionPointers[kind]).map(([name, evidence_pointers]) => { const flag = aliases[name] ?? 'attest-' + name.replaceAll('_', '-'); (0, common_1.check)(flags[flag] === true, 'usage_invalid', `Explicit semantic assertion --${flag} is required.`); return { name, value: true, evidence_pointers }; });
    // Non-decision owners historically carry no commitment-evidence CLI field; the
    // owner's explicit assertion is the user's statement, not an inferred fact.
    return { candidate, attestation: (0, owners_1.createAttestation)(candidate, assertions) };
}
